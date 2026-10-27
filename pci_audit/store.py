"""เก็บข้อมูลใน SQLite (ในเครื่อง/เซิร์ฟเวอร์โรงพยาบาล): เคส, ผู้ใช้, audit trail, mapping de-id, ประวัติคะแนน

mapping HN/ชื่อ ↔ token เก็บในตาราง deid_map ของฐานข้อมูลนี้เท่านั้น ไม่ถูกส่งออกไป Cloud API
"""
import hashlib
import hmac
import json
import os
import secrets
import shutil
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

ROLES = ("physician", "auditor", "coder", "manager", "admin")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(name TEXT PRIMARY KEY, display TEXT, role TEXT, salt TEXT, pw TEXT);
CREATE TABLE IF NOT EXISTS cases(
  id INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT, title TEXT, created TEXT, updated TEXT, status TEXT,
  source_name TEXT, kind TEXT, ocr INTEGER DEFAULT 0, pages_json TEXT, case_json TEXT, evaluation_json TEXT,
  overrides_json TEXT DEFAULT '{}', claim_status TEXT DEFAULT 'pending', proc_date TEXT, residual_phi TEXT DEFAULT '[]');
CREATE TABLE IF NOT EXISTS deid_map(case_id INTEGER, token TEXT, original TEXT);
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, user TEXT, case_id INTEGER, action TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS scores(id INTEGER PRIMARY KEY AUTOINCREMENT, case_id INTEGER, ts TEXT, label TEXT,
  overall REAL, overall_raw REAL, level TEXT, red_count INTEGER, user TEXT);
CREATE INDEX IF NOT EXISTS idx_audit_case ON audit(case_id);
CREATE INDEX IF NOT EXISTS idx_scores_case ON scores(case_id);
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def hash_pw(pw, salt=None):
    salt = salt or secrets.token_hex(16)
    return salt, hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 200_000).hex()


class Store:
    def __init__(self, root=None):
        self.root = root or os.environ.get("PCI_AUDIT_DIR") or os.path.expanduser("~/.pci_audit")
        os.makedirs(os.path.join(self.root, "files"), exist_ok=True)
        self.db = os.path.join(self.root, "pci_audit.db")
        self._lock = threading.Lock()
        with self._c() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _c(self):
        c = sqlite3.connect(self.db, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        try:
            with c:  # commit/rollback
                yield c
        finally:
            c.close()

    def _exec(self, sql, args=()):
        with self._lock, self._c() as c:
            cur = c.execute(sql, args)
            return cur.lastrowid

    def _all(self, sql, args=()):
        with self._c() as c:
            return [dict(r) for r in c.execute(sql, args).fetchall()]

    # ---- users ----
    def add_user(self, name, password, role, display=""):
        if role not in ROLES:
            raise ValueError(f"role ต้องเป็นหนึ่งใน {ROLES}")
        salt, pw = hash_pw(password)
        self._exec("INSERT OR REPLACE INTO users VALUES(?,?,?,?,?)", (name, display or name, role, salt, pw))

    def has_users(self):
        return bool(self._all("SELECT 1 FROM users LIMIT 1"))

    def verify(self, name, password):
        rows = self._all("SELECT * FROM users WHERE name=?", (name,))
        if not rows:
            hash_pw(password)  # กัน timing ระหว่างผู้ใช้ที่มี/ไม่มี
            return None
        u = rows[0]
        _, h = hash_pw(password, u["salt"])
        return {"name": u["name"], "display": u["display"], "role": u["role"]} if hmac.compare_digest(h, u["pw"]) else None

    def list_users(self):
        return self._all("SELECT name, display, role FROM users ORDER BY name")

    # ---- cases ----
    def create_case(self, owner, title, source_name, kind, ocr, pages, original, images):
        cid = self._exec(
            "INSERT INTO cases(owner,title,created,updated,status,source_name,kind,ocr,pages_json) VALUES(?,?,?,?,?,?,?,?,?)",
            (owner, title, now(), now(), "uploaded", source_name, kind, int(ocr), json.dumps(pages, ensure_ascii=False)))
        d = os.path.join(self.root, "files", str(cid))
        os.makedirs(d, exist_ok=True)
        if original:
            with open(os.path.join(d, "original" + os.path.splitext(source_name or "")[1].lower()), "wb") as f:
                f.write(original)
        for n, img in images.items():
            with open(os.path.join(d, f"p{n}.jpg"), "wb") as f:
                f.write(img)
        return cid

    def import_case(self, owner, title, case):
        cid = self._exec("INSERT INTO cases(owner,title,created,updated,status,source_name,kind,pages_json,case_json) VALUES(?,?,?,?,?,?,?,?,?)",
                         (owner, title, now(), now(), "extracted", "manual-import", "manual", "[]", json.dumps(case, ensure_ascii=False)))
        return cid

    def get(self, cid):
        rows = self._all("SELECT * FROM cases WHERE id=?", (cid,))
        if not rows:
            return None
        r = rows[0]
        for k in ("pages_json", "case_json", "evaluation_json", "overrides_json", "residual_phi"):
            r[k[:-5] if k.endswith("_json") else k] = json.loads(r[k]) if r[k] else None
        return r

    def list(self, owner=None):
        sql = ("SELECT id, owner, title, created, updated, status, source_name, kind, proc_date, claim_status, evaluation_json FROM cases")
        rows = self._all(sql + (" WHERE owner=?" if owner else "") + " ORDER BY id DESC", (owner,) if owner else ())
        out = []
        for r in rows:
            ev = json.loads(r.pop("evaluation_json") or "null")
            r["overall"] = ev and ev["overall"]
            r["level"] = ev and ev["level"]
            r["action"] = ev and ev["action"]
            r["red_count"] = ev and ev["red_count"]
            out.append(r)
        return out

    def all_evaluations(self):
        rows = self._all("SELECT id, owner, created, proc_date, claim_status, evaluation_json FROM cases WHERE evaluation_json IS NOT NULL")
        for r in rows:
            r["evaluation"] = json.loads(r.pop("evaluation_json"))
        return rows

    def save_case(self, cid, case=None, evaluation=None, status=None, overrides=None, proc_date=None,
                  residual_phi=None, claim_status=None):
        sets, args = ["updated=?"], [now()]
        for col, val in (("case_json", case), ("evaluation_json", evaluation), ("overrides_json", overrides), ("residual_phi", residual_phi)):
            if val is not None:
                sets.append(f"{col}=?")
                args.append(json.dumps(val, ensure_ascii=False, default=str))
        for col, val in (("status", status), ("proc_date", proc_date), ("claim_status", claim_status)):
            if val is not None:
                sets.append(f"{col}=?")
                args.append(val)
        self._exec(f"UPDATE cases SET {', '.join(sets)} WHERE id=?", (*args, cid))

    def delete_case(self, cid):
        for t in ("cases", "deid_map", "scores"):
            self._exec(f"DELETE FROM {t} WHERE {'id' if t == 'cases' else 'case_id'}=?", (cid,))
        shutil.rmtree(os.path.join(self.root, "files", str(cid)), ignore_errors=True)

    def case_dir(self, cid):
        return os.path.join(self.root, "files", str(cid))

    def page_images(self, cid):
        d, out = self.case_dir(cid), {}
        if os.path.isdir(d):
            for fn in os.listdir(d):
                if fn.startswith("p") and fn.endswith(".jpg") and fn[1:-4].isdigit():
                    with open(os.path.join(d, fn), "rb") as f:
                        out[int(fn[1:-4])] = f.read()
        return out

    # ---- de-id mapping ----
    def save_mapping(self, cid, mapping):
        for tok, orig in mapping.items():
            self._exec("INSERT INTO deid_map VALUES(?,?,?)", (cid, tok, orig))

    # ---- audit / scores ----
    def log(self, user, cid, action, detail=None):
        self._exec("INSERT INTO audit(ts,user,case_id,action,detail) VALUES(?,?,?,?,?)",
                   (now(), user, cid, action, json.dumps(detail, ensure_ascii=False, default=str) if detail is not None else None))

    def audit_for(self, cid):
        return self._all("SELECT ts, user, action, detail FROM audit WHERE case_id=? ORDER BY id", (cid,))

    def record_score(self, cid, label, ev, user):
        self._exec("INSERT INTO scores(case_id,ts,label,overall,overall_raw,level,red_count,user) VALUES(?,?,?,?,?,?,?,?)",
                   (cid, now(), label, ev["overall"], ev["overall_raw"], ev["level"], ev["red_count"], user))

    def scores_for(self, cid):
        return self._all("SELECT ts, label, overall, overall_raw, level, red_count, user FROM scores WHERE case_id=? ORDER BY id", (cid,))

    def first_last_scores(self):
        return self._all("""SELECT case_id, MIN(id) first_id, MAX(id) last_id FROM scores GROUP BY case_id""")

    def score_by_id(self, sid):
        r = self._all("SELECT overall FROM scores WHERE id=?", (sid,))
        return r[0]["overall"] if r else None

    # ---- retention ----
    def purge_expired(self, days=None):
        """ลบไฟล์สแกน/ภาพ (คงข้อมูล structured ที่ de-identify แล้ว) ที่เกินกำหนด; คืนจำนวนเคส"""
        days = days if days is not None else int(os.environ.get("PCI_AUDIT_RETENTION_DAYS", "90"))
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
        n = 0
        for r in self._all("SELECT id FROM cases WHERE created < ?", (cutoff,)):
            d = self.case_dir(r["id"])
            if os.path.isdir(d):
                shutil.rmtree(d, ignore_errors=True)
                self._exec("DELETE FROM deid_map WHERE case_id=?", (r["id"],))
                self.log("system", r["id"], "purge_files", {"retention_days": days})
                n += 1
        return n
