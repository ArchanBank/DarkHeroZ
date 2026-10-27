# PCI Audit & QC — AI Assisted Clinical Review

เว็บแอปตรวจ **คุณภาพเอกสาร** รายงาน CAG/PCI ก่อนส่งเบิก ตามเอกสารออกแบบ "PCI Report Audit & QC Web App"
ให้คะแนน 4 หมวด สร้างรายการแก้ (แดง/เหลือง/เขียว) ร่าง addendum และตรวจ coding — **ไม่ได้ตัดสินว่าการรักษาถูกหรือผิด**

## หลักการ ML ที่ใช้ (และที่ไม่ใช้)
| ขั้นตอน | วิธี |
|---|---|
| ดึงข้อมูลจากไฟล์ (PDF/ภาพ) | **Claude ผ่าน Cloud API** (structured output ตาม JSON schema) พร้อม source span + confidence |
| กันโมเดลเติมค่าเอง | ต้องอ้าง span; ระบบตรวจ span กับข้อความต้นฉบับ ถ้าไม่พบ → ลด confidence ครึ่งหนึ่ง + ส่งผู้ตรวจ |
| ประเมิน/ให้คะแนน | **Rule engine แบบ deterministic** (R01–R13, Y01–Y12, G01 + completeness ต่อ case type) — ไม่ใช้โมเดล black box ตามหลัก "Explainable rules" |
| ข้อสังเกตเพิ่มเติมจาก AI | แสดงเป็น `review_notes` ให้ผู้ตรวจพิจารณา ไม่นับคะแนน |

## เริ่มใช้งาน
```bash
pip install anthropic pypdf pypdfium2 pillow      # tesseract (ถ้ามี) ใช้ OCR ในเครื่อง
export ANTHROPIC_API_KEY=...                       # หรือ PCI_AUDIT_PROVIDER=bedrock|vertex
python -m pci_audit serve                          # http://127.0.0.1:8080 (โหมด dev ไม่ล็อกอิน เฉพาะ localhost)
python -m pci_audit add-user dr_somchai physician  # สร้างผู้ใช้ → เปิดโหมดล็อกอิน (role: physician/auditor/coder/manager/admin)
python -m unittest discover -s pci_audit/tests -t .
```
ทดลองโดยไม่เรียก API: กด "นำเข้าเคสตัวอย่าง (สังเคราะห์)" หรือตั้ง `PCI_AUDIT_MOCK=1`

ตัวแปรสำคัญ: `PCI_AUDIT_MODEL` (ค่าเริ่มต้น `claude-opus-5-5`), `PCI_AUDIT_DIR`, `PCI_AUDIT_RETENTION_DAYS` (90),
`PCI_AUDIT_ALLOW_IMAGE_UPLOAD=1` (อนุญาตส่งภาพสแกนตรง), `PCI_AUDIT_FALLBACK=0` (ปิด server-side refusal fallback), `ANTHROPIC_BASE_URL` (gateway องค์กร)

## Workflow 8 ขั้น
Ingest → De-identify → Extract → Verify → Classify (case type) → Evaluate → Report → Close loop (re-score เก็บคะแนนก่อน/หลังแก้ → dashboard)

## Privacy & governance
- ซ่อนชื่อ/HN/AN/เลขบัตร/DOB/โทร/อีเมล **ก่อน** ส่ง Cloud API; mapping เก็บในฐานข้อมูลของเซิร์ฟเวอร์เท่านั้น; ผู้ใช้ต้องดู preview และยืนยันทุกครั้ง
- De-id เป็น regex แบบ best-effort — ไม่รับประกันครบ จึงมี preview + เตือนข้อมูลที่อาจเหลือ
- **ภาพสแกนที่ไม่มี OCR ในเครื่องซ่อนข้อมูลในภาพไม่ได้** → ปิดเป็นค่าเริ่มต้น เปิดได้เมื่อมี DPA และผู้ใช้ติ๊กยืนยันต่อเคส
- Role-based access, audit trail ทุกการแก้ field/ยืนยัน/override, override ต้องมีเหตุผลและแสดงในรายงาน, retention ของไฟล์สแกน (`python -m pci_audit purge`)
- Basic auth + CSRF header + CSP — ใช้จริงต้องอยู่หลัง TLS reverse proxy
- ruleset มี version/เจ้าของ/วันที่มีผล (`ruleset.json`); ตาราง IFU/GTIN (`lookups.json`) ที่ยังไม่มีข้อมูลจริง rule ที่เกี่ยวข้อง (Y09) จะแสดง "ประเมินไม่ได้" ไม่ใช่ผ่าน
- ทุกหน้าที่แสดงคะแนนมีข้อความ: เกณฑ์ภายในที่ยังไม่ผ่านการ validate

## ส่วนที่ปรับปรุงจากเอกสารออกแบบ
1. Rule แดงที่ค้างอยู่ **ห้าม auto-unlock** แม้คะแนนรวม ≥8.5 (เอกสารเดิม cap แค่หมวดเดียว)
2. Field confidence ต่ำที่ยังไม่ verify → ไม่หักคะแนน แต่เข้า "รอยืนยัน"
3. คะแนนที่จะได้เพิ่มของแต่ละ rule → เรียงรายการแก้ตามผลกระทบ
4. Near-miss (เกือบดี ติดแดง 1 ข้อ) แยกจากเคสที่ขาดหลายข้อ
5. ตรวจ span กับข้อความต้นฉบับ, แปลง พ.ศ.→ค.ศ. อัตโนมัติ, ป้องกัน prompt injection จากเนื้อหาไฟล์
6. Dashboard เทียบรายแพทย์แบบนิรนาม เรียงตามตัวอักษร (ไม่ใช่ ranking)

## สมมติฐานที่ต้องให้ rule owner ยืนยัน
- **R09** ตีความ "ไม่มี pre-consent หรือ Heart Team" = ไม่มีทั้งสองอย่าง
- STEMI: Heart Team = N/A (R01 ถูกข้ามและแสดงในรายการ "ประเมินไม่ได้")
- เขียว (G01) หักคะแนนเกณฑ์ 3.5 เหลือ 1 (ปรับได้ที่ `policy.green_deducts`)
- การจับคู่ rule → เกณฑ์ 20 ข้อ (ตาราง `criteria` ใน `rules.py`) เป็นการออกแบบของระบบนี้ เอกสารต้นแบบไม่ได้ระบุ
- ข้อมูลอ้างอิงแนวทางเวชปฏิบัติและรหัส ICD-9-CM/ICD-10 เป็นข้อเสนอ ต้องให้ coder/rule owner ตรวจกับมาตรฐานโรงพยาบาล
- Roadmap Phase 0 ต้อง validate rule fire ≥90% กับผล audit ด้วยมือ 9 เคส — ยังไม่ได้ทำ (ไม่มีข้อมูลเคสจริงในรีโป)

## ยังไม่ได้ทำ / ข้อจำกัด
- ยังไม่ได้ทดสอบกับ Cloud API จริง (ไม่มี API key ใน sandbox) — ทดสอบด้วย client จำลองที่ตรวจ request/response shape; ควรทดสอบกับ PDF จริงที่ de-identify แล้วก่อนใช้
- ยังไม่มี Phase 4 integration (ดึงค่าจากเครื่อง/UDI scan) และ Phase 5 outcome link
- UI ใช้ vanilla JS ไม่มี build step; ยังไม่ได้ทดสอบ accessibility อย่างเป็นระบบ
