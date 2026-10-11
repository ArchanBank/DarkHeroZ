# Receipt Collector (โปรแกรมเก็บรวบรวมใบเสร็จ)

โปรแกรม command-line สำหรับเก็บใบเสร็จ ใช้ Python 3.8+ ไม่ต้องติดตั้งไลบรารีเพิ่ม

ข้อมูลเก็บที่ `~/.receipts/` (เปลี่ยนได้ด้วยตัวแปร `RECEIPTS_DIR`):
ฐานข้อมูล `receipts.db` และสำเนาไฟล์ใบเสร็จใน `files/`

## วิธีใช้

```bash
python receipts.py add -d 2026-10-11 -s "7-Eleven" -a 125.50 -c อาหาร -f bill.jpg
python receipts.py list [--month 2026-10] [--category อาหาร]
python receipts.py search 7-Eleven
python receipts.py show 1
python receipts.py summary --month 2026-10
python receipts.py export receipts.csv [--month 2026-10]
python receipts.py delete 1
```

ถ้าไม่ระบุ `-d` จะใช้วันที่วันนี้
