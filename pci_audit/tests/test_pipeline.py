import base64
import io
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import contextmanager
from types import SimpleNamespace

from pci_audit import deid, extractor, server
from pci_audit.service import Service, ServiceError
from pci_audit.store import Store


class TestDeid(unittest.TestCase):
    def test_redacts_patient_but_keeps_clinicians_and_procedure_dates(self):
        t = ("ชื่อ-สกุล: นายสมชาย ใจดี  HN: 1234567  DOB: 12/03/2501\nโทร 081-234-5678 บัตร ปชช 1-2345-67890-12-3\n"
             "Patient Name: John Smith\nแพทย์ผู้ทำหัตถการ นพ.วิชัย รักษา วันที่ทำหัตถการ 5 ต.ค. 2569 FFR 0.71")
        d = deid.Deidentifier()
        out = d.redact(t)
        for leak in ("สมชาย", "ใจดี", "1234567", "2501", "081-234-5678", "John Smith", "67890"):
            self.assertNotIn(leak, out)
        for keep in ("นพ.วิชัย", "5 ต.ค. 2569", "FFR 0.71"):
            self.assertIn(keep, out)
        self.assertEqual(deid.has_residual_phi(out), [])
        self.assertIn("สมชาย", d.restore(out))

    def test_same_value_same_token_and_known_names(self):
        d = deid.Deidentifier()
        out = d.redact_known("ผู้ป่วย Somchai มาถึง Somchai", ["Somchai"])
        self.assertEqual(out.count("[NAME_1]"), 2)


class FakeStream:
    def __init__(self, payload, stop="end_turn"):
        self.msg = SimpleNamespace(stop_reason=stop, model="fake", content=[SimpleNamespace(type="text", text=json.dumps(payload))],
                                   usage=SimpleNamespace(input_tokens=10, output_tokens=5), _request_id="req_1")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self.msg


class FakeClient:
    def __init__(self, payload, stop="end_turn"):
        self.calls = []
        mk = lambda **kw: (self.calls.append(kw), FakeStream(payload, stop))[1]
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=mk))
        self.messages = SimpleNamespace(stream=mk)


def payload():
    f = lambda k, v, span, conf=0.95, page=1: {"key": k, "value": v, "page": page, "span": span, "confidence": conf}
    return {"fields": [f("patient.sex", "ชาย", "เพศ ชาย"), f("clinical.procedure_date", "5 ต.ค. 2569", "วันที่ทำหัตถการ 5 ต.ค. 2569"),
                       f("echo.lvef", "55", "ข้อความที่ไม่มีอยู่จริง")],
            "lesions": [{"id": "L1", "fields": [f("stenosis_pct", "90%", "LAD 90%"), f("treated", "true", "treated LAD")]}],
            "devices": [{"slot": "S1", "source": "log", "category": "stent", "name": "X", "size": "3.0x18", "gtin": "", "ref": "", "lot": "", "expiry": "", "page": 1, "span": "stent X"}],
            "review_notes": []}


class TestExtractor(unittest.TestCase):
    PAGES = [{"page": 1, "text": "เพศ ชาย\nวันที่ทำหัตถการ 5 ต.ค. 2569\nLAD 90% treated LAD stent X"}]

    def test_to_case_grounds_spans_and_coerces(self):
        case = extractor.to_case(payload(), self.PAGES, True)
        f = case["fields"]
        self.assertEqual(f["clinical.procedure_date"]["value"], "2026-10-05")  # พ.ศ. → ค.ศ.
        self.assertTrue(f["patient.sex"]["grounded"])
        self.assertFalse(f["echo.lvef"]["grounded"])               # span ไม่อยู่ในต้นฉบับ
        self.assertAlmostEqual(f["echo.lvef"]["confidence"], 0.475)  # ลดครึ่งหนึ่ง
        self.assertEqual(case["lesions"][0]["fields"]["stenosis_pct"]["value"], 90.0)
        self.assertIs(case["lesions"][0]["fields"]["treated"]["value"], True)
        self.assertEqual(f["patient.age"]["status"], "not_found")   # ไม่เติมค่าเอง

    def test_schema_is_strict(self):
        s = extractor.output_schema()
        self.assertFalse(s["additionalProperties"])
        item = s["properties"]["fields"]["items"]
        self.assertEqual(set(item["required"]), set(item["properties"]))
        self.assertIn("patient.age", item["properties"]["key"]["enum"])

    def test_call_model_uses_structured_output_and_fallback(self):
        cl = FakeClient(payload())
        data = extractor.call_model([{"type": "text", "text": "x"}], client=cl)
        self.assertEqual(len(data["fields"]), 3)
        kw = cl.messages.stream.__closure__ and cl.calls[0]
        self.assertEqual(kw["output_config"]["format"]["type"], "json_schema")
        self.assertNotIn("tool_choice", kw)  # โมเดลรุ่นใหม่ห้าม forced tool_choice
        self.assertNotIn("temperature", kw)

    def test_refusal_and_truncation_raise(self):
        for stop in ("refusal", "max_tokens"):
            with self.assertRaises(extractor.ExtractionError):
                extractor.call_model([], client=FakeClient(payload(), stop))

    def test_ingest_text_pdf(self):
        doc = extractor.ingest(make_pdf("Hello cath lab " + "y" * 120), "r.pdf")
        self.assertEqual(doc["kind"], "pdf-text")
        self.assertIn("Hello cath lab", doc["pages"][0]["text"])

    def test_ingest_rejects_unknown(self):
        with self.assertRaises(extractor.ExtractionError):
            extractor.ingest(b"x", "a.exe")


def make_pdf(text):
    """PDF ขั้นต่ำที่มี text layer (เขียนด้วยมือ ไม่ต้องใช้ไลบรารีเพิ่ม)"""
    body = "BT /F1 12 Tf 50 700 Td (" + text.replace("(", "").replace(")", "") + ") Tj ET"
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            f"<< /Length {len(body)} >>\nstream\n{body}\nendstream", "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode()
    x = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offs)
    return out + f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{x}\n%%EOF".encode()


@contextmanager
def tmp_store():
    with tempfile.TemporaryDirectory() as d:
        yield Store(d)


class TestService(unittest.TestCase):
    USER = {"name": "dr1", "display": "Dr", "role": "physician"}

    def test_full_workflow_with_fake_cloud(self):
        text = "Name: John Smith HN: 7654321 sex ชาย. " + "x" * 120 + " LAD 90%"
        with tmp_store() as st:
            svc = Service(st)
            cid = svc.upload(self.USER, "r.pdf", make_pdf(text), "case A")
            c = st.get(cid)
            self.assertNotIn("7654321", json.dumps(c["pages"]))
            self.assertNotIn("John", json.dumps(c["pages"]))
            with self.assertRaises(ServiceError):  # ต้องยืนยัน preview ก่อน
                svc.extract(self.USER, cid, confirm_text=False, client=FakeClient(payload()))
            cl = FakeClient(payload())
            ev = svc.extract(self.USER, cid, confirm_text=True, client=cl)
            sent = json.dumps(cl.calls[0]["messages"], ensure_ascii=False)
            self.assertNotIn("7654321", sent)   # ไม่มี PHI ถูกส่งออก
            self.assertNotIn("John Smith", sent)
            self.assertIsNotNone(ev["overall"])
            ev2 = svc.set_field(self.USER, cid, "case", "patient.age", "62")
            self.assertEqual(st.get(cid)["case"]["fields"]["patient.age"]["verified_by"], "dr1")
            actions = [a["action"] for a in st.audit_for(cid)]
            self.assertIn("upload", actions)
            self.assertIn("extract_done", actions)
            self.assertIn("field_edit", actions)
            self.assertGreaterEqual(len(st.scores_for(cid)), 1)

    def test_image_without_ocr_needs_flag(self):
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (80, 80), "white").save(buf, "PNG")
        os.environ.pop("PCI_AUDIT_ALLOW_IMAGE_UPLOAD", None)
        with tmp_store() as st:
            svc = Service(st)
            cid = svc.upload(self.USER, "scan.png", buf.getvalue(), "scan")
            if not st.get(cid)["ocr"]:
                with self.assertRaises(ServiceError) as cm:
                    svc.extract(self.USER, cid, True, True, client=FakeClient(payload()))
                self.assertEqual(cm.exception.status, 403)

    def test_override_needs_reason(self):
        with tmp_store() as st:
            svc = Service(st)
            with open(os.path.join(os.path.dirname(__file__), "..", "samples", "sample_case.json"), encoding="utf-8") as fh:
                sample = json.load(fh)
            cid = svc.import_case(self.USER, "s", sample)
            with self.assertRaises(ServiceError):
                svc.override(self.USER, cid, "R01", "  ")
            before = st.get(cid)["evaluation"]["overall"]
            svc.override(self.USER, cid, "R01", "Heart Team documented in separate note")
            self.assertGreater(st.get(cid)["evaluation"]["overall"], before)
            self.assertEqual(len(st.scores_for(cid)), 2)  # close loop: ก่อน/หลัง


class Client:
    def __init__(self, base, user=None, pw=None):
        self.base = base
        self.auth = "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode() if user else None

    def req(self, method, path, body=None, csrf=True):
        data = json.dumps(body).encode() if body is not None else None
        r = urllib.request.Request(self.base + path, data=data, method=method)
        if data is not None:
            r.add_header("Content-Type", "application/json")
        if csrf and method != "GET":
            r.add_header("X-Requested-With", "pci-audit")
        if self.auth:
            r.add_header("Authorization", self.auth)
        try:
            with urllib.request.urlopen(r) as resp:
                return resp.status, json.loads(resp.read() or b"{}"), resp.headers
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"{}"), e.headers
            except Exception:
                return e.code, {}, e.headers


@contextmanager
def running(store, host="127.0.0.1"):
    app = server.App(store, host)
    server.Handler.app = app
    httpd = server.ThreadingHTTPServer((host, 0), server.Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://{host}:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()


class TestServer(unittest.TestCase):
    def test_roles_csrf_and_headers(self):
        with tmp_store() as st:
            for n, r in (("drA", "physician"), ("drB", "physician"), ("aud", "auditor"), ("cod", "coder"), ("mgr", "manager")):
                st.add_user(n, "password-12345", r)
            with running(st) as base:
                self.assertEqual(Client(base).req("GET", "/api/me")[0], 401)
                self.assertEqual(Client(base, "drA", "wrong").req("GET", "/api/me")[0], 401)
                a, b, aud, cod, mgr = (Client(base, n, "password-12345") for n in ("drA", "drB", "aud", "cod", "mgr"))
                s, r, h = a.req("POST", "/api/cases/sample", {})
                self.assertEqual(s, 201)
                cid = r["id"]
                self.assertEqual(h.get("X-Content-Type-Options"), "nosniff")
                self.assertEqual(a.req("POST", "/api/cases/sample", {}, csrf=False)[0], 403)   # CSRF
                self.assertEqual(b.req("GET", f"/api/cases/{cid}")[0], 403)                      # แพทย์อื่นห้ามดู
                self.assertEqual(aud.req("GET", f"/api/cases/{cid}")[0], 200)
                s, rep, _ = cod.req("GET", f"/api/cases/{cid}")
                self.assertEqual(s, 200)
                self.assertNotIn("case", rep)                                                    # coder เห็นเฉพาะ coding
                self.assertIn("coding", rep)
                self.assertEqual(mgr.req("GET", "/api/cases")[0], 403)
                self.assertEqual(mgr.req("GET", "/api/dashboard")[0], 200)
                self.assertEqual(aud.req("POST", f"/api/cases/{cid}/override", {"rule_id": "R01", "reason": "x"})[0], 403)
                self.assertEqual(a.req("POST", f"/api/cases/{cid}/override", {"rule_id": "R01", "reason": ""})[0], 400)
                s, rep, _ = a.req("POST", f"/api/cases/{cid}/override", {"rule_id": "R01", "reason": "HT note on file"})
                self.assertEqual(s, 200)
                self.assertEqual(rep["overrides"]["R01"]["user"], "drA")
                s, d, _ = mgr.req("GET", "/api/dashboard")
                self.assertEqual(d["overall"]["cases"], 1)
                self.assertTrue(d["physicians"][0]["alias"].startswith("แพทย์ "))

    def test_static_and_traversal(self):
        with tmp_store() as st, running(st) as base:
            for path, code in (("/", 200), ("/static/app.js", 200), ("/static/../server.py", 404), ("/%2e%2e/server.py", 404)):
                try:
                    status = urllib.request.urlopen(base + path).status
                except urllib.error.HTTPError as e:
                    status = e.code
                self.assertEqual(status, code, path)
            _, _, h = Client(base).req("GET", "/api/config")
            self.assertIn("default-src 'none'", h["Content-Security-Policy"])

    def test_dev_mode_refuses_public_bind(self):
        with tmp_store() as st:
            with self.assertRaises(SystemExit):
                server.App(st, "0.0.0.0")


if __name__ == "__main__":
    unittest.main()
