#!/usr/bin/env python3
"""เว็บแอปเก็บรวบรวมใบเสร็จ — ใช้ฐานข้อมูลเดียวกับ receipts.py (CLI)

รัน:  python webapp.py [--host 127.0.0.1] [--port 8000]
แล้วเปิด http://127.0.0.1:8000

หมายเหตุ: ไม่มีระบบล็อกอิน จึงผูกกับ 127.0.0.1 เป็นค่าเริ่มต้น
อย่าเปิดสู่อินเทอร์เน็ตโดยไม่มี reverse proxy ที่ยืนยันตัวตนก่อน
"""
import argparse
import csv
import io
import mimetypes
import os
from datetime import date, datetime
from email import policy
from email.parser import BytesParser
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

import receipts

MAX_UPLOAD = 20 * 1024 * 1024
ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".pdf"}

CSS = """
body{font-family:system-ui,sans-serif;max-width:960px;margin:0 auto;padding:16px;color:#222}
h1{margin-top:0} table{border-collapse:collapse;width:100%} th,td{padding:6px 8px;border-bottom:1px solid #ddd;text-align:left}
td.n,th.n{text-align:right} fieldset{border:1px solid #ccc;border-radius:6px;margin-bottom:16px}
label{display:inline-block;margin:4px 12px 4px 0} input,button{padding:5px;font:inherit}
.err{background:#fde8e8;color:#900;padding:8px;border-radius:6px;margin-bottom:12px}
.sum span{display:inline-block;margin-right:16px} form.inline{display:inline}
"""


def page(body, error=""):
    err = f'<div class="err">{escape(error)}</div>' if error else ""
    return (f'<!doctype html><html lang="th"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>ใบเสร็จ</title><style>{CSS}</style></head><body>'
            f'<h1>🧾 เก็บรวบรวมใบเสร็จ</h1>{err}{body}</body></html>').encode()


def parse_multipart(content_type, body):
    """แยกฟอร์ม multipart คืน (fields, files) ; files = {name: (filename, bytes)}"""
    msg = BytesParser(policy=policy.HTTP).parsebytes(
        b"Content-Type: " + content_type.encode() + b"\r\n\r\n" + body)
    fields, files = {}, {}
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        filename = part.get_filename()
        data = part.get_payload(decode=True) or b""
        if filename is not None:
            files[name] = (filename, data)
        else:
            fields[name] = data.decode("utf-8", "replace").strip()
    return fields, files


class Handler(BaseHTTPRequestHandler):
    def send(self, status, body, ctype="text/html; charset=utf-8", headers=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def redirect(self, to="/"):
        self.send_response(303)
        self.send_header("Location", to)
        self.end_headers()

    def same_origin(self):
        """กัน CSRF: ถ้ามี Origin ต้องตรงกับ Host"""
        origin = self.headers.get("Origin")
        return not origin or urlparse(origin).netloc == self.headers.get("Host")

    # ---------- GET ----------
    def do_GET(self):
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        if url.path == "/":
            return self.index(q)
        if url.path == "/export.csv":
            return self.export(q)
        if url.path.startswith("/files/"):
            return self.serve_file(unquote(url.path[len("/files/"):]))
        self.send(404, page("<p>ไม่พบหน้านี้</p>"))

    def where(self, q):
        where, params = [], []
        month = q.get("month", "")
        if month:
            try:
                datetime.strptime(month, "%Y-%m")
                where.append("substr(date,1,7) = ?")
                params.append(month)
            except ValueError:
                pass
        if q.get("category"):
            where.append("category = ?")
            params.append(q["category"])
        if q.get("q"):
            like = f"%{q['q']}%"
            where.append("(store LIKE ? OR note LIKE ?)")
            params += [like, like]
        return (" WHERE " + " AND ".join(where)) if where else "", params

    def index(self, q, error=""):
        db = receipts.connect()
        w, params = self.where(q)
        rows = db.execute(f"SELECT * FROM receipts{w} ORDER BY date DESC, id DESC", params).fetchall()
        sums = db.execute(
            f"SELECT category, COUNT(*) n, SUM(amount) t FROM receipts{w} "
            "GROUP BY category ORDER BY t DESC", params).fetchall()
        cats = [r[0] for r in db.execute("SELECT DISTINCT category FROM receipts ORDER BY 1")]
        e = escape
        qs = "&".join(f"{k}={e(v)}" for k, v in q.items() if v)
        tr = "".join(
            f"<tr><td>{r['date']}</td><td>{e(r['store'])}</td><td>{e(r['category'])}</td>"
            f"<td class=n>{r['amount']:,.2f}</td><td>{e(r['note'])}</td>"
            + (f"<td><a href='/files/{e(r['file'])}' target=_blank>ดูไฟล์</a></td>" if r["file"] else "<td>-</td>")
            + f"<td><form class=inline method=post action='/delete/{r['id']}' "
              "onsubmit=\"return confirm('ลบใบเสร็จนี้?')\"><button>ลบ</button></form></td></tr>"
            for r in rows) or "<tr><td colspan=7>ยังไม่มีใบเสร็จ</td></tr>"
        summary = "".join(f"<span>{e(r['category'])}: <b>{r['t']:,.2f}</b> ({r['n']})</span>" for r in sums)
        body = f"""
<fieldset><legend>เพิ่มใบเสร็จ</legend>
<form method=post action=/add enctype=multipart/form-data>
<label>วันที่ <input type=date name=date value="{date.today().isoformat()}" required></label>
<label>ร้านค้า <input name=store required></label>
<label>จำนวนเงิน <input name=amount type=number step=0.01 min=0 required></label>
<label>หมวด <input name=category list=cats value="ทั่วไป"></label>
<datalist id=cats>{''.join(f'<option value="{e(c)}">' for c in cats)}</datalist>
<label>หมายเหตุ <input name=note></label>
<label>ไฟล์ <input type=file name=file accept="image/*,.pdf"></label>
<button>บันทึก</button></form></fieldset>
<form method=get><label>เดือน <input type=month name=month value="{e(q.get('month',''))}"></label>
<label>หมวด <input name=category value="{e(q.get('category',''))}"></label>
<label>ค้นหา <input name=q value="{e(q.get('q',''))}"></label>
<button>กรอง</button> <a href="/">ล้าง</a> · <a href="/export.csv?{qs}">ส่งออก CSV</a></form>
<p class=sum>{summary}<span>รวม <b>{sum(r['amount'] for r in rows):,.2f}</b> ({len(rows)} รายการ)</span></p>
<table><tr><th>วันที่<th>ร้านค้า<th>หมวด<th class=n>จำนวนเงิน<th>หมายเหตุ<th>ไฟล์<th></tr>{tr}</table>"""
        self.send(200, page(body, error))

    def export(self, q):
        w, params = self.where(q)
        rows = receipts.connect().execute(
            f"SELECT * FROM receipts{w} ORDER BY date, id", params).fetchall()
        buf = io.StringIO()
        wr = csv.writer(buf)
        wr.writerow(["id", "date", "store", "amount", "category", "note", "file"])
        for r in rows:
            wr.writerow([r["id"], r["date"], r["store"], r["amount"], r["category"], r["note"], r["file"] or ""])
        self.send(200, buf.getvalue().encode("utf-8-sig"), "text/csv; charset=utf-8",
                  {"Content-Disposition": 'attachment; filename="receipts.csv"'})

    def serve_file(self, name):
        if name != os.path.basename(name) or not name:
            return self.send(404, page("<p>ไม่พบไฟล์</p>"))
        path = os.path.join(receipts.FILES_DIR, name)
        if not os.path.isfile(path):
            return self.send(404, page("<p>ไม่พบไฟล์</p>"))
        with open(path, "rb") as f:
            data = f.read()
        ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
        self.send(200, data, ctype, {"Content-Disposition": "inline",
                                     "Content-Security-Policy": "sandbox"})

    # ---------- POST ----------
    def do_POST(self):
        if not self.same_origin():
            return self.send(403, page("<p>คำขอไม่ถูกต้อง</p>"))
        url = urlparse(self.path)
        if url.path == "/add":
            return self.add()
        if url.path.startswith("/delete/"):
            return self.delete(url.path[len("/delete/"):])
        self.send(404, page("<p>ไม่พบหน้านี้</p>"))

    def add(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_UPLOAD + 65536:
            return self.index({}, "ไฟล์ใหญ่เกิน 20MB")
        fields, files = parse_multipart(self.headers.get("Content-Type", ""), self.rfile.read(length))
        try:
            d = datetime.strptime(fields.get("date", ""), "%Y-%m-%d").date().isoformat()
            store = fields.get("store", "")
            amount = float(fields.get("amount", ""))
            if not store or amount < 0:
                raise ValueError
        except ValueError:
            return self.index({}, "ข้อมูลไม่ถูกต้อง: ตรวจวันที่ ร้านค้า และจำนวนเงิน")
        fname = None
        up = files.get("file")
        if up and up[1]:
            base = os.path.basename(up[0].replace("\\", "/"))
            if os.path.splitext(base)[1].lower() not in ALLOWED_EXT:
                return self.index({}, "รองรับเฉพาะไฟล์รูปภาพและ PDF")
            if len(up[1]) > MAX_UPLOAD:
                return self.index({}, "ไฟล์ใหญ่เกิน 20MB")
            fname = receipts.unique_name(base)
            with open(os.path.join(receipts.FILES_DIR, fname), "wb") as f:
                f.write(up[1])
        db = receipts.connect()
        db.execute(
            "INSERT INTO receipts (date, store, amount, category, note, file, created_at) VALUES (?,?,?,?,?,?,?)",
            (d, store, amount, fields.get("category") or "ทั่วไป", fields.get("note", ""), fname,
             datetime.now().isoformat(timespec="seconds")))
        db.commit()
        self.redirect()

    def delete(self, rid):
        if rid.isdigit():
            db = receipts.connect()
            r = db.execute("SELECT file FROM receipts WHERE id = ?", (int(rid),)).fetchone()
            db.execute("DELETE FROM receipts WHERE id = ?", (int(rid),))
            db.commit()
            if r and r["file"] and not db.execute(
                    "SELECT 1 FROM receipts WHERE file = ?", (r["file"],)).fetchone():
                try:
                    os.remove(os.path.join(receipts.FILES_DIR, r["file"]))
                except FileNotFoundError:
                    pass
        self.redirect()


def main():
    ap = argparse.ArgumentParser(description="เว็บแอปเก็บรวบรวมใบเสร็จ")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args()
    receipts.connect().close()
    print(f"เปิดที่ http://{a.host}:{a.port}  (Ctrl+C เพื่อหยุด)")
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
