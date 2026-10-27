"""De-identification ก่อนส่งข้อมูลออกไปประมวลผลบน Cloud API

ซ่อนชื่อผู้ป่วย, HN/AN, เลขบัตรประชาชน, วันเกิด, เบอร์โทร, อีเมล แล้วเก็บ mapping ไว้ที่ server ของโรงพยาบาลเท่านั้น
ไม่ซ่อนชื่อแพทย์ (นพ./พญ./Dr.) และวันที่ทำหัตถการ เพราะ rule ต้องใช้

ข้อจำกัด: เป็น regex แบบ best-effort — ต้องให้ผู้ใช้ดู preview และยืนยันก่อนส่งทุกครั้ง
และ OCR ของภาพสแกนที่ไม่มี text layer ไม่ผ่านขั้นตอนนี้ (ดู extractor.py / PCI_AUDIT_ALLOW_IMAGE_UPLOAD)
"""
import re

PATIENT_TITLES = r"(?:นาย|นาง(?:สาว)?|น\.ส\.|ด\.ช\.|ด\.ญ\.|เด็กชาย|เด็กหญิง|Mr\.?|Mrs\.?|Ms\.?|Miss)"
THAI_WORD = r"[ก-๙]{2,}"
LATIN_WORD = r"[A-Z][a-zA-Z\-']{1,}"

PATTERNS = [
    ("HN", re.compile(r"((?:HN|AN|VN|เลขที่เวชระเบียน|เลข\s*HN)\s*[:#.]?\s*)([A-Za-z]?\d[\d\-/]{4,})", re.I)),
    ("NID", re.compile(r"(?<![\d])(\d[\s\-]?\d{4}[\s\-]?\d{5}[\s\-]?\d{2}[\s\-]?\d)(?![\d])")),
    ("DOB", re.compile(r"((?:วันเกิด|วัน\s*เดือน\s*ปี\s*เกิด|DOB|Date of Birth|Birth\s*date)\s*[:.]?\s*)([^\n,;]{4,30})", re.I)),
    ("PHONE", re.compile(r"(?<!\d)(0\d{1,2}[\s\-]?\d{3}[\s\-]?\d{3,4})(?!\d)")),
    ("EMAIL", re.compile(r"([\w.+-]+@[\w-]+\.[\w.-]+)")),
    ("NAME", re.compile(r"((?:ชื่อ[\s\-]*(?:นามสกุล|สกุล)?|Patient(?:\s*Name)?|Name)\s*[:.]?\s*)(" + PATIENT_TITLES + r"?\s*(?:" + THAI_WORD + r"|" + LATIN_WORD + r")(?:\s+(?:" + THAI_WORD + r"|" + LATIN_WORD + r")){0,2})")),
    ("NAME", re.compile(r"((?<![ก-๙A-Za-z])" + PATIENT_TITLES + r"\s*)((?:" + THAI_WORD + r"|" + LATIN_WORD + r")(?:\s+(?:" + THAI_WORD + r"|" + LATIN_WORD + r")){0,2})")),
]
# ไม่ซ่อนข้อความที่ขึ้นต้นด้วยคำเหล่านี้ (ไม่ใช่ชื่อผู้ป่วย)
KEEP_PREFIX = re.compile(r"^(?:นพ|พญ|ดร|Dr|แพทย์|ศ\.|รศ|ผศ)", re.I)


class Deidentifier:
    """ใช้ instance เดียวต่อเคส เพื่อให้ token เดิมได้ค่าเดิม และเก็บ mapping"""

    def __init__(self):
        self.mapping = {}   # token -> ค่าจริง
        self._rev = {}      # (kind, ค่า) -> token
        self._count = {}

    def _token(self, kind, original):
        key = (kind, re.sub(r"\s+", " ", original.strip()).lower())
        if key in self._rev:
            return self._rev[key]
        n = self._count[kind] = self._count.get(kind, 0) + 1
        tok = f"[{kind}_{n}]"
        self._rev[key] = tok
        self.mapping[tok] = original.strip()
        return tok

    def redact(self, text):
        for kind, pat in PATTERNS:
            def sub(m, kind=kind):
                if kind in ("HN", "DOB", "NAME"):
                    prefix, val = m.group(1), m.group(2)
                    if kind == "NAME" and KEEP_PREFIX.match(val.strip()):
                        return m.group(0)
                    if val.startswith("["):  # redacted แล้ว
                        return m.group(0)
                    return prefix + self._token(kind, val)
                return self._token(kind, m.group(1))
            text = pat.sub(sub, text)
        return text

    def redact_known(self, text, values):
        """ซ่อนค่าที่รู้อยู่แล้ว (เช่น ชื่อที่ผู้ใช้กรอก) ทุกจุดที่ปรากฏ"""
        for v in sorted({x.strip() for x in values if x and len(x.strip()) >= 3}, key=len, reverse=True):
            text = re.sub(re.escape(v), lambda m: self._token("NAME", v), text, flags=re.I)
        return text

    def restore(self, text):
        for tok, orig in self.mapping.items():
            text = text.replace(tok, orig)
        return text


def has_residual_phi(text):
    """ตรวจซ้ำหลัง redact: คืนรายการรูปแบบที่ยังเหลือ (ใช้เตือนผู้ใช้ใน preview)"""
    out = []
    for kind in ("HN", "NID", "PHONE", "EMAIL"):
        for k, pat in PATTERNS:
            if k == kind and pat.search(text):
                out.append(kind)
                break
    return out
