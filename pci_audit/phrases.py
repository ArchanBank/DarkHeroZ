"""Phrase library สำหรับร่าง addendum + coding check (ข้อเสนอเท่านั้น)

ข้อจำกัดที่ออกแบบไว้:
  - ร่าง addendum ต้องให้แพทย์ผู้ทำหัตถการแก้ไขและลงนามก่อน ระบบไม่เขียนลงเวชระเบียนเอง
  - ข้อความเขียนในมุมผู้บันทึก (operator's note) ไม่ใช่การวิเคราะห์จากบุคคลที่สาม
  - ช่องที่ไม่มีข้อมูลในรายงานจะเว้นเป็น [____] ห้ามเติมค่าเอง
  - รหัส ICD เป็นข้อเสนอ coder ต้องยืนยันตามมาตรฐาน ICD-10-TM / ICD-9-CM ของโรงพยาบาล
"""
import re

from . import util
from .ctx import Ctx, major_vessel
from .scoring import evaluate

BLANK = "[____]"
HEADER = ("ร่าง Addendum — แพทย์ผู้ทำหัตถการต้องตรวจทาน แก้ไข และลงนามก่อนบันทึก "
          "(ระบบไม่เขียนลงเวชระเบียนอัตโนมัติ)")

# {field.key} ถูกแทนด้วยค่าจาก field ถ้ามี ไม่มีให้เป็น [____]
PHRASES = {
    "R01": "Heart Team: ผู้ป่วยได้รับการปรึกษาร่วมกับศัลยแพทย์หัวใจ {decision.heart_team_cvt} เมื่อวันที่ {decision.heart_team_date} "
           "ข้อสรุป: {decision.heart_team_recommendation} เหตุผล: [____]",
    "R02": "ระหว่างหัตถการมีการใช้ {qc.vasoactive}{qc.mcs_type} เวลา {qc.vasoactive_time} BP ขณะนั้น {qc.vasoactive_bp} "
           "เหตุผล: {qc.vasoactive_reason} ลักษณะ: prophylactic / bailout (เลือก 1) เวลาหยุด: [____]",
    "R03": "ขอแก้ไขการวินิจฉัยให้ตรงกัน: Syndrome = [____] (header: {clinical.header_syndrome}; DAPT: {clinical.dapt_text_syndrome}) "
           "เหตุผลที่ใช้ syndrome นี้: [____]",
    "R04": "วันที่เริ่มมีอาการ (onset) = {clinical.onset_date} วันที่ทำหัตถการ = {clinical.procedure_date} "
           "ขอยืนยัน/แก้ไข syndrome และเหตุผลการจัดเป็น ACS: [____]",
    "R05": "ข้อบ่งชี้ PCI ใน CCS: FFR/iFR = {ischemia.ffr}/{ischemia.ifr_rfr} เหตุผลที่ทำ PCI (เช่น refractory angina ภายใต้ OMT: {clinical.omt_list}) : [____]",
    "R06": "ขอตรวจสอบจำนวนอุปกรณ์: stent = {summary.stent_count}, DCB = {summary.dcb_count} เทียบกับ device log/sticker; จำนวนที่ถูกต้อง: [____]",
    "R07": "ขอแก้ชื่อ/ขนาดอุปกรณ์ให้ตรงกับ sticker/UDI: อุปกรณ์ที่ใช้จริง = [____] ขนาด [____]",
    "R08": "ขอบันทึกรายละเอียดเฉพาะของแต่ละ lesion: ตำแหน่ง [____] เส้นผ่านศูนย์กลาง/ความยาว [____] อุปกรณ์ [____] ผลหลังทำ [____]",
    "R09": "การทำ PCI แบบ ad hoc: เหตุผล [____] การปรึกษา/ความยินยอมของผู้ป่วยก่อนทำ [____] แผนสำหรับรอยโรคที่เหลือ [____]",
    "R10": "SYNTAX score = {decision.syntax_score} องค์ประกอบ: {summary.syntax_components} (คำนวณซ้ำด้วย calculator มาตรฐาน: [____])",
    "R11": "LM {anatomy.lm_stenosis_pct}%: ก่อนทำ PCI ใช้ IVUS MLA = [____] mm² / FFR-iFR = [____] เป็นเกณฑ์ตัดสินใจ",
    "R12": "ผลหัตถการ: [ล้มเหลว / สำเร็จบางส่วน] สาเหตุ: [____] แผนต่อไป: [____]",
    "R13": "STEMI timeline: onset {clinical.onset_date}, FMC {clinical.fmc_time}, door {clinical.door_time}, wire crossing {clinical.wire_time}",
    "Y01": "Imaging หลัง PCI: MSA = [____] mm², % expansion = [____], เกณฑ์ที่ใช้ = [____], edge dissection = [____]",
    "Y02": "DCB: residual stenosis หลัง prep = [____]%, dissection grade = [____], เหตุผลเลือก DCB = [____]",
    "Y03": "เหตุผลการใช้อุปกรณ์ HBR: [____] (เกณฑ์ ARC-HBR: major/minor [____]) แผน DAPT = {plan.dapt_agents} ระยะเวลา {plan.dapt_duration_months} เดือน",
    "Y04": "DAPT ระยะเวลา {plan.dapt_duration_months} เดือนใน CCS เหตุผล: [____]",
    "Y05": "ขอแก้ label การวินิจฉัย: [____] (หลอดเลือดที่เข้าเกณฑ์ ≥70% และ ≥2.0 mm: [____])",
    "Y06": "ระยะเวลาการอุดตัน (duration) ของ CTO = [____] เดือน หลักฐาน: [____]",
    "Y07": "Echo ล่าสุด วันที่ {echo.date} LVEF {echo.lvef}% RWMA: {echo.rwma_territory}",
    "Y08": "Thrombus grade = [____] เหตุผลที่ทำ thrombectomy: [____]",
    "Y09": "Post-dilation ด้วย balloon ขนาด [____] mm (limit ตาม IFU [____] mm) เหตุผล/การยืนยันด้วย imaging: [____]",
    "Y10": "ยา OAC/NOAC: {plan.oac_name} ขนาด/เวลาที่ได้รับครั้งสุดท้าย: {plan.oac_last_dose}",
    "Y11": "ACT ระหว่างหัตถการ: [____] (เวลา [____]) heparin เพิ่ม [____] U",
    "Y12": "หัตถการที่ทำจริง: [____] (LHC / IVUS-OCT / atherectomy / bifurcation / thrombectomy)",
    "G01": "Contrast {qc.contrast_ml} mL, Air Kerma {qc.air_kerma_gy} Gy, DAP {qc.dap}, fluoro {qc.fluoro_min} min, hemostasis {qc.hemostasis}",
}
GENERIC = "ขอเพิ่มเติมบันทึก: {label} = [____] (หรือระบุ N/A พร้อมเหตุผล)"


def _fill(tpl, ctx):
    def sub(m):
        v = ctx.v(m.group(1))
        if isinstance(v, list):
            v = ", ".join(map(str, v))
        return BLANK if v is None else str(v)
    return re.sub(r"\{([a-z_]+\.[a-z0-9_]+)\}", sub, tpl)


def addendum(case, evaluation=None):
    """ร่าง addendum ต่อ rule (รวม instance ของ rule เดียวกันเป็นข้อเดียว) เฉพาะ finding ที่ยังเป็นผลจริง"""
    ev = evaluation or evaluate(case)
    ctx = Ctx(case)
    items, seen = [], set()
    for f in ev["findings"]:
        if f["overridden"] or f["needs_review"]:
            continue
        base = f["rule_id"].split("@")[0]
        if f["kind"] == "completeness":
            label = f["title"].split("ขาด", 1)[-1].strip()
            text = GENERIC.format(label=label)
            key = f["rule_id"]
        elif base in PHRASES:
            text, key = _fill(PHRASES[base], ctx), base
        else:
            continue
        if key in seen:
            continue
        seen.add(key)
        items.append({"rule_id": f["rule_id"], "severity": f["severity"], "text": text,
                      "needs_input": text.count(BLANK), "why": f["message"]})
    return {"header": HEADER, "items": items}


# ---------------------------------------------------------------- coding check
DES_HINT = re.compile(r"des|limus|xience|resolute|ultimaster|synergy|orsiro|onyx|biofreedom|promus|element", re.I)


def _norm_code(c):
    return str(c).strip().upper().replace(" ", "")


def suggest_codes(ctx):
    icd10, icd9 = [], []
    syn = ctx.syndrome()
    cv = ctx.v("clinical.culprit_vessel") or ""
    if syn == "STEMI":
        mv = major_vessel(cv)
        code = {"LAD": ("I21.0", "STEMI ผนังหน้า (culprit LAD)"), "RCA": ("I21.1", "STEMI ผนังล่าง (culprit RCA)"),
                "LCX": ("I21.2", "STEMI ตำแหน่งอื่น (culprit LCx)")}.get(mv)
        icd10.append(code and {"code": code[0], "desc": code[1], "reason": f"syndrome=STEMI, culprit={cv}"} or
                     {"code": "I21.3", "desc": "STEMI ไม่ระบุตำแหน่ง", "reason": "STEMI แต่ไม่ระบุ culprit — coder ตรวจ"})
    elif syn == "NSTEMI":
        icd10.append({"code": "I21.4", "desc": "NSTEMI", "reason": "syndrome=NSTEMI"})
    elif syn == "UA":
        icd10.append({"code": "I20.0", "desc": "Unstable angina", "reason": "syndrome=UA"})
    elif syn == "CCS":
        icd10.append({"code": "I25.1", "desc": "Atherosclerotic heart disease (CCS)", "reason": "syndrome=CCS"})

    treated = ctx.treated_lesions()
    stents = [l for l in treated if l.has("stent_brand")]
    vessels = {major_vessel(l.v("vessel") or l.v("name")) or l.id for l in treated}
    if ctx.boolean("qc.lhc_performed"):
        icd9.append({"code": "37.22", "desc": "Left heart catheterization", "reason": "ทำ LHC/coronary angiography (เลือกรหัส 88.5x ตามจำนวน catheter)"})
    if stents:
        des = any(DES_HINT.search(str(l.v("stent_brand"))) for l in stents)
        icd9.append({"code": "36.07" if des else "36.06", "desc": "Insertion of drug-eluting coronary stent" if des else "Insertion of non-drug-eluting coronary stent",
                     "reason": "จาก brand ใน lesion — coder ตรวจชนิด stent"})
    if any(l.boolean("dcb") for l in treated) or (treated and not stents):
        icd9.append({"code": "00.66", "desc": "PTCA or coronary atherectomy", "reason": "balloon/DCB angioplasty"})
    if vessels:
        n = min(len(vessels), 4)
        icd9.append({"code": f"00.4{n - 1}", "desc": f"Procedure on {n if n < 4 else '4 or more'} vessel(s)", "reason": f"treated vessels: {', '.join(sorted(vessels))}"})
    if stents:
        n = min(len(stents), 4)
        icd9.append({"code": f"00.4{4 + n}", "desc": f"Insertion of {n if n < 4 else '4 or more'} vascular stent(s)", "reason": f"จำนวน lesion ที่ใส่ stent = {len(stents)} (ตรวจกับจำนวน stent จริง)"})
    if "Bifurcation" in ctx.case_types:
        icd9.append({"code": "00.44", "desc": "Procedure on vessel bifurcation", "reason": "case type = Bifurcation"})
    if "Atherectomy" in ctx.case_types:
        icd9.append({"code": "17.55", "desc": "Transluminal coronary atherectomy", "reason": "มี rota/OAS/IVL"})
    if re.search(r"iabp", str(ctx.v("qc.mcs_type") or "") + " ".join(ctx.lst("qc.vasoactive")), re.I):
        icd9.append({"code": "37.61", "desc": "Implant of pulsation balloon (IABP)", "reason": "ใช้ IABP"})
    if any(re.search(r"ivus|oct", str(l.v("imaging_used") or ""), re.I) for l in treated):
        icd9.append({"code": "00.24", "desc": "Intravascular imaging of coronary vessels", "reason": "ใช้ IVUS/OCT"})
    return icd10, icd9


def _match(code, pool):
    c = _norm_code(code)
    return any(c.startswith(p) or p.startswith(c) for p in map(_norm_code, pool))


def coding_check(case):
    ctx = Ctx(case)
    s10, s9 = suggest_codes(ctx)
    sub10, sub9 = ctx.lst("coding.icd10"), ctx.lst("coding.icd9cm")
    report = {
        "note": "ข้อเสนอเท่านั้น — coder ต้องยืนยันตามมาตรฐาน ICD-10-TM/ICD-9-CM ของโรงพยาบาล",
        "icd10": [dict(s, status="ส่งแล้ว" if _match(s["code"], sub10) else "ยังไม่พบในที่ส่ง") for s in s10],
        "icd9cm": [dict(s, status="ส่งแล้ว" if _match(s["code"], sub9) else "ยังไม่พบในที่ส่ง") for s in s9],
        "submitted": {"icd10": sub10, "icd9cm": sub9},
        "extra_submitted": {"icd10": [c for c in sub10 if not _match(c, [s["code"] for s in s10])],
                            "icd9cm": [c for c in sub9 if not _match(c, [s["code"] for s in s9])]},
    }
    table = {}
    for d in ctx.devices:
        cat = d.get("category") or "other"
        table.setdefault(cat, {"narrative": 0, "log": 0, "sticker": 0})
        if d.get("source") in table[cat]:
            table[cat][d["source"]] += 1
    report["devices"] = [{"category": k, **v, "consistent": len({v["log"], v["sticker"]}) == 1 or not (v["log"] and v["sticker"])}
                         for k, v in sorted(table.items())]
    return report
