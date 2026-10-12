#!/usr/bin/env python3
"""โปรแกรมเก็บรวบรวมใบเสร็จ (Receipt collector).

เก็บข้อมูลใน SQLite และคัดลอกไฟล์รูป/PDF ของใบเสร็จมาไว้ในโฟลเดอร์ข้อมูล
ไม่ต้องติดตั้งไลบรารีเพิ่ม (Python 3.8+)

ตัวอย่าง:
  python receipts.py add -d 2026-10-11 -s "7-Eleven" -a 125.50 -c อาหาร -f bill.jpg
  python receipts.py list --month 2026-10
  python receipts.py search 7-Eleven
  python receipts.py summary --month 2026-10
  python receipts.py export receipts.csv
"""
import argparse
import csv
import os
import shutil
import sqlite3
import sys
from datetime import date, datetime

DATA_DIR = os.environ.get("RECEIPTS_DIR", os.path.expanduser("~/.receipts"))
DB_PATH = os.path.join(DATA_DIR, "receipts.db")
FILES_DIR = os.path.join(DATA_DIR, "files")


def connect():
    os.makedirs(FILES_DIR, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute(
        """CREATE TABLE IF NOT EXISTS receipts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            store TEXT NOT NULL,
            amount REAL NOT NULL,
            category TEXT NOT NULL DEFAULT 'ทั่วไป',
            note TEXT NOT NULL DEFAULT '',
            file TEXT,
            created_at TEXT NOT NULL
        )"""
    )
    return db


def parse_date(s):
    try:
        return datetime.strptime(s, "%Y-%m-%d").date().isoformat()
    except ValueError:
        raise argparse.ArgumentTypeError(f"วันที่ต้องเป็นรูปแบบ YYYY-MM-DD: {s}")


def parse_month(s):
    try:
        return datetime.strptime(s, "%Y-%m").strftime("%Y-%m")
    except ValueError:
        raise argparse.ArgumentTypeError(f"เดือนต้องเป็นรูปแบบ YYYY-MM: {s}")


def unique_name(base):
    """คืนชื่อไฟล์ที่ยังไม่ซ้ำในโฟลเดอร์ข้อมูล"""
    os.makedirs(FILES_DIR, exist_ok=True)
    stem, ext = os.path.splitext(base)
    name = base
    n = 1
    while os.path.exists(os.path.join(FILES_DIR, name)):
        name = f"{stem}_{n}{ext}"
        n += 1
    return name


def store_file(path):
    """คัดลอกไฟล์ใบเสร็จเข้าโฟลเดอร์ข้อมูล คืนชื่อไฟล์ที่เก็บ"""
    if not os.path.isfile(path):
        sys.exit(f"ไม่พบไฟล์: {path}")
    name = unique_name(os.path.basename(path))
    shutil.copy2(path, os.path.join(FILES_DIR, name))
    return name


def print_rows(rows):
    if not rows:
        print("ไม่พบใบเสร็จ")
        return
    print(f"{'ID':>4}  {'วันที่':10}  {'ร้านค้า':20}  {'หมวด':12}  {'จำนวนเงิน':>12}  ไฟล์")
    for r in rows:
        print(
            f"{r['id']:>4}  {r['date']:10}  {r['store'][:20]:20}  "
            f"{r['category'][:12]:12}  {r['amount']:>12,.2f}  {r['file'] or '-'}"
        )
    print(f"รวม {len(rows)} รายการ  ยอดรวม {sum(r['amount'] for r in rows):,.2f}")


def filters(args):
    where, params = [], []
    if getattr(args, "month", None):
        where.append("substr(date,1,7) = ?")
        params.append(args.month)
    if getattr(args, "category", None):
        where.append("category = ?")
        params.append(args.category)
    return (" WHERE " + " AND ".join(where)) if where else "", params


def cmd_add(args):
    fname = store_file(args.file) if args.file else None
    db = connect()
    cur = db.execute(
        "INSERT INTO receipts (date, store, amount, category, note, file, created_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (args.date, args.store, args.amount, args.category, args.note, fname,
         datetime.now().isoformat(timespec="seconds")),
    )
    db.commit()
    print(f"บันทึกใบเสร็จ #{cur.lastrowid} เรียบร้อย")


def cmd_list(args):
    where, params = filters(args)
    rows = connect().execute(
        f"SELECT * FROM receipts{where} ORDER BY date DESC, id DESC", params
    ).fetchall()
    print_rows(rows)


def cmd_search(args):
    like = f"%{args.text}%"
    rows = connect().execute(
        "SELECT * FROM receipts WHERE store LIKE ? OR note LIKE ? OR category LIKE ?"
        " ORDER BY date DESC, id DESC",
        (like, like, like),
    ).fetchall()
    print_rows(rows)


def cmd_show(args):
    r = connect().execute("SELECT * FROM receipts WHERE id = ?", (args.id,)).fetchone()
    if not r:
        sys.exit("ไม่พบใบเสร็จนี้")
    for k in r.keys():
        print(f"{k:11}: {r[k] if r[k] is not None else '-'}")
    if r["file"]:
        print(f"{'path':11}: {os.path.join(FILES_DIR, r['file'])}")


def cmd_delete(args):
    db = connect()
    r = db.execute("SELECT * FROM receipts WHERE id = ?", (args.id,)).fetchone()
    if not r:
        sys.exit("ไม่พบใบเสร็จนี้")
    db.execute("DELETE FROM receipts WHERE id = ?", (args.id,))
    db.commit()
    still_used = r["file"] and db.execute(
        "SELECT 1 FROM receipts WHERE file = ?", (r["file"],)).fetchone()
    if r["file"] and not still_used:
        try:
            os.remove(os.path.join(FILES_DIR, r["file"]))
        except FileNotFoundError:
            pass
    print(f"ลบใบเสร็จ #{args.id} แล้ว")


def cmd_summary(args):
    where, params = filters(args)
    db = connect()
    rows = db.execute(
        f"SELECT category, COUNT(*) n, SUM(amount) total FROM receipts{where}"
        " GROUP BY category ORDER BY total DESC", params
    ).fetchall()
    if not rows:
        print("ไม่พบใบเสร็จ")
        return
    for r in rows:
        print(f"{r['category']:16} {r['n']:>4} รายการ  {r['total']:>12,.2f}")
    print(f"{'รวม':16} {sum(r['n'] for r in rows):>4} รายการ  "
          f"{sum(r['total'] for r in rows):>12,.2f}")


def cmd_export(args):
    where, params = filters(args)
    rows = connect().execute(
        f"SELECT * FROM receipts{where} ORDER BY date, id", params).fetchall()
    # utf-8-sig เพื่อให้ Excel เปิดภาษาไทยได้ถูกต้อง
    with open(args.out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["id", "date", "store", "amount", "category", "note", "file"])
        for r in rows:
            w.writerow([r["id"], r["date"], r["store"], r["amount"], r["category"],
                        r["note"], r["file"] or ""])
    print(f"ส่งออก {len(rows)} รายการไปที่ {args.out}")


def build_parser():
    p = argparse.ArgumentParser(description="โปรแกรมเก็บรวบรวมใบเสร็จ")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="เพิ่มใบเสร็จ")
    a.add_argument("-d", "--date", type=parse_date, default=date.today().isoformat())
    a.add_argument("-s", "--store", required=True, help="ชื่อร้านค้า")
    a.add_argument("-a", "--amount", type=float, required=True, help="จำนวนเงิน")
    a.add_argument("-c", "--category", default="ทั่วไป")
    a.add_argument("-n", "--note", default="")
    a.add_argument("-f", "--file", help="ไฟล์รูป/PDF ของใบเสร็จ")
    a.set_defaults(func=cmd_add)

    for name, fn, h in (("list", cmd_list, "แสดงรายการ"),
                        ("summary", cmd_summary, "สรุปยอดตามหมวด")):
        s = sub.add_parser(name, help=h)
        s.add_argument("--month", type=parse_month)
        s.add_argument("--category")
        s.set_defaults(func=fn)

    s = sub.add_parser("search", help="ค้นหา (ร้าน/หมายเหตุ/หมวด)")
    s.add_argument("text")
    s.set_defaults(func=cmd_search)

    s = sub.add_parser("show", help="ดูรายละเอียด")
    s.add_argument("id", type=int)
    s.set_defaults(func=cmd_show)

    s = sub.add_parser("delete", help="ลบใบเสร็จ")
    s.add_argument("id", type=int)
    s.set_defaults(func=cmd_delete)

    s = sub.add_parser("export", help="ส่งออกเป็น CSV")
    s.add_argument("out")
    s.add_argument("--month", type=parse_month)
    s.add_argument("--category")
    s.set_defaults(func=cmd_export)
    return p


def main():
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
