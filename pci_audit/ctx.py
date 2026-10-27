"""Context สำหรับ rule engine: เข้าถึง field อย่างปลอดภัย, ตรวจจับ case type, โหลด ruleset/lookup"""
import json
import os
import re

from . import util
from .schema import normalize_case

BASE = os.path.dirname(os.path.abspath(__file__))


def _load(name):
    path = os.environ.get("PCI_AUDIT_" + name.upper().split(".")[0], os.path.join(BASE, name))
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_ruleset():
    return _load("ruleset.json")


def load_lookups():
    return _load("lookups.json")


def major_vessel(text):
    s = str(text or "").lower()
    if re.search(r"\blm\b|left main|\blmca\b", s):
        return "LM"
    if re.search(r"lad|d[12]\b|diag", s):
        return "LAD"
    if re.search(r"lcx|circ|\bom\d?\b|ramus", s):
        return "LCX"
    if re.search(r"rca|pda|plv|rpl|\bpl\b|acute marginal|\bam\b", s):
        return "RCA"
    return None


def normalize_syndrome(v):
    """คืน STEMI | NSTEMI | UA | CCS | ACS (ไม่ระบุชนิด) | None"""
    s = util.norm_text(v)
    if not s:
        return None
    if "nstemi" in s or "non st" in s or "nste" in s.replace(" ", "") and "acs" not in s:
        return "NSTEMI"
    if "stemi" in s or "st elevation" in s or "st ยก" in s:
        return "STEMI"
    if re.search(r"\bua\b|unstable", s):
        return "UA"
    if re.search(r"ccs|stable|chronic|sihd|เรื้อรัง", s):
        return "CCS"
    if "acs" in s or "nste" in s:
        return "ACS"
    return None


class Finding(dict):
    """ผลการ fire ของ rule"""

    @staticmethod
    def make(rule, message, evidence=(), fix="", needs_review=False, detail=None):
        return Finding(
            rule_id=rule["id"], severity=rule["severity"], domain=rule["domain"],
            title=rule["title"], guideline=rule["guideline"], message=message,
            evidence=list(evidence), fix=fix or rule.get("fix", ""),
            needs_review=needs_review, overridden=False, override=None,
            detail=detail or {}, kind=rule.get("kind", "consistency"))


class Ctx:
    def __init__(self, case, ruleset=None, lookups=None):
        self.case = normalize_case(case)
        self.ruleset = ruleset or load_ruleset()
        self.cfg = self.ruleset["thresholds"]
        self.lookups = lookups or load_lookups()
        self.lesions = [Lesion(l, self) for l in self.case["lesions"]]
        self.devices = self.case["devices"]
        self.skipped = []  # [(rule_id, เหตุผลที่ประเมินไม่ได้)]
        self._detect()

    # ---- field access ----
    def f(self, key):
        return self.case["fields"].get(key) or {}

    def v(self, key):
        f = self.f(key)
        if f.get("status") in ("not_found", "na"):
            return None
        val = f.get("value")
        return None if util.is_empty(val) else val

    def has(self, key):
        return self.v(key) is not None

    def num(self, key):
        return util.to_float(self.v(key))

    def date(self, key):
        return util.parse_date(self.v(key))

    def boolean(self, key):
        return util.to_bool(self.v(key))

    def lst(self, key):
        return util.to_list(self.v(key))

    def is_na(self, key):
        return self.f(key).get("status") == "na"

    def skip(self, rule_id, reason):
        self.skipped.append({"rule_id": rule_id, "reason": reason})

    # ---- derived ----
    def d2b(self):
        return util.minutes_between(self.v("clinical.door_time"), self.v("clinical.wire_time"))

    def duration_min(self):
        return util.minutes_between(self.v("qc.start_time"), self.v("qc.end_time"))

    def syndrome(self):
        """syndrome ที่ใช้ตัดสิน: field หลัก > header > narrative"""
        for k in ("clinical.syndrome", "clinical.header_syndrome", "clinical.narrative_syndrome"):
            s = normalize_syndrome(self.v(k))
            if s and s != "ACS":
                return s
        for k in ("clinical.syndrome", "clinical.header_syndrome", "clinical.narrative_syndrome"):
            if normalize_syndrome(self.v(k)) == "ACS":
                return "ACS"
        return None

    def vessel_stenoses(self):
        """{vessel: max % stenosis ที่ขนาด vessel ≥ threshold หรือไม่ทราบขนาด}"""
        out = {}
        for l in self.lesions:
            vs = major_vessel(l.v("vessel") or l.v("name"))
            pct = l.num("stenosis_pct")
            if vs and pct is not None:
                out[vs] = max(out.get(vs, 0), pct)
        return out

    def treated_lesions(self):
        res = []
        for l in self.lesions:
            t = l.boolean("treated")
            if t is True or (t is None and (l.has("stent_brand") or l.boolean("dcb") or l.has("prep_device"))):
                res.append(l)
        return res

    def hbr(self):
        """คำนวณ ARC-HBR (2019) จาก field → (is_hbr, major[], minor[])"""
        major, minor = [], []
        if self.boolean("clinical.af_oac"):
            major.append("OAC ระยะยาว")
        egfr = self.num("patient.egfr")
        if egfr is not None:
            if egfr < 30:
                major.append("eGFR <30")
            elif egfr < 60:
                minor.append("eGFR 30–59")
        hb, sex = self.num("patient.hb"), util.norm_text(self.v("patient.sex"))
        if hb is not None:
            if hb < 11:
                major.append("Hb <11")
            elif hb < (13 if sex.startswith(("m", "ช")) else 12):
                minor.append("Hb ต่ำเล็กน้อย")
        plt = self.num("patient.plt")
        if plt is not None and plt < 100:
            major.append("Platelet <100")
        if self.boolean("clinical.malignancy"):
            major.append("มะเร็งที่ active")
        if self.boolean("patient.bleeding_history"):
            major.append("ประวัติเลือดออก/ICH")
        age = self.num("patient.age")
        if age is not None and age >= 75:
            minor.append("อายุ ≥75")
        if self.boolean("patient.chronic_nsaid_steroid"):
            minor.append("NSAID/steroid เรื้อรัง")
        return (len(major) >= 1 or len(minor) >= 2), major, minor

    # ---- case types ----
    def _detect(self):
        t = []
        syn = self.syndrome()
        if syn == "STEMI":
            t.append("STEMI")
        elif syn in ("NSTEMI", "UA"):
            t.append("NSTE-ACS")
        elif syn == "CCS":
            t.append("CCS")
        stn = self.vessel_stenoses()
        lm = self.num("anatomy.lm_stenosis_pct")
        lm_lesion = max([l.num("stenosis_pct") or 0 for l in self.lesions if major_vessel(l.v("vessel") or l.v("name")) == "LM"] or [0])
        lm = max(lm or 0, lm_lesion)
        self.lm_pct = lm
        mvd_n = sum(1 for k, p in stn.items() if k != "LM" and p >= self.cfg["mvd_min_stenosis_pct"])
        self.mvd = mvd_n >= 2
        self.has_lm = lm >= self.cfg["lm_significant_pct"]
        if self.has_lm or self.mvd:
            t.append("LM/MVD")
            if self.boolean("clinical.dm") and self.mvd:
                t.append("DM+MVD")
        if any(l.boolean("cto") or (l.num("cto_duration_months") or 0) >= self.cfg["cto_min_months"] for l in self.lesions):
            t.append("CTO")
        if any(l.has("medina") or l.boolean("kbi_pot") for l in self.lesions):
            t.append("Bifurcation")
        if any(l.boolean("dcb") for l in self.lesions):
            t.append("DCB")
        if any(l.has("atherectomy_type") for l in self.lesions):
            t.append("Atherectomy")
        self.is_hbr, self.hbr_major, self.hbr_minor = self.hbr()
        if self.is_hbr:
            t.append("HBR")
        if self.boolean("clinical.af_oac"):
            t.append("OAC")
        if self.lst("qc.vasoactive") or self.has("qc.mcs_type"):
            t.append("Hemodynamic support")
        dur = self.duration_min()
        if (dur is not None and dur > self.cfg["long_procedure_min"]) or "CTO" in t or "Atherectomy" in t:
            t.append("Long/complex")
        self.case_types = t


class Lesion:
    def __init__(self, data, ctx):
        self.data, self.ctx = data, ctx
        self.id = data.get("id", "")

    def f(self, key):
        return self.data["fields"].get(key) or {}

    def v(self, key):
        f = self.f(key)
        if f.get("status") in ("not_found", "na"):
            return None
        val = f.get("value")
        return None if util.is_empty(val) else val

    def has(self, key):
        return self.v(key) is not None

    def num(self, key):
        return util.to_float(self.v(key))

    def boolean(self, key):
        return util.to_bool(self.v(key))

    @property
    def label(self):
        return str(self.v("name") or self.v("vessel") or self.id)

    @property
    def is_lm(self):
        return major_vessel(self.v("vessel") or self.v("name")) == "LM"
