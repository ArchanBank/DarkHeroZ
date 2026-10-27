"""Workflow 8 ขั้น: Ingest → De-identify → Extract → Verify → Classify → Evaluate → Report → Close loop"""
import os

from . import deid, extractor, phrases, util
from .extractor import ExtractionError
from .schema import CASE_FIELD_META, LESION_FIELD_META, normalize_case
from .scoring import evaluate

MAX_UPLOAD = 25 * 1024 * 1024


class ServiceError(Exception):
    def __init__(self, msg, status=400):
        super().__init__(msg)
        self.status = status


class Service:
    def __init__(self, store):
        self.store = store

    # 1–2. Ingest + De-identify
    def upload(self, user, filename, data, title="", known_names=()):
        if len(data) > MAX_UPLOAD:
            raise ServiceError("ไฟล์ใหญ่เกิน 25 MB", 413)
        try:
            doc = extractor.ingest(data, filename)
        except ExtractionError as e:
            raise ServiceError(str(e))
        d = deid.Deidentifier()
        pages = []
        for p in doc["pages"]:
            t = d.redact_known(p["text"], known_names) if known_names else p["text"]
            pages.append({"page": p["page"], "text": d.redact(t)})
        phi = sorted({k for p in pages for k in deid.has_residual_phi(p["text"])})
        images = {p["page"]: p["image"] for p in doc["pages"] if p["image"]}
        cid = self.store.create_case(user["name"], title or os.path.basename(filename), filename, doc["kind"],
                                     doc["ocr"], pages, data, images)
        self.store.save_mapping(cid, d.mapping)
        self.store.save_case(cid, residual_phi=phi)
        self.store.log(user["name"], cid, "upload", {"file": filename, "kind": doc["kind"], "pages": len(pages),
                                                      "ocr_local": doc["ocr"], "redactions": len(d.mapping)})
        return cid

    # 3. Extract (ส่งข้อมูลออก Cloud API — ต้องผู้ใช้ยืนยัน)
    def extract(self, user, cid, confirm_text=False, confirm_images=False, client=None):
        c = self.store.get(cid)
        if not c:
            raise ServiceError("ไม่พบเคส", 404)
        if not confirm_text:
            raise ServiceError("ต้องยืนยันว่าตรวจ preview ข้อความที่ de-identify แล้ว ก่อนส่งประมวลผล")
        has_text = any(p["text"].strip() for p in c["pages"])
        mode = "text" if has_text else "image"
        if mode == "image":
            if os.environ.get("PCI_AUDIT_ALLOW_IMAGE_UPLOAD") != "1":
                raise ServiceError("เซิร์ฟเวอร์ไม่อนุญาตให้ส่งภาพไป Cloud API (ตั้ง PCI_AUDIT_ALLOW_IMAGE_UPLOAD=1 หลังมี DPA) "
                                   "หรือติดตั้ง tesseract เพื่อ OCR ในเครื่อง", 403)
            if not confirm_images:
                raise ServiceError("ภาพสแกนจะถูกส่งไป Cloud API โดยซ่อนชื่อ/HN ในภาพไม่ได้ — ต้องติ๊กยืนยันเพิ่ม")
        images = self.store.page_images(cid)
        raw_pages = [{"page": p["page"], "text": p["text"], "image": images.get(p["page"])} for p in c["pages"]]
        self.store.log(user["name"], cid, "extract_start", {"mode": mode, "model": extractor.DEFAULT_MODEL})
        try:
            case = extractor.extract_case(raw_pages, c["pages"], mode, client=client,
                                          low_factor=self._cfg()["span_missing_confidence_factor"])
        except ExtractionError as e:
            self.store.log(user["name"], cid, "extract_failed", {"error": str(e)})
            raise ServiceError(str(e), 502)
        self.store.save_case(cid, case=case, status="extracted")
        self.store.log(user["name"], cid, "extract_done", {"usage": case.get("usage"), "fields": sum(1 for f in case["fields"].values() if f["status"] != "not_found")})
        return self.rescore(user, cid, "initial")

    def _cfg(self):
        from .ctx import load_ruleset
        return load_ruleset()["thresholds"]

    # 5–6. Classify + Evaluate
    def rescore(self, user, cid, label="rescore"):
        c = self.store.get(cid)
        if not c or not c["case"]:
            raise ServiceError("เคสยังไม่มีข้อมูล", 409)
        ev = evaluate(c["case"], c["overrides"] or {})
        pd = util.parse_date(c["case"]["fields"].get("clinical.procedure_date", {}).get("value"))
        self.store.save_case(cid, evaluation=ev, proc_date=pd.isoformat() if pd else None)
        last = self.store.scores_for(cid)
        if not last or (last[-1]["overall"], last[-1]["red_count"]) != (ev["overall"], ev["red_count"]) or label == "initial":
            self.store.record_score(cid, label, ev, user["name"])
        return ev

    # 4. Verify / แก้ field
    def set_field(self, user, cid, scope, key, value=None, lesion_id=None, verify=True, na=False):
        c = self.store.get(cid)
        if not c or not c["case"]:
            raise ServiceError("ไม่พบเคส/ข้อมูล", 404)
        case = normalize_case(c["case"])
        if scope == "lesion":
            les = next((l for l in case["lesions"] if l["id"] == lesion_id), None)
            if not les or key not in LESION_FIELD_META:
                raise ServiceError("ไม่พบ lesion/field")
            f, meta = les["fields"][key], LESION_FIELD_META[key]
        else:
            if key not in CASE_FIELD_META:
                raise ServiceError("ไม่รู้จัก field")
            f, meta = case["fields"][key], CASE_FIELD_META[key]
        old = f["value"]
        if na:
            f.update(value=None, status="na", verified_by=user["name"])
        elif value is None or str(value).strip() == "":
            f.update(value=None, status="not_found", verified_by=user["name"], span="", page=None, confidence=None)
        else:
            new = extractor._coerce(str(value), meta["type"])
            if new != old:
                f.update(value=new, status="manual", confidence=1.0, span=f.get("span") or "")
            if verify:
                f["verified_by"] = user["name"]
        self.store.save_case(cid, case=case)
        self.store.log(user["name"], cid, "field_edit", {"scope": scope, "lesion": lesion_id, "key": key, "old": old, "new": f["value"], "status": f["status"]})
        return self.rescore(user, cid)

    def verify_fields(self, user, cid, min_conf=0.9):
        """ยืนยันทุก field ที่ extract แล้วและ confidence ≥ เกณฑ์ (ผู้ใช้เป็นคนกดรับรอง)"""
        c = self.store.get(cid)
        case, n = normalize_case(c["case"]), 0
        for f in list(case["fields"].values()) + [x for l in case["lesions"] for x in l["fields"].values()]:
            if f["status"] == "extracted" and not f["verified_by"] and (f["confidence"] or 0) >= min_conf and f.get("grounded") is not False:
                f["verified_by"] = user["name"]
                n += 1
        self.store.save_case(cid, case=case, status="verified")
        self.store.log(user["name"], cid, "verify_bulk", {"min_conf": min_conf, "count": n})
        return n, self.rescore(user, cid)

    def edit_lesions(self, user, cid, action, lesion_id=None):
        c = self.store.get(cid)
        case = normalize_case(c["case"])
        if action == "add":
            ids = {l["id"] for l in case["lesions"]}
            i = 1
            while f"L{i}" in ids:
                i += 1
            case["lesions"].append({"id": f"L{i}", "fields": {}})
        elif action == "remove":
            case["lesions"] = [l for l in case["lesions"] if l["id"] != lesion_id]
        else:
            raise ServiceError("action ไม่ถูกต้อง")
        case = normalize_case(case)
        self.store.save_case(cid, case=case)
        self.store.log(user["name"], cid, "lesion_" + action, {"id": lesion_id})
        return self.rescore(user, cid)

    def set_devices(self, user, cid, devices):
        c = self.store.get(cid)
        case = normalize_case(c["case"])
        case["devices"] = devices
        self.store.save_case(cid, case=normalize_case(case))
        self.store.log(user["name"], cid, "devices_edit", {"count": len(devices)})
        return self.rescore(user, cid)

    # override ของแพทย์ (ต้องใส่เหตุผล และจะแสดงใน audit report)
    def override(self, user, cid, rule_id, reason, remove=False):
        c = self.store.get(cid)
        ov = c["overrides"] or {}
        if remove:
            ov.pop(rule_id, None)
        else:
            if not (reason or "").strip():
                raise ServiceError("ต้องระบุเหตุผลในการ override")
            ov[rule_id] = {"reason": reason.strip(), "user": user["name"]}
        self.store.save_case(cid, overrides=ov)
        self.store.log(user["name"], cid, "override_remove" if remove else "override", {"rule": rule_id, "reason": reason})
        return self.rescore(user, cid)

    def import_case(self, user, title, case):
        cid = self.store.import_case(user["name"], title, normalize_case(case))
        self.store.log(user["name"], cid, "import", {"title": title})
        self.rescore(user, cid, "initial")
        return cid

    # 7. Report
    def report(self, cid, role="auditor"):
        c = self.store.get(cid)
        if not c:
            return None
        out = {"id": cid, "title": c["title"], "owner": c["owner"], "status": c["status"], "kind": c["kind"],
               "source_name": c["source_name"], "created": c["created"], "claim_status": c["claim_status"],
               "residual_phi": c["residual_phi"], "ocr": bool(c["ocr"]), "overrides": c["overrides"]}
        if role == "coder":  # coder เห็นเฉพาะส่วน coding
            out["coding"] = phrases.coding_check(c["case"]) if c["case"] else None
            out["devices"] = (c["case"] or {}).get("devices", [])
            return out
        out.update(pages=c["pages"], case=c["case"], evaluation=c["evaluation"], scores=self.store.scores_for(cid))
        if c["case"] and c["evaluation"]:
            out["addendum"] = phrases.addendum(c["case"], c["evaluation"])
            out["coding"] = phrases.coding_check(c["case"])
        return out
