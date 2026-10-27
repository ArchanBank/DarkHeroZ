"""QC dashboard: แนวโน้มรายเดือน, KPI, top fired rules, systemic findings

การเปรียบเทียบรายแพทย์ใช้เพื่อพัฒนาเท่านั้น: แสดงเป็นรหัสนิรนาม เรียงตามตัวอักษร ไม่จัดอันดับ ไม่ระบุตัวบุคคล
"""
import hashlib
from collections import defaultdict

SYSTEMIC = [
    (("R01", "R10"), "ไม่มี Heart Team/SYNTAX ใน LM หรือ MVD", "เพิ่ม Heart Team block บังคับเมื่อ anatomy เข้า trigger (LM ≥50% หรือ ≥2 vessels ≥70%)"),
    (("R02",), "ให้ vasopressor/MCS โดยไม่บันทึกเหตุผล", "เมื่อ log บันทึกยา vasoactive ให้บังคับกรอกเวลา, BP, สาเหตุ และเวลาหยุด"),
    (("R03", "Y04"), "DAPT เขียน 'after ACS' แบบตายตัว", "เปลี่ยนเป็น dropdown ตาม syndrome พร้อมเหตุผลระยะเวลา"),
    (("G01",), "ไม่มี QC metrics (contrast, Air Kerma, DAP, fluoro)", "ดึงค่าอัตโนมัติจากเครื่อง angiography"),
    (("Y01",), "ใช้ imaging แต่ไม่มีตัวเลข", "เพิ่มช่อง MSA, % expansion และ criterion ต่อ lesion"),
    (("R07", "R08"), "Copy-paste ข้าม lesion / ชื่ออุปกรณ์ไม่ตรงกัน", "ดึงชื่ออุปกรณ์จาก barcode/UDI scan โดยตรง และเตือนเมื่อข้อความ lesion ซ้ำกัน"),
    (("Y12", "C-ALL-sex"), "Header ไม่มีเพศและหัตถการไม่ครบ", "สร้างรายการหัตถการใน header อัตโนมัติจาก log"),
]
SYSTEMIC_MIN_SHARE = 0.30
SYSTEMIC_MIN_CASES = 5


def _alias(owner):
    return "แพทย์ " + hashlib.sha256(("pci-audit:" + owner).encode()).hexdigest()[:4].upper()


def _pct(n, d):
    return round(100 * n / d, 1) if d else None


def build(rows, first_last=None, score_lookup=None):
    """rows: store.all_evaluations()"""
    months = defaultdict(list)
    for r in rows:
        months[(r["proc_date"] or r["created"] or "")[:7] or "ไม่ระบุ"].append(r)

    def k(rs, key):
        return [r["evaluation"]["kpi"][key] for r in rs]

    def kpis(rs):
        lmmvd = [r for r in rs if r["evaluation"]["kpi"]["lm_mvd"]]
        vaso = [r for r in rs if r["evaluation"]["kpi"]["vasopressor_event"]]
        stemi = [r for r in rs if r["evaluation"]["kpi"]["stemi"]]
        img = [r for r in rs if r["evaluation"]["kpi"]["imaging_used"]]
        scored = [r["evaluation"]["overall"] for r in rs if r["evaluation"]["overall"] is not None]
        claims = [r for r in rs if r["claim_status"] in ("approved", "denied")]
        return {
            "cases": len(rs),
            "mean_overall": round(sum(scored) / len(scored), 2) if scored else None,
            "pct_ge_7": _pct(sum(1 for s in scored if s >= 7.0), len(scored)),
            "pct_lmmvd_heart_team": _pct(sum(r["evaluation"]["kpi"]["lm_mvd_heart_team"] for r in lmmvd), len(lmmvd)),
            "pct_vasopressor_no_reason": _pct(sum(r["evaluation"]["kpi"]["vasopressor_no_reason"] for r in vaso), len(vaso)),
            "pct_stemi_with_d2b": _pct(sum(r["evaluation"]["kpi"]["stemi_has_d2b"] for r in stemi), len(stemi)),
            "pct_stemi_d2b_le_90": _pct(sum(1 for r in stemi if r["evaluation"]["kpi"]["d2b_le_target"]), len(stemi)),
            "pct_imaging_with_msa": _pct(sum(r["evaluation"]["kpi"]["imaging_has_msa"] for r in img), len(img)),
            "pct_contrast_egfr": _pct(sum(r["evaluation"]["kpi"]["has_contrast_egfr"] for r in rs), len(rs)),
            "pct_air_kerma": _pct(sum(r["evaluation"]["kpi"]["has_air_kerma"] for r in rs), len(rs)),
            "claim_denial_rate": _pct(sum(1 for r in claims if r["claim_status"] == "denied"), len(claims)),
            "claims_known": len(claims),
        }

    n = len(rows)
    fired = defaultdict(set)
    by_type = defaultdict(list)
    for r in rows:
        ev = r["evaluation"]
        for f in ev["findings"]:
            if not f["overridden"] and not f["needs_review"]:
                fired[f["rule_id"].split("@")[0]].add(r["id"])
        for t in ev["case_types"]:
            if ev["overall"] is not None:
                by_type[t].append(ev["overall"])
    rank = lambda items: sorted(({"rule_id": rid, "cases": len(ids), "pct": _pct(len(ids), n)} for rid, ids in items),
                                key=lambda x: (-x["cases"], x["rule_id"]))[:15]
    top = rank((r, i) for r, i in fired.items() if not r.startswith("C-"))
    top_missing = rank((r, i) for r, i in fired.items() if r.startswith("C-"))
    systemic = []
    for ids, title, fix in SYSTEMIC:
        cases = set().union(*(fired.get(i, set()) for i in ids))
        share = len(cases) / n if n else 0
        systemic.append({"rules": list(ids), "title": title, "fix": fix, "cases": len(cases), "pct": _pct(len(cases), n),
                         "flag": n >= SYSTEMIC_MIN_CASES and share >= SYSTEMIC_MIN_SHARE})
    owners = defaultdict(list)
    for r in rows:
        if r["evaluation"]["overall"] is not None:
            owners[_alias(r["owner"] or "?")].append(r["evaluation"]["overall"])
    physicians = [{"alias": a, "cases": len(v), "mean_overall": round(sum(v) / len(v), 2)} for a, v in sorted(owners.items())]

    close_loop = None
    if first_last and score_lookup:
        pairs = [(score_lookup(x["first_id"]), score_lookup(x["last_id"])) for x in first_last if x["first_id"] != x["last_id"]]
        pairs = [(a, b) for a, b in pairs if a is not None and b is not None]
        if pairs:
            close_loop = {"cases": len(pairs), "before": round(sum(a for a, _ in pairs) / len(pairs), 2),
                          "after": round(sum(b for _, b in pairs) / len(pairs), 2)}
    return {
        "overall": kpis(rows),
        "monthly": [{"month": m, **kpis(rs)} for m, rs in sorted(months.items())],
        "by_case_type": [{"case_type": t, "cases": len(v), "mean_overall": round(sum(v) / len(v), 2)} for t, v in sorted(by_type.items())],
        "top_rules": top, "top_missing": top_missing, "systemic": systemic, "physicians": physicians, "close_loop": close_loop,
        "note": "การแสดงผลเปรียบเทียบรายแพทย์ใช้เพื่อพัฒนา peer review เท่านั้น ไม่ใช่ ranking หรือประเมินผลงานรายบุคคล",
    }
