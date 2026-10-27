"""เว็บเซิร์ฟเวอร์ (stdlib) + REST API + role-based access

รัน:  python -m pci_audit serve [--host 127.0.0.1] [--port 8080]
ผู้ใช้:  python -m pci_audit add-user <ชื่อ> <role>   (role: physician/auditor/coder/manager/admin)
ถ้ายังไม่มีผู้ใช้ จะเปิดโหมด dev (admin ไม่ต้องล็อกอิน) เฉพาะเมื่อผูกกับ 127.0.0.1 เท่านั้น
ใช้งานจริงต้องอยู่หลัง TLS reverse proxy (Basic auth ส่งรหัสผ่านแบบ base64)
"""
import argparse
import base64
import getpass
import json
import mimetypes
import os
import re
import sys
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from . import __version__, dashboard, extractor, phrases
from .ctx import load_lookups, load_ruleset
from .rules import rule_catalog
from .scoring import CRITERIA, DISCLAIMER
from .schema import CASE_FIELD_META, LESION_FIELD_META
from .service import MAX_UPLOAD, Service, ServiceError
from .store import ROLES, Store

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
DEV_USER = {"name": "dev", "display": "Dev (localhost)", "role": "admin"}
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; style-src-attr 'unsafe-inline'; img-src 'self' data: blob:; "
       "connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")

# role → สิทธิ์
CAN_WRITE = {"physician", "auditor", "admin"}
CAN_VIEW_ALL = {"auditor", "admin"}


def parse_multipart(content_type, body):
    msg = BytesParser(policy=policy.HTTP).parsebytes(b"Content-Type: " + content_type.encode() + b"\r\n\r\n" + body)
    fields, files = {}, {}
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        data = part.get_payload(decode=True) or b""
        if part.get_filename() is not None:
            files[name] = (part.get_filename(), data)
        else:
            fields[name] = data.decode("utf-8", "replace").strip()
    return fields, files


class App:
    def __init__(self, store, host="127.0.0.1"):
        self.store, self.svc, self.host = store, Service(store), host
        self.dev_mode = not store.has_users()
        if self.dev_mode and host not in ("127.0.0.1", "localhost", "::1"):
            raise SystemExit("ยังไม่มีผู้ใช้: โหมดไม่ล็อกอินใช้ได้เฉพาะ 127.0.0.1 — สร้างผู้ใช้ด้วย `python -m pci_audit add-user` ก่อน")

    def config(self):
        rs = load_ruleset()
        return {
            "version": __version__, "ruleset": {k: rs.get(k) for k in ("version", "effective_date", "owner", "status", "thresholds", "policy")},
            "rules": rule_catalog(), "criteria": [{"id": i, "domain": d, "label": l} for i, d, l in CRITERIA],
            "fields": [{"key": k, **m} for k, m in CASE_FIELD_META.items()],
            "lesion_fields": [{"key": k, **m} for k, m in LESION_FIELD_META.items()],
            "lookups": {k: load_lookups().get(k) for k in ("updated", "updated_by")},
            "model": extractor.DEFAULT_MODEL, "mock": bool(os.environ.get("PCI_AUDIT_MOCK")),
            "allow_image_upload": os.environ.get("PCI_AUDIT_ALLOW_IMAGE_UPLOAD") == "1",
            "local_ocr": bool(__import__("shutil").which("tesseract")), "disclaimer": DISCLAIMER,
            "dev_mode": self.dev_mode, "max_upload_mb": MAX_UPLOAD // 1048576,
        }


class Handler(BaseHTTPRequestHandler):
    app = None
    server_version = "PCIAudit/" + __version__

    def log_message(self, fmt, *a):
        sys.stderr.write("%s %s\n" % (self.command, self.path.split("?")[0]))

    # ---- helpers ----
    def send(self, status, body, ctype="application/json; charset=utf-8", headers=None):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def fail(self, status, msg):
        self.send(status, {"error": msg})

    def auth(self):
        if self.app.dev_mode:
            return DEV_USER
        h = self.headers.get("Authorization", "")
        if h.startswith("Basic "):
            try:
                name, _, pw = base64.b64decode(h[6:]).decode().partition(":")
                u = self.app.store.verify(name, pw)
                if u:
                    return u
            except Exception:
                pass
        self.send(401, {"error": "ต้องล็อกอิน"}, headers={"WWW-Authenticate": 'Basic realm="PCI Audit", charset="UTF-8"'})
        return None

    def body_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 20 * 1048576:
            raise ServiceError("ข้อมูลใหญ่เกินไป", 413)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            return json.loads(raw or b"{}")
        except json.JSONDecodeError:
            raise ServiceError("JSON ไม่ถูกต้อง")

    def csrf_ok(self):
        if self.headers.get("X-Requested-With") != "pci-audit":
            return False
        origin = self.headers.get("Origin")
        return not origin or urlparse(origin).netloc == self.headers.get("Host")

    def case_for(self, user, cid, write=False, coder_ok=False):
        c = self.app.store.get(cid)
        if not c:
            raise ServiceError("ไม่พบเคส", 404)
        role = user["role"]
        ok = role in CAN_VIEW_ALL or (role == "physician" and c["owner"] == user["name"]) or (coder_ok and role == "coder")
        if write and role not in CAN_WRITE:
            ok = False
        if not ok:
            raise ServiceError("ไม่มีสิทธิ์เข้าถึงเคสนี้", 403)
        return c

    # ---- dispatch ----
    def do_GET(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")

    def do_PUT(self):
        self.route("PUT")

    def do_DELETE(self):
        self.route("DELETE")

    def route(self, method):
        path = urlparse(self.path).path
        try:
            if method == "GET" and not path.startswith("/api/"):
                return self.static(path)
            user = self.auth()
            if not user:
                return
            if method != "GET" and not self.csrf_ok():
                return self.fail(403, "คำขอไม่ผ่านการตรวจ CSRF")
            self.api(method, path, user)
        except ServiceError as e:
            self.fail(e.status, str(e))
        except Exception as e:  # ไม่เปิดเผยรายละเอียดภายในให้ client
            sys.stderr.write(f"ERROR {method} {path}: {type(e).__name__}: {e}\n")
            self.fail(500, "เกิดข้อผิดพลาดภายในระบบ")

    def static(self, path):
        name = "index.html" if path in ("/", "") else path.lstrip("/").replace("static/", "", 1)
        full = os.path.realpath(os.path.join(STATIC, name))
        if not full.startswith(STATIC + os.sep) or not os.path.isfile(full):
            return self.fail(404, "ไม่พบหน้า")
        with open(full, "rb") as f:
            self.send(200, f.read(), (mimetypes.guess_type(full)[0] or "text/plain") + "; charset=utf-8")

    def api(self, method, path, user):
        a, role, parts = self.app, user["role"], path.strip("/").split("/")[1:]
        if parts == ["me"]:
            return self.send(200, user)
        if parts == ["config"]:
            return self.send(200, a.config())
        if parts == ["dashboard"] and method == "GET":
            if role not in ("manager", "admin", "auditor"):
                raise ServiceError("ไม่มีสิทธิ์ดู dashboard", 403)
            return self.send(200, dashboard.build(a.store.all_evaluations(), a.store.first_last_scores(), a.store.score_by_id))
        if parts == ["cases"]:
            if method == "GET":
                if role == "manager":
                    raise ServiceError("ผู้บริหารดูได้เฉพาะ dashboard", 403)
                owner = user["name"] if role == "physician" else None
                return self.send(200, a.store.list(owner))
            if method == "POST":
                if role not in CAN_WRITE:
                    raise ServiceError("ไม่มีสิทธิ์สร้างเคส", 403)
                ctype = self.headers.get("Content-Type", "")
                n = int(self.headers.get("Content-Length") or 0)
                if n > MAX_UPLOAD + 65536:
                    raise ServiceError("ไฟล์ใหญ่เกินกำหนด", 413)
                fields, files = parse_multipart(ctype, self.rfile.read(n))
                if "file" not in files or not files["file"][1]:
                    raise ServiceError("ไม่พบไฟล์")
                known = [x for x in re.split(r"[;\n,]", fields.get("known_names", "")) if x.strip()]
                cid = a.svc.upload(user, files["file"][0], files["file"][1], fields.get("title", ""), known)
                return self.send(201, {"id": cid})
        if parts == ["cases", "import"] and method == "POST":
            if role not in CAN_WRITE:
                raise ServiceError("ไม่มีสิทธิ์", 403)
            d = self.body_json()
            return self.send(201, {"id": a.svc.import_case(user, d.get("title") or "นำเข้าด้วยมือ", d.get("case") or {})})
        if parts == ["cases", "sample"] and method == "POST":
            if role not in CAN_WRITE:
                raise ServiceError("ไม่มีสิทธิ์", 403)
            with open(os.path.join(os.path.dirname(__file__), "samples", "sample_case.json"), encoding="utf-8") as f:
                case = json.load(f)
            return self.send(201, {"id": a.svc.import_case(user, "เคสตัวอย่าง (สังเคราะห์)", case)})

        if len(parts) >= 2 and parts[0] == "cases" and parts[1].isdigit():
            cid, sub = int(parts[1]), parts[2:]
            if not sub and method == "GET":
                self.case_for(user, cid, coder_ok=True)
                return self.send(200, a.svc.report(cid, role))
            if not sub and method == "DELETE":
                c = self.case_for(user, cid, write=True)
                if role != "admin" and c["owner"] != user["name"]:
                    raise ServiceError("ลบได้เฉพาะเจ้าของเคสหรือ admin", 403)
                a.store.log(user["name"], cid, "delete", {})
                a.store.delete_case(cid)
                return self.send(200, {"ok": True})
            if sub == ["audit"] and method == "GET":
                self.case_for(user, cid)
                return self.send(200, a.store.audit_for(cid))
            if sub[:1] == ["image"] and method == "GET" and len(sub) == 2:
                self.case_for(user, cid)
                img = a.store.page_images(cid).get(int(sub[1]) if sub[1].isdigit() else -1)
                if not img:
                    raise ServiceError("ไม่พบภาพ", 404)
                a.store.log(user["name"], cid, "view_image", {"page": sub[1]})
                return self.send(200, img, "image/jpeg")
            if sub == ["claim"] and method == "POST":
                self.case_for(user, cid, coder_ok=True)
                if role not in ("coder", "admin", "auditor"):
                    raise ServiceError("ไม่มีสิทธิ์บันทึกผลการเบิก", 403)
                st = self.body_json().get("status")
                if st not in ("pending", "approved", "denied"):
                    raise ServiceError("status ไม่ถูกต้อง")
                a.store.save_case(cid, claim_status=st)
                a.store.log(user["name"], cid, "claim_status", {"status": st})
                return self.send(200, {"ok": True})
            if method in ("POST", "PUT"):
                self.case_for(user, cid, write=True)
                d = self.body_json()
                if sub == ["extract"]:
                    ev = a.svc.extract(user, cid, bool(d.get("confirm_text")), bool(d.get("confirm_images")))
                    return self.send(200, a.svc.report(cid, role))
                if sub == ["rescore"]:
                    a.svc.rescore(user, cid, "rescore")
                    return self.send(200, a.svc.report(cid, role))
                if sub == ["field"]:
                    a.svc.set_field(user, cid, d.get("scope", "case"), d.get("key", ""), d.get("value"),
                                    d.get("lesion_id"), d.get("verify", True), bool(d.get("na")))
                    return self.send(200, a.svc.report(cid, role))
                if sub == ["verify_all"]:
                    n, _ = a.svc.verify_fields(user, cid, float(d.get("min_conf", 0.9)))
                    return self.send(200, {"verified": n, "report": a.svc.report(cid, role)})
                if sub == ["lesions"]:
                    a.svc.edit_lesions(user, cid, d.get("action"), d.get("id"))
                    return self.send(200, a.svc.report(cid, role))
                if sub == ["devices"]:
                    devs = d.get("devices")
                    if not isinstance(devs, list):
                        raise ServiceError("devices ต้องเป็น list")
                    a.svc.set_devices(user, cid, devs)
                    return self.send(200, a.svc.report(cid, role))
                if sub == ["override"]:
                    if role not in ("physician", "admin"):
                        raise ServiceError("เฉพาะแพทย์ผู้ทำหัตถการ (หรือ admin) ที่ override rule ได้", 403)
                    a.svc.override(user, cid, d.get("rule_id", ""), d.get("reason", ""), bool(d.get("remove")))
                    return self.send(200, a.svc.report(cid, role))
        raise ServiceError("ไม่พบ endpoint", 404)


def serve(host, port, store=None):
    store = store or Store()
    purged = store.purge_expired()
    app = App(store, host)
    Handler.app = app
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"PCI Audit & QC {__version__} — http://{host}:{port}  "
          f"(โหมด {'dev ไม่ล็อกอิน' if app.dev_mode else 'ล็อกอิน'}; ข้อมูลที่ {store.root}; ลบไฟล์หมดอายุ {purged} เคส)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nหยุดเซิร์ฟเวอร์")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m pci_audit", description="PCI Audit & QC — AI Assisted Clinical Review")
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("serve", help="เปิดเว็บแอป")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8080)
    u = sub.add_parser("add-user", help="สร้าง/แก้ผู้ใช้")
    u.add_argument("name")
    u.add_argument("role", choices=ROLES)
    u.add_argument("--display", default="")
    sub.add_parser("purge", help="ลบไฟล์สแกนที่เกินระยะเก็บ (PCI_AUDIT_RETENTION_DAYS, ค่าเริ่มต้น 90 วัน)")
    args = ap.parse_args(argv)
    if args.cmd == "add-user":
        pw = getpass.getpass("รหัสผ่าน: ")
        if len(pw) < 10:
            raise SystemExit("รหัสผ่านต้องยาวอย่างน้อย 10 ตัวอักษร")
        Store().add_user(args.name, pw, args.role, args.display)
        print("บันทึกผู้ใช้แล้ว")
    elif args.cmd == "purge":
        print(f"ลบไฟล์ของ {Store().purge_expired()} เคส")
    else:
        a = args if args.cmd == "serve" else ap.parse_args(["serve"])
        serve(a.host, a.port)


if __name__ == "__main__":
    main()
