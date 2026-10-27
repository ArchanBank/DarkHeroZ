"""Rule engine: R01–R13 (แดง), Y01–Y12 (เหลือง), G01 (เขียว) + completeness ตาม case type

ทุก rule ระบุ: ID, condition, severity, domain, guideline reference, ข้อความแนะนำการแก้
rule ทำงานบน structured field เท่านั้น (ไม่ใช้คะแนนจากโมเดลแบบ black box)
Rule ที่ต้องตีความทางคลินิกจะคืน needs_review=True เพื่อส่งให้ผู้ตรวจ ไม่ตัดสินเอง
"""
import difflib
import re
from collections import Counter

from . import util
from .ctx import Ctx, Finding, major_vessel, normalize_syndrome

RULES = []

G_REVASC21 = "2021 ACC/AHA/SCAI Coronary Revascularization"
G_ESC18 = "2018 ESC/EACTS Myocardial Revascularization"
G_ACS23 = "2023 ESC ACS"
G_CCS23 = "2023 ESC CCS"
G_HBR = "ARC-HBR 2019 / 2017 ESC DAPT focused update"
G_INT = "Consensus ภายในโรงพยาบาล (rule owner ต้อง validate)"


def hit(message, evidence=(), fix=None, review=False, detail=None):
    return {"message": message, "evidence": list(evidence), "fix": fix, "review": review, "detail": detail}


def rule(rid, severity, domain, title, guideline, fix, criteria, kind="consistency"):
    def deco(fn):
        RULES.append({"id": rid, "severity": severity, "domain": domain, "title": title,
                      "guideline": guideline, "fix": fix, "criteria": criteria,
                      "kind": kind, "fn": fn})
        return fn
    return deco


def _lv(les, key):
    return f"{les.id}:{key}"


# ---------------------------------------------------------------- แดง
@rule("R01", "red", "Strategy", "LM / multivessel / CTO ไม่มี Heart Team note (วันที่ + ชื่อ CVT)",
      f"{G_REVASC21} (Heart Team, Class 1); {G_ESC18}",
      "เพิ่ม Heart Team block: วันที่ปรึกษา, ชื่อศัลยแพทย์ CVT, ข้อสรุป และเหตุผล", ["2.1"])
def r01(c):
    if not (c.has_lm or c.mvd or "CTO" in c.case_types):
        return
    if c.syndrome() == "STEMI":
        c.skip("R01", "STEMI: Heart Team = N/A ตามตาราง case type")
        return
    miss = [lbl for k, lbl in (("decision.heart_team_date", "วันที่"), ("decision.heart_team_cvt", "ชื่อ CVT"))
            if not c.has(k)]
    if miss:
        trig = "LM" if c.has_lm else ("MVD" if c.mvd else "CTO")
        return [hit(f"เคส {trig} ไม่พบ Heart Team note ที่ครบ (ขาด: {', '.join(miss)})",
                    ["decision.heart_team_date", "decision.heart_team_cvt"])]


@rule("R02", "red", "Technical", "ใช้ vasopressor/inotrope/MCS แต่ไม่มีเหตุผลหรือ BP",
      G_INT, "ระบุเหตุผล, BP ขณะให้ยา, เวลา, และ prophylactic/bailout", ["3.5"])
def r02(c):
    used = c.lst("qc.vasoactive") or c.has("qc.mcs_type")
    if not used:
        return
    miss = []
    if not c.has("qc.vasoactive_reason"):
        miss.append("เหตุผล")
    if not c.has("qc.vasoactive_bp"):
        miss.append("BP")
    if miss:
        what = ", ".join(c.lst("qc.vasoactive")) or str(c.v("qc.mcs_type"))
        return [hit(f"ใช้ {what} แต่ไม่พบ{' และ '.join(miss)}",
                    ["qc.vasoactive", "qc.mcs_type", "qc.vasoactive_reason", "qc.vasoactive_bp"])]


@rule("R03", "red", "Insurance", "Syndrome ใน header/indication ไม่ตรงกับข้อความ DAPT หรือ diagnosis",
      G_ACS23 + "; เกณฑ์เบิกจ่าย", "แก้ให้ syndrome ตรงกันทุกส่วน หรือระบุเหตุผลที่แตกต่าง", ["4.1"])
def r03(c):
    keys = ["clinical.header_syndrome", "clinical.narrative_syndrome", "clinical.dapt_text_syndrome", "clinical.syndrome"]
    vals = {k: normalize_syndrome(c.v(k)) for k in keys if normalize_syndrome(c.v(k))}
    specific = {k: v for k, v in vals.items() if v != "ACS"}
    conflict = len(set(specific.values())) > 1
    if not conflict and "ACS" in vals.values() and "CCS" in specific.values():
        conflict = True
    if conflict:
        txt = "; ".join(f"{k.split('.')[1]}={c.v(k)}" for k in vals)
        return [hit(f"Syndrome ไม่ตรงกัน: {txt}", list(vals))]


@rule("R04", "red", "Insurance", "STEMI/NSTEMI แต่ onset มากกว่า 28 วันก่อนทำหัตถการ",
      f"{G_ACS23}; เกณฑ์เบิก 28-day rule", "ทบทวน syndrome (อาจเป็น CCS/recent MI) หรือแก้วันที่ onset ให้ถูกต้อง", ["4.1", "4.3"])
def r04(c):
    if c.syndrome() not in ("STEMI", "NSTEMI"):
        return
    on, pr = c.date("clinical.onset_date"), c.date("clinical.procedure_date")
    if on and pr:
        days = (pr - on).days
        if days > c.cfg["onset_max_days"]:
            return [hit(f"Coded {c.syndrome()} แต่ onset {on} → หัตถการ {pr} ห่างกัน {days} วัน (> {c.cfg['onset_max_days']})",
                        ["clinical.onset_date", "clinical.procedure_date", "clinical.syndrome"])]
        if days < 0:
            return [hit("วันที่ onset ภายหลังวันทำหัตถการ — ตรวจความถูกต้องของวันที่",
                        ["clinical.onset_date", "clinical.procedure_date"])]


@rule("R05", "red", "Indication", "CCS + PCI โดย FFR >0.80 หรือ iFR/RFR >0.89 และไม่มี refractory-angina rationale",
      f"{G_CCS23}; {G_REVASC21}", "เพิ่มเหตุผล refractory angina / ข้อบ่งชี้อื่น หรือทบทวนข้อบ่งชี้การทำ PCI", ["1.2"])
def r05(c):
    if c.syndrome() != "CCS" or not c.treated_lesions() or c.has("ischemia.refractory_rationale"):
        return
    out = []
    for l in c.treated_lesions():
        ffr = l.num("ffr") if l.num("ffr") is not None else c.num("ischemia.ffr")
        ifr = l.num("ifr_rfr") if l.num("ifr_rfr") is not None else c.num("ischemia.ifr_rfr")
        if (ffr is not None and ffr > c.cfg["ffr_nonsignificant_above"]) or \
           (ifr is not None and ifr > c.cfg["ifr_rfr_nonsignificant_above"]):
            out.append(hit(f"{l.label}: physiology ไม่ significant (FFR={ffr}, iFR/RFR={ifr}) แต่ทำ PCI โดยไม่มี refractory rationale",
                           ["ischemia.ffr", "ischemia.ifr_rfr", _lv(l, "ffr"), _lv(l, "ifr_rfr"), "ischemia.refractory_rationale"]))
    return out


def _count_dev(c, source, cat):
    return sum(1 for d in c.devices if d.get("source") == source and d.get("category") == cat)


@rule("R06", "red", "Insurance", "จำนวน stent/DCB ใน summary ไม่ตรงกับ device list หรือ sticker",
      "เกณฑ์เบิกจ่าย / ICD-9-CM 00.45–00.48", "ตรวจนับอุปกรณ์จริงและแก้ summary ให้ตรงกับ log และ sticker", ["4.4", "4.2"])
def r06(c):
    out = []
    for cat, key, label in (("stent", "summary.stent_count", "stent"), ("dcb", "summary.dcb_count", "DCB")):
        s = c.num(key)
        if s is None:
            continue
        for src in ("log", "sticker"):
            if any(d.get("source") == src for d in c.devices) or any(d.get("source") == src and d.get("category") == cat for d in c.devices):
                n = _count_dev(c, src, cat)
                if n != int(s):
                    out.append(hit(f"จำนวน {label} ใน summary = {int(s)} แต่ {src} = {n}", [key],
                                   detail={"summary": int(s), "source": src, "count": n}))
    return out


def _name_compat(a, b):
    a, b = util.squash(a), util.squash(b)
    return bool(a and b and (a in b or b in a or difflib.SequenceMatcher(None, a, b).ratio() >= 0.8))


@rule("R07", "red", "Technical", "ชื่อ/ขนาดอุปกรณ์ใน narrative ไม่ตรงกับ log หรือ sticker (เช่น Rota กับ OAS)",
      G_INT, "ตรวจชื่อ/ขนาดจาก barcode/UDI sticker แล้วแก้ narrative หรือ log ให้ตรงกัน", ["3.1"])
def r07(c):
    groups = {}
    for d in c.devices:
        if d.get("slot"):
            groups.setdefault(d["slot"], []).append(d)
    out = []
    for slot, ds in groups.items():
        by_src = {d["source"]: d for d in ds if d.get("source")}
        srcs = list(by_src)
        for i in range(len(srcs)):
            for j in range(i + 1, len(srcs)):
                a, b = by_src[srcs[i]], by_src[srcs[j]]
                if a.get("name") and b.get("name") and not _name_compat(a["name"], b["name"]):
                    out.append(hit(f"อุปกรณ์ {slot}: {srcs[i]}='{a['name']}' ≠ {srcs[j]}='{b['name']}'", [],
                                   detail={"slot": slot}))
                sa, sb = util.norm_size(a.get("size")), util.norm_size(b.get("size"))
                if sa and sb and sa != sb:
                    out.append(hit(f"ขนาดอุปกรณ์ {slot}: {srcs[i]}={a['size']} ≠ {srcs[j]}={b['size']}", [],
                                   detail={"slot": slot}))
    return out


@rule("R08", "red", "Technical", "ข้อความ lesion section ซ้ำกับ lesion อื่นเกินเกณฑ์ similarity (copy-paste)",
      G_INT, "เขียนรายละเอียดเฉพาะของแต่ละ lesion (ขนาด, ตำแหน่ง, อุปกรณ์, ผล) แทนการ copy-paste", ["3.2"])
def r08(c):
    texts = [(l, util.norm_text(l.v("narrative_text"))) for l in c.lesions]
    texts = [(l, t) for l, t in texts if len(t) >= c.cfg["copy_paste_min_chars"]]
    out = []
    for i in range(len(texts)):
        for j in range(i + 1, len(texts)):
            r = difflib.SequenceMatcher(None, texts[i][1], texts[j][1]).ratio()
            if r >= c.cfg["copy_paste_similarity"]:
                out.append(hit(f"ข้อความ {texts[i][0].label} และ {texts[j][0].label} เหมือนกัน {r:.0%} (เกณฑ์ {c.cfg['copy_paste_similarity']:.0%})",
                               [_lv(texts[i][0], "narrative_text"), _lv(texts[j][0], "narrative_text")],
                               detail={"similarity": round(r, 3)}))
    return out


@rule("R09", "red", "Strategy", "Ad hoc PCI ใน multivessel หรือ DM+MVD โดยไม่มี pre-consent หรือ Heart Team",
      f"{G_REVASC21}; {G_ESC18}", "บันทึกเหตุผล ad hoc, consent ล่วงหน้า หรือ Heart Team ก่อนทำ PCI หลายหลอดเลือด", ["2.3", "2.4"])
def r09(c):
    if not c.mvd:
        return
    adhoc = "adhoc" in util.squash(c.v("decision.adhoc_or_staged") or "")
    if adhoc and not c.boolean("decision.pre_consent") and not (c.has("decision.heart_team_date") or c.has("decision.heart_team_cvt")):
        tag = "DM+MVD" if "DM+MVD" in c.case_types else "MVD"
        return [hit(f"Ad hoc PCI ใน {tag} โดยไม่พบทั้ง pre-consent และ Heart Team",
                    ["decision.adhoc_or_staged", "decision.pre_consent", "decision.heart_team_date"])]


@rule("R10", "red", "Strategy", "SYNTAX คำนวณผิดกฎ (modifier นับซ้ำ) หรือไม่มี SYNTAX ใน LM/MVD",
      f"{G_ESC18} (SYNTAX score)", "คำนวณ SYNTAX ใหม่ด้วย calculator มาตรฐาน และบันทึกองค์ประกอบ", ["2.2"])
def r10(c):
    if not (c.has_lm or c.mvd):
        return
    out = []
    if not c.has("decision.syntax_score"):
        out.append(hit("LM/MVD แต่ไม่มี SYNTAX score", ["decision.syntax_score"]))
    comps = [util.norm_text(x) for x in c.lst("summary.syntax_components")]
    if comps:
        dup = [k for k, n in Counter(comps).items() if n > 1]
        if dup:
            out.append(hit(f"องค์ประกอบ/modifier SYNTAX ถูกนับซ้ำ: {', '.join(dup)}", ["summary.syntax_components"]))
    elif c.has("decision.syntax_score"):
        c.skip("R10", "มี SYNTAX score แต่ไม่มีองค์ประกอบให้ตรวจการคำนวณ — ให้ผู้ตรวจเทียบกับรายงานเอง")
    return out


@rule("R11", "red", "Indication", "Intermediate LM (50–70%) ได้รับการรักษาโดยไม่มี IVUS MLA หรือ physiology ก่อนทำ",
      f"{G_REVASC21}; {G_ESC18} (LM imaging/physiology)", "บันทึก IVUS MLA (หรือ FFR/iFR) ก่อนทำ หรือเหตุผลที่ทำไม่ได้", ["1.2"])
def r11(c):
    out = []
    for l in c.lesions:
        pct = l.num("stenosis_pct")
        if l.is_lm and pct is not None and 50 <= pct <= c.cfg["lm_intermediate_max_pct"] and l in c.treated_lesions():
            if not (l.has("pre_mla") or l.has("ffr") or l.has("ifr_rfr")):
                out.append(hit(f"{l.label}: LM {pct:.0f}% ทำ PCI โดยไม่มี MLA/physiology ก่อนทำ",
                               [_lv(l, "stenosis_pct"), _lv(l, "pre_mla"), _lv(l, "ffr")]))
    return out


FAIL_WORDS = ("fail", "unsuccess", "ล้มเหลว", "ไม่สำเร็จ", "ไม่สามารถ", "abort", "unable")


@rule("R12", "red", "Insurance", "หัตถการล้มเหลว (เช่น CTO) ไม่อยู่ใน conclusion",
      G_INT + "; เกณฑ์เบิกจ่าย", "ระบุผลหัตถการ (ล้มเหลว/สำเร็จบางส่วน) และเหตุผลใน conclusion", ["4.5"])
def r12(c):
    failed = c.boolean("summary.procedure_success") is False or any(
        util.squash(l.v("result") or "").startswith(("fail", "ไม่สำเร็จ", "ล้มเหลว")) for l in c.lesions)
    if not failed:
        return
    concl = str(c.v("summary.conclusion_text") or "").lower()
    if not any(w in concl for w in FAIL_WORDS):
        return [hit("พบหัตถการ/lesion ที่ล้มเหลว แต่ conclusion ไม่ระบุ", ["summary.procedure_success", "summary.conclusion_text"])]


@rule("R13", "red", "Technical", "STEMI ไม่มี D2B / onset / FMC",
      f"{G_ACS23} (door-to-balloon, system delay)", "บันทึก onset, FMC, door time และ wire crossing time", ["3.5"])
def r13(c):
    if c.syndrome() != "STEMI":
        return
    miss = []
    if not c.has("clinical.onset_date"):
        miss.append("onset")
    if not c.has("clinical.fmc_time"):
        miss.append("FMC")
    if c.d2b() is None:
        miss.append("D2B (door/wire time)")
    if miss:
        return [hit(f"STEMI ไม่มี {', '.join(miss)}",
                    ["clinical.onset_date", "clinical.fmc_time", "clinical.door_time", "clinical.wire_time"])]


# ---------------------------------------------------------------- เหลือง
@rule("Y01", "yellow", "Technical", "ใช้ imaging แต่ไม่มีค่า MSA/expansion หลัง PCI",
      f"{G_REVASC21} (IVUS/OCT guidance)", "เพิ่ม MSA, % expansion และเกณฑ์ที่ใช้ต่อ lesion", ["3.3"])
def y01(c):
    out = []
    for l in c.treated_lesions():
        img = util.norm_text(l.v("imaging_used") or "")
        if img and img != "none" and not (l.has("post_msa") and l.has("post_expansion_pct")):
            miss = [lbl for k, lbl in (("post_msa", "MSA"), ("post_expansion_pct", "% expansion")) if not l.has(k)]
            out.append(hit(f"{l.label}: ใช้ {l.v('imaging_used')} แต่ไม่มี {', '.join(miss)}",
                           [_lv(l, "imaging_used"), _lv(l, "post_msa"), _lv(l, "post_expansion_pct")]))
    return out


@rule("Y02", "yellow", "Technical", "DCB ไม่มี residual stenosis/dissection grade หลัง prep หรือ dissection ≥C โดยไม่มีเหตุผล",
      "Consensus DCB (International DCB Consensus 2020) / IFU", "บันทึก residual ≤30%, dissection grade หลัง prep และเหตุผลหากเป็น ≥C", ["3.2"])
def y02(c):
    out = []
    for l in c.lesions:
        if not l.boolean("dcb"):
            continue
        miss = []
        if not l.has("residual_after_prep_pct"):
            miss.append("residual หลัง prep")
        if not l.has("dissection_after_prep"):
            miss.append("dissection grade หลัง prep")
        if miss:
            out.append(hit(f"{l.label}: DCB ไม่มี {', '.join(miss)}", [_lv(l, "residual_after_prep_pct"), _lv(l, "dissection_after_prep")]))
        g = str(l.v("dissection_after_prep") or "").strip().upper()[:1]
        if g in "CDEF" and g and not l.has("dissection_rationale"):
            out.append(hit(f"{l.label}: dissection type {g} โดยไม่มีเหตุผลรองรับการใช้ DCB", [_lv(l, "dissection_after_prep"), _lv(l, "dissection_rationale")]))
        r = l.num("residual_after_prep_pct")
        if r is not None and r > c.cfg["dcb_max_residual_pct"]:
            out.append(hit(f"{l.label}: residual หลัง prep {r:.0f}% เกิน {c.cfg['dcb_max_residual_pct']}% ที่ใช้เป็นเกณฑ์ DCB", [_lv(l, "residual_after_prep_pct")]))
    return out


def _is_hbr_device(c, l):
    if l.boolean("hbr_device"):
        return True
    brand = util.norm_text(l.v("stent_brand") or "")
    if brand and any(h in brand for h in map(util.norm_text, c.lookups.get("hbr_devices", []))):
        return True
    return bool(l.boolean("dcb") and re.search(r"hbr|bleed|เลือดออก", str(l.v("dcb_rationale") or ""), re.I))


@rule("Y03", "yellow", "Strategy", "HBR stent (เช่น BioFreedom) หรือ DCB-for-HBR แต่ DAPT ไม่สอดคล้องกับ HBR",
      G_HBR, "ปรับแผน DAPT (สั้น 1–3 เดือน) ให้สอดคล้อง หรือระบุเหตุผลที่ใช้อุปกรณ์ HBR", ["2.5"])
def y03(c):
    devs = [l for l in c.treated_lesions() if _is_hbr_device(c, l)]
    if not devs:
        return
    out = []
    if not c.is_hbr:
        out.append(hit("ใช้อุปกรณ์ HBR แต่ข้อมูลผู้ป่วยไม่เข้าเกณฑ์ ARC-HBR (คำนวณจาก field)",
                       ["patient.age", "patient.egfr", "patient.hb", "clinical.af_oac"], review=True))
    d = c.num("plan.dapt_duration_months")
    if d is None:
        out.append(hit("ใช้อุปกรณ์ HBR แต่ไม่ระบุระยะเวลา DAPT", ["plan.dapt_duration_months"]))
    elif d > c.cfg["hbr_max_dapt_months"] and not c.has("plan.dapt_reason"):
        out.append(hit(f"อุปกรณ์/ผู้ป่วย HBR แต่ DAPT {d:.0f} เดือน (> {c.cfg['hbr_max_dapt_months']}) โดยไม่มีเหตุผล",
                       ["plan.dapt_duration_months", "plan.dapt_reason"]))
    return out


@rule("Y04", "yellow", "Strategy", "DAPT 12 เดือนใน CCS โดยไม่มีเหตุผล",
      G_CCS23, "ระบุเหตุผลหรือปรับเป็น DAPT 6 เดือน (default CCS)", ["2.5"])
def y04(c):
    d = c.num("plan.dapt_duration_months")
    if c.syndrome() == "CCS" and d is not None and d >= 12 and not c.has("plan.dapt_reason"):
        return [hit(f"CCS แต่ DAPT {d:.0f} เดือน โดยไม่มีเหตุผล", ["plan.dapt_duration_months", "plan.dapt_reason"])]


@rule("Y05", "yellow", "Indication", "ระบุ TVD/2VD แต่ vessel/branch ไม่เข้าเกณฑ์ (≥70%, ≥2.0 mm)",
      G_INT, "ตรวจ label และ stenosis/ขนาด vessel ให้สอดคล้อง", ["1.5"])
def y05(c):
    label = str(c.v("anatomy.diagnosis_label") or "")
    m = re.search(r"([123])\s*[- ]?\s*v(?:essel)?\s*d|\b(tvd|dvd|svd)\b", label, re.I)
    if not m:
        return
    need = int(m.group(1)) if m.group(1) else {"tvd": 3, "dvd": 2, "svd": 1}[m.group(2).lower()]
    strict, lenient = set(), set()
    for l in c.lesions:
        vs, pct, sz = major_vessel(l.v("vessel") or l.v("name")), l.num("stenosis_pct"), l.num("vessel_size_mm")
        if vs and vs != "LM" and pct is not None and pct >= c.cfg["tvd_min_stenosis_pct"]:
            if sz is None:
                lenient.add(vs)
            elif sz >= c.cfg["tvd_min_vessel_mm"]:
                strict.add(vs); lenient.add(vs)
    if len(lenient) < need:
        return [hit(f"Label '{label}' ต้องการ {need} หลอดเลือด แต่เข้าเกณฑ์ (≥{c.cfg['tvd_min_stenosis_pct']}%, ≥{c.cfg['tvd_min_vessel_mm']} mm) เพียง {len(lenient)}",
                    ["anatomy.diagnosis_label"])]
    if len(strict) < need:
        return [hit(f"Label '{label}' — บาง vessel ไม่ระบุขนาด จึงยืนยันเกณฑ์ ≥{c.cfg['tvd_min_vessel_mm']} mm ไม่ได้", ["anatomy.diagnosis_label"], review=True)]


@rule("Y06", "yellow", "Indication", "label CTO แต่ duration <3 เดือน",
      "J-CTO / EuroCTO definition (occlusion ≥3 เดือน)", "ตรวจ duration หรือแก้ label เป็น recent/subacute occlusion", ["1.4"])
def y06(c):
    out = []
    for l in c.lesions:
        d = l.num("cto_duration_months")
        if l.boolean("cto") and d is not None and d < c.cfg["cto_min_months"]:
            out.append(hit(f"{l.label}: label CTO แต่ duration {d:g} เดือน (<{c.cfg['cto_min_months']})", [_lv(l, "cto"), _lv(l, "cto_duration_months")]))
    return out


@rule("Y07", "yellow", "Indication", "Echo >6 เดือน หรือ echo field ว่าง",
      G_INT, "ทำ/บันทึก Echo ล่าสุด (≤6 เดือน) พร้อม LVEF และ RWMA", ["1.3"])
def y07(c):
    ed, pr = c.date("echo.date"), c.date("clinical.procedure_date")
    if not c.has("echo.lvef") or not ed:
        return [hit("ไม่พบข้อมูล Echo (วันที่/LVEF)", ["echo.date", "echo.lvef"])]
    if pr and (pr - ed).days / 30.4375 > c.cfg["echo_max_months"]:
        return [hit(f"Echo ห่างจากวันทำหัตถการ {(pr - ed).days / 30.4375:.1f} เดือน (>{c.cfg['echo_max_months']})", ["echo.date", "clinical.procedure_date"])]


@rule("Y08", "yellow", "Technical", "Routine thrombectomy โดยไม่มี thrombus grade",
      G_ACS23 + " (routine thrombectomy not recommended)", "บันทึก thrombus grade (TIMI) และเหตุผลที่ทำ thrombectomy", ["3.2"])
def y08(c):
    if c.boolean("qc.thrombectomy") and not (c.has("qc.thrombus_grade") or any(l.has("thrombus_grade") for l in c.lesions)):
        return [hit("ทำ thrombectomy แต่ไม่มี thrombus grade", ["qc.thrombectomy", "qc.thrombus_grade"])]


def _overexp_limit(c, brand):
    b = util.norm_text(brand)
    for e in c.lookups.get("overexpansion_limits_mm", []):
        if not e.get("example") and util.norm_text(e["match"]) in b:
            return e["max_post_dilation_mm"]
    return None


@rule("Y09", "yellow", "Technical", "Post-dilation เกิน overexpansion limit ตาม IFU (lookup table ต่อรุ่น)",
      "IFU ของผู้ผลิตแต่ละรุ่น (lookup table ที่ admin ดูแล)", "ตรวจขนาด balloon ให้ไม่เกิน limit หรือบันทึกเหตุผลและ imaging ยืนยัน", ["3.2"])
def y09(c):
    real = [e for e in c.lookups.get("overexpansion_limits_mm", []) if not e.get("example")]
    out = []
    for l in c.lesions:
        pd, brand = l.num("post_dilation_balloon_mm"), l.v("stent_brand")
        if pd is None or not brand:
            continue
        if not real:
            c.skip("Y09", "ตาราง overexpansion limit ใน lookups.json ยังไม่มีข้อมูลจริง — ประเมินไม่ได้")
            return
        lim = _overexp_limit(c, brand)
        if lim is None:
            c.skip("Y09", f"ไม่มี limit ของรุ่น '{brand}' ใน lookup table")
        elif pd > lim:
            out.append(hit(f"{l.label}: post-dilate {pd} mm เกิน limit {lim} mm ของ {brand}", [_lv(l, "post_dilation_balloon_mm"), _lv(l, "stent_brand")]))
    return out


@rule("Y10", "yellow", "Indication", "OAC/NOAC ไม่ระบุชื่อยาและ dose สุดท้าย",
      G_HBR, "ระบุชื่อ OAC/NOAC และขนาด/เวลาที่ได้รับครั้งสุดท้าย", ["1.3"])
def y10(c):
    if c.boolean("clinical.af_oac"):
        miss = [lbl for k, lbl in (("plan.oac_name", "ชื่อยา"), ("plan.oac_last_dose", "dose สุดท้าย")) if not c.has(k)]
        if miss:
            return [hit(f"ผู้ป่วยใช้ OAC แต่ไม่ระบุ {', '.join(miss)}", ["plan.oac_name", "plan.oac_last_dose"])]


@rule("Y11", "yellow", "Technical", "หัตถการ >60 นาที หรือ CTO/atherectomy โดยไม่มี ACT ซ้ำ",
      G_INT, "บันทึก ACT ซ้ำ (ทุก 20–30 นาที) และขนาด heparin เพิ่ม", ["3.5"])
def y11(c):
    dur = c.duration_min()
    long_ = (dur is not None and dur > c.cfg["long_procedure_min"]) or "CTO" in c.case_types or "Atherectomy" in c.case_types
    if long_ and len(c.lst("qc.act_values")) < 2:
        why = f"{dur} นาที" if dur and dur > c.cfg["long_procedure_min"] else "/".join(t for t in c.case_types if t in ("CTO", "Atherectomy"))
        return [hit(f"หัตถการซับซ้อน/นาน ({why}) แต่ไม่พบ ACT ซ้ำ", ["qc.act_values", "qc.start_time", "qc.end_time"])]


@rule("Y12", "yellow", "Insurance", "หัตถการใน header รายงานไม่ครบ (LHC, IVUS/OCT, atherectomy, bifurcation, thrombectomy)",
      "ICD-9-CM / เกณฑ์เบิกจ่าย", "เพิ่มหัตถการที่ทำจริงใน header (แนะนำให้สร้างอัตโนมัติจาก log)", ["4.2"])
def y12(c):
    hdr = util.norm_text(" ".join(c.lst("summary.header_procedures")))
    if not hdr:
        return [hit("ไม่พบรายการหัตถการใน header", ["summary.header_procedures"])]
    done = {}
    if c.boolean("qc.lhc_performed"):
        done["LHC"] = ("lhc", "coronary angio", "cag", "left heart")
    if any(re.search(r"ivus|oct", util.norm_text(l.v("imaging_used") or "")) for l in c.lesions):
        done["IVUS/OCT"] = ("ivus", "oct", "imaging")
    if any(l.has("atherectomy_type") for l in c.lesions):
        done["atherectomy"] = ("atherectomy", "rota", "oas", "ivl", "lithotripsy")
    if any(l.has("medina") for l in c.lesions):
        done["bifurcation"] = ("bifurcation", "kbi", "pot", "medina")
    if c.boolean("qc.thrombectomy"):
        done["thrombectomy"] = ("thrombectomy", "aspiration")
    miss = [n for n, kws in done.items() if not any(k in hdr for k in kws)]
    if miss:
        return [hit(f"หัตถการที่ทำจริงแต่ไม่อยู่ใน header: {', '.join(miss)}", ["summary.header_procedures"])]


# ---------------------------------------------------------------- เขียว
@rule("G01", "green", "Technical", "ไม่มี contrast, contrast/eGFR, Air Kerma, DAP หรือ hemostasis",
      "ตัวชี้วัด QC (CIN, radiation safety)", "ดึงค่าอัตโนมัติจากเครื่อง angiography และบันทึก hemostasis", ["3.5"])
def g01(c):
    miss = []
    if not c.has("qc.contrast_ml"):
        miss.append("contrast")
    elif not c.has("patient.egfr"):
        miss.append("eGFR (สำหรับ contrast/eGFR)")
    for k, lbl in (("qc.air_kerma_gy", "Air Kerma"), ("qc.dap", "DAP"), ("qc.hemostasis", "hemostasis")):
        if not c.has(k):
            miss.append(lbl)
    if miss:
        return [hit(f"ไม่พบ: {', '.join(miss)}", ["qc.contrast_ml", "patient.egfr", "qc.air_kerma_gy", "qc.dap", "qc.hemostasis"])]


# ---------------------------------------------------------------- Completeness ตาม case type
# (case_type, scope, key, ป้ายไทย, criterion)   scope: "case" หรือ ชื่อตัวกรอง lesion
REQUIRED = [
    ("ALL", "case", "patient.sex", "เพศ", "1.3"),
    ("ALL", "case", "patient.age", "อายุ", "1.3"),
    ("ALL", "case", "clinical.syndrome", "Syndrome", "1.1"),
    ("ALL", "case", "clinical.procedure_date", "วันที่ทำหัตถการ", "4.3"),
    ("ALL", "case", "plan.dapt_duration_months", "ระยะเวลา DAPT", "2.5"),
    ("ALL", "treated", "vessel_size_mm", "ขนาด vessel", "3.2"),
    ("ALL", "treated", "stenosis_pct", "% stenosis", "1.4"),
    ("ALL", "treated", "residual_pct", "residual stenosis", "3.4"),
    ("ALL", "treated", "timi_post", "TIMI flow หลังทำ", "3.4"),
    ("STEMI", "case", "clinical.door_time", "Door time", "3.5"),
    ("STEMI", "case", "clinical.wire_time", "Wire crossing time", "3.5"),
    ("STEMI", "case", "clinical.ecg_leads", "ECG leads", "1.3"),
    ("STEMI", "case", "clinical.killip", "Killip class", "1.3"),
    ("STEMI", "case", "clinical.loading_dose", "Loading dose", "1.3"),
    ("NSTE-ACS", "case", "patient.troponin", "Troponin", "1.3"),
    ("NSTE-ACS", "case", "clinical.ecg", "ECG", "1.3"),
    ("NSTE-ACS", "case", "clinical.onset_date", "Onset (28-day rule)", "1.1"),
    ("CCS", "case", "clinical.angina_class", "Angina class", "1.1"),
    ("CCS", "case", "clinical.omt_list", "OMT พร้อมรายชื่อยา", "1.3"),
    ("CCS", "case", "ischemia.risk_level", "Ischemia risk level", "1.2"),
    ("CCS", "case", "ischemia.territory", "Ischemia territory", "1.2"),
    ("LM/MVD", "case", "decision.sts_euroscore", "STS/EuroSCORE II", "2.2"),
    ("LM/MVD", "case", "decision.patient_decision", "การตัดสินใจของผู้ป่วย", "2.3"),
    ("LM/MVD", "case", "decision.adhoc_or_staged", "Ad hoc/staged rationale", "2.4"),
    ("LM/MVD", "lm_treated", "imaging_used", "IVUS/OCT สำหรับ LM", "3.3"),
    ("DM+MVD", "case", "decision.cabg_discussion", "CABG discussion (FREEDOM)", "2.1"),
    ("CTO", "cto", "j_cto", "J-CTO score", "1.4"),
    ("CTO", "cto", "collateral", "Collateral", "1.4"),
    ("CTO", "cto", "cto_duration_months", "Duration", "1.4"),
    ("CTO", "case", "decision.strategy", "Strategy", "2.4"),
    ("CTO", "case", "summary.procedure_success", "ผลสำเร็จ/ล้มเหลวใน conclusion", "4.5"),
    ("Bifurcation", "bif", "side_branch_size_mm", "Side branch size", "3.2"),
    ("Bifurcation", "bif", "bifurcation_technique", "เทคนิค bifurcation", "3.2"),
    ("Bifurcation", "bif", "side_branch_result", "ผล side branch", "3.4"),
    ("DCB", "dcb", "prep_device", "Prep device", "3.2"),
    ("DCB", "dcb", "dcb_balloon_vessel_ratio", "Balloon/vessel ratio", "3.2"),
    ("DCB", "dcb", "dcb_inflation_sec", "Inflation time", "3.2"),
    ("DCB", "dcb", "dcb_rationale", "เหตุผลเลือก DCB", "3.2"),
    ("Atherectomy", "ath", "atherectomy_indication", "ข้อบ่งชี้ atherectomy", "3.2"),
    ("Atherectomy", "ath", "atherectomy_size", "Burr/crown size", "3.2"),
    ("Atherectomy", "ath", "atherectomy_runs", "Runs", "3.2"),
    ("HBR", "case", "plan.precise_dapt", "PRECISE-DAPT", "2.5"),
    ("OAC", "case", "plan.triple_therapy_duration", "ระยะ triple therapy", "2.5"),
    ("OAC", "case", "plan.p2y12_agent", "P2Y12 agent", "2.5"),
    ("OAC", "case", "plan.dual_therapy_duration", "ระยะ dual therapy", "2.5"),
    ("Hemodynamic support", "case", "qc.vasoactive_time", "เวลาให้ vasoactive/MCS", "3.5"),
]

DOMAIN_OF_CRITERION = {"1": "Indication", "2": "Strategy", "3": "Technical", "4": "Insurance"}
_LESION_FILTERS = {
    "treated": lambda c, l: l in c.treated_lesions(),
    "lm_treated": lambda c, l: l.is_lm and l in c.treated_lesions(),
    "cto": lambda c, l: bool(l.boolean("cto")) or (l.num("cto_duration_months") or 0) >= c.cfg["cto_min_months"],
    "bif": lambda c, l: l.has("medina") or bool(l.boolean("kbi_pot")),
    "dcb": lambda c, l: bool(l.boolean("dcb")),
    "ath": lambda c, l: l.has("atherectomy_type"),
}
# field ที่ rule อื่นครอบคลุมแล้ว จึงไม่แจ้งซ้ำ
_COVERED = {("STEMI", "clinical.door_time"), ("STEMI", "clinical.wire_time")}


def completeness(c):
    out, seen = [], set()
    for ctype, scope, key, label, crit in REQUIRED:
        if ctype != "ALL" and ctype not in c.case_types:
            continue
        if (ctype, key) in _COVERED:
            continue  # R13 ครอบคลุม
        meta = {"id": f"C-{ctype}-{key.split('.')[-1]}", "severity": "yellow",
                "domain": DOMAIN_OF_CRITERION[crit[0]], "title": f"[{ctype}] ขาด {label}",
                "guideline": "field บังคับตาม case type (ตาราง Case-type logic)", "kind": "completeness",
                "criteria": [crit], "fix": f"บันทึก {label} หรือระบุ N/A พร้อมเหตุผล"}
        if scope == "case":
            if not c.has(key) and not c.is_na(key) and (ctype, key) not in seen:
                seen.add((ctype, key))
                out.append((meta, hit(f"ไม่พบ {label}", [key])))
        else:
            for l in c.lesions:
                if _LESION_FILTERS[scope](c, l) and not l.has(key) and not l.f(key).get("status") == "na":
                    m = dict(meta, id=meta["id"] + f"@{l.id}", title=meta["title"] + f" ({l.label})")
                    out.append((m, hit(f"{l.label}: ไม่พบ {label}", [_lv(l, key)])))
    # CCS + lesion 50–70% ที่ทำ PCI ต้องมี physiology
    if "CCS" in c.case_types:
        for l in c.treated_lesions():
            p = l.num("stenosis_pct")
            if p is not None and 50 <= p <= 70 and not (l.has("ffr") or l.has("ifr_rfr") or l.has("pre_mla") or c.has("ischemia.ffr") or c.has("ischemia.ifr_rfr")):
                meta = {"id": f"C-CCS-physiology@{l.id}", "severity": "yellow", "domain": "Indication",
                        "title": f"[CCS] lesion 50–70% ไม่มี physiology ({l.label})", "guideline": G_CCS23,
                        "kind": "completeness", "criteria": ["1.2"], "fix": "บันทึก FFR/iFR/RFR หรือ IVUS/OCT ก่อนทำ PCI"}
                out.append((meta, hit(f"{l.label}: stenosis {p:.0f}% ไม่มี FFR/iFR/MLA", [_lv(l, "stenosis_pct")])))
    if (c.num("qc.air_kerma_gy") or 0) > c.cfg["air_kerma_followup_gy"] and not c.has("qc.skin_followup_plan"):
        meta = {"id": "C-LONG-skin", "severity": "yellow", "domain": "Technical", "title": "Air Kerma >5 Gy ไม่มีแผนติดตามผิวหนัง",
                "guideline": "SCAI/HRS radiation safety (>5 Gy skin follow-up)", "kind": "completeness",
                "criteria": ["3.5"], "fix": "บันทึกแผนติดตามผิวหนังและแจ้งผู้ป่วย"}
        out.append((meta, hit(f"Air Kerma {c.num('qc.air_kerma_gy'):.1f} Gy (>5) ไม่มีแผนติดตามผิวหนัง", ["qc.air_kerma_gy", "qc.skin_followup_plan"])))
    return out


# ---------------------------------------------------------------- engine
def _evidence(c, keys):
    ev = []
    for k in keys:
        if ":" in k:
            lid, lk = k.split(":", 1)
            les = next((l for l in c.lesions if l.id == lid), None)
            f = les.f(lk) if les else {}
        else:
            f = c.f(k)
        if f:
            ev.append({"key": k, "value": f.get("value"), "page": f.get("page"), "span": f.get("span"),
                       "confidence": f.get("confidence"), "verified_by": f.get("verified_by"), "status": f.get("status")})
    return ev


def run_rules(ctx):
    """คืน list[Finding] — ไม่รวมผล override/low-confidence (จัดการใน scoring)"""
    findings = []
    disabled = set(ctx.ruleset.get("disabled_rules", []))
    for meta in RULES:
        if meta["id"] in disabled:
            continue
        for h in meta["fn"](ctx) or []:
            f = Finding.make(meta, h["message"], _evidence(ctx, h["evidence"]), h["fix"], h["review"], h["detail"])
            f["criteria"] = meta["criteria"]
            findings.append(f)
    for meta, h in completeness(ctx):
        f = Finding.make(meta, h["message"], _evidence(ctx, h["evidence"]), meta["fix"], False, None)
        f["criteria"] = meta["criteria"]
        findings.append(f)
    return findings


def rule_catalog():
    return [{k: v for k, v in r.items() if k != "fn"} for r in RULES]
