"""Scoring engine: 4 หมวด × 5 เกณฑ์ × 0/1/2, N/A rescale เป็น 10, red-flag cap, level & action

ส่วนที่ปรับปรุงจากเอกสารออกแบบ (ระบุไว้ใน README):
  - เกณฑ์ที่ใช้ field ความมั่นใจต่ำ (ยังไม่ verify) ไม่ถูกหักคะแนน แต่ส่งเข้า "รอผู้ตรวจยืนยัน"
  - rule ระดับแดงที่ยังเหลือ ห้าม auto-unlock แม้คะแนนรวม ≥8.5 (policy.red_blocks_auto_unlock)
  - near-miss: เกือบดีแต่ติดแดง 1 ข้อ แยกจากเคสที่ขาดหลายข้อ
  - คำนวณ "คะแนนที่จะได้เพิ่ม" ของแต่ละ rule เพื่อเรียงลำดับความสำคัญในรายการแก้
"""
from .ctx import Ctx
from .rules import run_rules

DOMAINS = ["Indication", "Strategy", "Technical", "Insurance"]
DOMAIN_TH = {"Indication": "1. Right indication & documentation", "Strategy": "2. Right strategy",
             "Technical": "3. Good technical PCI documentation", "Insurance": "4. Good insurance/audit documentation"}
CRITERIA = [
    ("1.1", "Indication", "Syndrome ชัดเจนและตรงกัน"), ("1.2", "Indication", "Ischemia / physiology"),
    ("1.3", "Indication", "ข้อมูลก่อนทำหัตถการ (pre-procedure data)"), ("1.4", "Indication", "Angiographic definitions"),
    ("1.5", "Indication", "Diagnosis ตรงกับ findings"),
    ("2.1", "Strategy", "Heart Team"), ("2.2", "Strategy", "SYNTAX / STS ถูกต้อง"),
    ("2.3", "Strategy", "Patient decision / consent"), ("2.4", "Strategy", "Ad hoc หรือ staged + target rationale"),
    ("2.5", "Strategy", "Residual/staged plan + antithrombotic plan"),
    ("3.1", "Technical", "อุปกรณ์ตรงกัน 3 แหล่ง (narrative/log/sticker)"), ("3.2", "Technical", "Sizing / prep justification"),
    ("3.3", "Technical", "Imaging criteria"), ("3.4", "Technical", "Final result / complication"),
    ("3.5", "Technical", "QC metrics + hemodynamic events"),
    ("4.1", "Insurance", "Principal diagnosis"), ("4.2", "Insurance", "Procedure codes ครบ"),
    ("4.3", "Insurance", "Dates ตรงกัน"), ("4.4", "Insurance", "Sticker ครบ"),
    ("4.5", "Insurance", "Narrative ไม่ขัดแย้งเอง และเป็น operator's note"),
]
DISCLAIMER = ("เกณฑ์นี้เป็นเกณฑ์ audit ภายในที่ยังไม่ผ่านการ validate — ประเมินคุณภาพ 'เอกสาร' "
              "ไม่ได้ตัดสินว่าการรักษาถูกหรือผิด การตัดสินใจทางคลินิกเป็นของแพทย์และ peer review")


def _applicable(ctx):
    t, syn = set(ctx.case_types), ctx.syndrome()
    treated = bool(ctx.treated_lesions())
    imaging = any(l.has("imaging_used") and str(l.v("imaging_used")).lower() not in ("none", "no", "ไม่ใช้") for l in ctx.lesions)
    lmmvd = "LM/MVD" in t
    return {
        "1.2": syn in ("CCS", "NSTEMI", "UA", "ACS") or any(l.is_lm for l in ctx.treated_lesions()),
        "2.1": (lmmvd or "CTO" in t) and syn != "STEMI",
        "2.2": lmmvd, "2.3": lmmvd, "2.4": lmmvd or "CTO" in t,
        "3.1": bool(ctx.devices) or treated, "3.2": treated,
        "3.3": imaging or "DCB" in t or "Atherectomy" in t or any(l.is_lm for l in ctx.treated_lesions()),
        "3.4": treated, "4.4": bool(ctx.devices) or treated,
    }


def _classify(ctx, findings, overrides):
    th = ctx.cfg["low_confidence"]
    for f in findings:
        o = overrides.get(f["rule_id"])
        if o:
            f["overridden"], f["override"] = True, o
        low = [e for e in f["evidence"] if e["status"] == "extracted" and not e["verified_by"]
               and e["confidence"] is not None and e["confidence"] < th]
        if low and not f["needs_review"]:
            f["needs_review"] = True
            f["detail"] = dict(f.get("detail") or {}, low_confidence=[e["key"] for e in low])
    return findings


def _compute(ctx, findings):
    pol = ctx.ruleset["policy"]
    active = [f for f in findings if not f["overridden"] and not f["needs_review"]]
    app = _applicable(ctx)
    crits = []
    for cid, dom, label in CRITERIA:
        hits = [f for f in active if cid in f["criteria"]]
        applicable = app.get(cid, True) or bool(hits)
        if not applicable:
            score = None
        elif any(f["severity"] == "red" for f in hits):
            score = 0
        elif any(f["severity"] == "yellow" or (f["severity"] == "green" and pol["green_deducts"]) for f in hits):
            score = 1
        else:
            score = 2
        crits.append({"id": cid, "domain": dom, "label": label, "score": score,
                      "rules": sorted({f["rule_id"] for f in hits})})
    domains = {}
    for d in DOMAINS:
        cs = [c for c in crits if c["domain"] == d and c["score"] is not None]
        raw = round(sum(c["score"] for c in cs) / (2 * len(cs)) * 10, 2) if cs else None
        red = sorted({f["rule_id"] for f in active if f["severity"] == "red" and f["domain"] == d})
        capped = min(raw, pol["red_cap"]) if raw is not None and red else raw
        domains[d] = {"label": DOMAIN_TH[d], "raw": raw, "score": capped, "red_rules": red, "evaluated": len(cs)}
    ev = [v for v in domains.values() if v["score"] is not None]
    overall = round(sum(v["score"] for v in ev) / len(ev), 2) if ev else None
    overall_raw = round(sum(v["raw"] for v in ev) / len(ev), 2) if ev else None
    return crits, domains, overall, overall_raw


def _level(ctx, overall, any_red):
    pol = ctx.ruleset["policy"]
    if overall is None:
        return None
    for lv in pol["levels"]:
        if overall >= lv["min"]:
            action, note = lv["action"], ""
            if action == "unlock" and any_red and pol["red_blocks_auto_unlock"]:
                action, note = "addendum", "ยังมี rule ระดับแดงค้างอยู่ จึงไม่ปลดล็อกอัตโนมัติ"
            return {"level": lv["level"], "action": action, "label_th": lv["label_th"], "note": note}


def _kpi(ctx):
    syn = ctx.syndrome()
    imaging = [l for l in ctx.treated_lesions() if str(l.v("imaging_used") or "").lower() not in ("", "none", "no")]
    d2b = ctx.d2b()
    return {
        "lm_mvd": "LM/MVD" in ctx.case_types,
        "lm_mvd_heart_team": "LM/MVD" in ctx.case_types and ctx.has("decision.heart_team_date") and ctx.has("decision.heart_team_cvt"),
        "vasopressor_event": bool(ctx.lst("qc.vasoactive") or ctx.has("qc.mcs_type")),
        "vasopressor_no_reason": bool(ctx.lst("qc.vasoactive") or ctx.has("qc.mcs_type")) and not ctx.has("qc.vasoactive_reason"),
        "stemi": syn == "STEMI", "stemi_has_d2b": syn == "STEMI" and d2b is not None,
        "d2b_le_target": (d2b is not None and d2b <= ctx.cfg["d2b_target_min"]) if syn == "STEMI" and d2b is not None else None,
        "imaging_used": bool(imaging),
        "imaging_has_msa": bool(imaging) and all(l.has("post_msa") for l in imaging),
        "has_contrast_egfr": ctx.has("qc.contrast_ml") and ctx.has("patient.egfr"),
        "has_air_kerma": ctx.has("qc.air_kerma_gy"),
    }


def evaluate(case, overrides=None, ruleset=None, lookups=None):
    overrides = overrides or {}
    ctx = Ctx(case, ruleset, lookups)
    findings = _classify(ctx, run_rules(ctx), overrides)
    crits, domains, overall, overall_raw = _compute(ctx, findings)
    active = [f for f in findings if not f["overridden"] and not f["needs_review"]]
    red_ids = sorted({f["rule_id"].split("@")[0] for f in active if f["severity"] == "red"})
    lvl = _level(ctx, overall, bool(red_ids)) or {}

    # คะแนนที่จะได้เพิ่มถ้าแก้ rule นั้น
    impact = {}
    for rid in {f["rule_id"] for f in active}:
        rest = [dict(f, overridden=True) if f["rule_id"] == rid else f for f in findings]
        _, _, ov2, _ = _compute(ctx, rest)
        impact[rid] = round((ov2 or 0) - (overall or 0), 2)
    for f in findings:
        f["impact"] = impact.get(f["rule_id"], 0.0)
    order = {"red": 0, "yellow": 1, "green": 2}
    findings.sort(key=lambda f: (order[f["severity"]], -f["impact"], f["rule_id"]))

    return {
        "ruleset": {k: ctx.ruleset.get(k) for k in ("version", "effective_date", "status")},
        "case_types": ctx.case_types, "findings": findings, "skipped": ctx.skipped,
        "criteria": crits, "domains": domains,
        "overall": overall, "overall_raw": overall_raw,
        "level": lvl.get("level"), "action": lvl.get("action"), "action_th": lvl.get("label_th"), "action_note": lvl.get("note"),
        "red_rules": red_ids, "red_count": len(red_ids),
        "near_miss": len(red_ids) == 1 and (overall_raw or 0) >= 8.5,
        "pending_review": sum(1 for f in findings if f["needs_review"] and not f["overridden"]),
        "d2b_min": ctx.d2b(), "kpi": _kpi(ctx), "disclaimer": DISCLAIMER,
    }
