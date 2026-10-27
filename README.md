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

## เว็บแอป

```bash
python webapp.py            # เปิดที่ http://127.0.0.1:8000
python webapp.py --port 9000
```

ใช้ฐานข้อมูลและโฟลเดอร์ไฟล์เดียวกับ CLI — เพิ่ม/ค้นหา/กรองตามเดือนและหมวด/ดูไฟล์แนบ/ลบ/ส่งออก CSV ได้จากหน้าเว็บ
รองรับไฟล์แนบ jpg, png, gif, webp, heic, pdf (ไม่เกิน 20MB)

**ข้อควรระวัง:** ยังไม่มีระบบล็อกอิน จึงผูกกับ `127.0.0.1` เป็นค่าเริ่มต้น
อย่าใช้ `--host 0.0.0.0` บนเครือข่ายสาธารณะโดยไม่มีการยืนยันตัวตนด้านหน้า

## PCI Audit & QC (AI Assisted Clinical Review)

เว็บแอปตรวจคุณภาพเอกสารรายงาน PCI ด้วย Claude (Cloud API) + rule engine — ดู [`pci_audit/README.md`](pci_audit/README.md)
