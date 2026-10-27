"""ฟังก์ชันช่วยแปลงค่า: วันที่ (รวม พ.ศ.), เวลา, ตัวเลข, boolean, ข้อความ"""
import re
from datetime import date, datetime

_TH_MONTHS = {
    "ม.ค.": 1, "ก.พ.": 2, "มี.ค.": 3, "เม.ย.": 4, "พ.ค.": 5, "มิ.ย.": 6,
    "ก.ค.": 7, "ส.ค.": 8, "ก.ย.": 9, "ต.ค.": 10, "พ.ย.": 11, "ธ.ค.": 12,
    "มกราคม": 1, "กุมภาพันธ์": 2, "มีนาคม": 3, "เมษายน": 4, "พฤษภาคม": 5, "มิถุนายน": 6,
    "กรกฎาคม": 7, "สิงหาคม": 8, "กันยายน": 9, "ตุลาคม": 10, "พฤศจิกายน": 11, "ธันวาคม": 12,
}
_EN_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
EMPTY = {"", "none", "null", "n/a", "na", "-", "ไม่พบ", "ไม่ระบุ", "unknown"}


def is_empty(v):
    if v is None:
        return True
    if isinstance(v, str):
        return v.strip().lower() in EMPTY
    if isinstance(v, (list, tuple, dict, set)):
        return len(v) == 0
    return False


def _year(y):
    y = int(y)
    if y < 100:
        y += 2000
    if y > 2400:  # พ.ศ.
        y -= 543
    return y


def parse_date(v):
    """คืน datetime.date หรือ None รองรับ ISO, dd/mm/yyyy, '5 ต.ค. 2569', '5 Oct 2026' (พ.ศ. แปลงเป็น ค.ศ.)"""
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if is_empty(v):
        return None
    s = str(v).strip()
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    try:
        if m:
            return date(_year(m.group(1)), int(m.group(2)), int(m.group(3)))
        m = re.search(r"(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})", s)
        if m:
            return date(_year(m.group(3)), int(m.group(2)), int(m.group(1)))
        m = re.search(r"(\d{1,2})\s*([^\d\s]+\.?[^\d\s]*)\s*(\d{2,4})", s)
        if m:
            name = m.group(2).strip().lower()
            mon = _TH_MONTHS.get(name) or _EN_MONTHS.get(name[:3])
            if mon:
                return date(_year(m.group(3)), mon, int(m.group(1)))
    except ValueError:
        return None
    return None


def parse_time(v):
    """คืนนาทีนับจากเที่ยงคืน หรือ None (รับ 'HH:MM', 'HHMM', 'HH.MM', ISO datetime)"""
    if is_empty(v):
        return None
    s = str(v)
    m = re.search(r"(\d{1,2})[:.](\d{2})", s) or re.fullmatch(r"\s*(\d{2})(\d{2})\s*", s)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    return h * 60 + mi if h < 24 and mi < 60 else None


def minutes_between(start, end):
    a, b = parse_time(start), parse_time(end)
    if a is None or b is None:
        return None
    d = b - a
    return d + 1440 if d < 0 else d


def months_between(d1, d2):
    """จำนวนเดือน (ทศนิยม) จาก d1 ถึง d2"""
    if not d1 or not d2:
        return None
    return abs((d2 - d1).days) / 30.4375


def to_float(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if is_empty(v):
        return None
    m = re.search(r"-?\d+(?:[.,]\d+)?", str(v))
    return float(m.group(0).replace(",", ".")) if m else None


_TRUE = {"true", "yes", "y", "1", "มี", "ใช่", "พบ", "ทำ", "positive", "+"}
_FALSE = {"false", "no", "n", "0", "ไม่มี", "ไม่ใช่", "ไม่ได้ทำ", "negative"}


def to_bool(v):
    if isinstance(v, bool):
        return v
    if is_empty(v):
        return None
    s = str(v).strip().lower()
    if s in _TRUE:
        return True
    if s in _FALSE:
        return False
    return None


def to_list(v):
    if is_empty(v):
        return []
    if isinstance(v, (list, tuple)):
        return [str(x).strip() for x in v if not is_empty(x)]
    return [x.strip() for x in re.split(r"[;,\n|]", str(v)) if x.strip()]


def norm_text(s):
    return re.sub(r"[^0-9a-zA-Z฀-๿]+", " ", str(s or "")).strip().lower()


def squash(s):
    return re.sub(r"\s+", "", norm_text(s))


def norm_size(s):
    """'3.0 x 24 mm' -> (3.0, 24.0)"""
    nums = re.findall(r"\d+(?:\.\d+)?", str(s or ""))
    return tuple(float(n) for n in nums[:2])
