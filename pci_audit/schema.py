"""Data model: ทุก field เก็บ value, unit, source span (หน้า+บรรทัด/ข้อความ), confidence, verified_by

โครงเคส:
  {"fields": {key: Field}, "lesions": [{"id": "L1", "fields": {key: Field}}],
   "devices": [Device], "review_notes": [...]}
Field = {value, unit, page, span, confidence, verified_by, status}
status: extracted | manual | not_found | na
"""
import copy

# (key, ป้ายภาษาไทย, กลุ่ม, ชนิด, หน่วย)
CASE_FIELDS = [
    # Patient
    ("patient.age", "อายุ", "Patient", "num", "ปี"),
    ("patient.sex", "เพศ", "Patient", "str", ""),
    ("patient.weight_kg", "น้ำหนัก", "Patient", "num", "kg"),
    ("patient.height_cm", "ส่วนสูง", "Patient", "num", "cm"),
    ("patient.egfr", "eGFR", "Patient", "num", "mL/min/1.73m²"),
    ("patient.hb", "Hb", "Patient", "num", "g/dL"),
    ("patient.plt", "Platelet", "Patient", "num", "×10³/µL"),
    ("patient.inr", "INR", "Patient", "num", ""),
    ("patient.troponin", "Troponin", "Patient", "str", ""),
    ("patient.hba1c", "HbA1c", "Patient", "num", "%"),
    ("patient.bleeding_history", "ประวัติเลือดออก/ICH", "Patient", "bool", ""),
    ("patient.chronic_nsaid_steroid", "ใช้ NSAID/steroid เรื้อรัง", "Patient", "bool", ""),
    # Clinical
    ("clinical.syndrome", "Syndrome (STEMI/NSTEMI/UA/CCS)", "Clinical", "str", ""),
    ("clinical.header_syndrome", "Syndrome ใน header/indication", "Clinical", "str", ""),
    ("clinical.narrative_syndrome", "Diagnosis ใน narrative", "Clinical", "str", ""),
    ("clinical.dapt_text_syndrome", "Syndrome ที่อ้างในข้อความ DAPT", "Clinical", "str", ""),
    ("clinical.onset_date", "วันที่เริ่มมีอาการ (onset)", "Clinical", "date", ""),
    ("clinical.procedure_date", "วันที่ทำหัตถการ", "Clinical", "date", ""),
    ("clinical.fmc_time", "First medical contact", "Clinical", "time", ""),
    ("clinical.door_time", "Door time", "Clinical", "time", ""),
    ("clinical.wire_time", "Wire crossing time", "Clinical", "time", ""),
    ("clinical.ecg", "ECG", "Clinical", "str", ""),
    ("clinical.ecg_leads", "ECG leads ที่ ST ยก", "Clinical", "str", ""),
    ("clinical.killip", "Killip class", "Clinical", "str", ""),
    ("clinical.loading_dose", "Loading dose antiplatelet", "Clinical", "str", ""),
    ("clinical.angina_class", "Angina class (CCS)", "Clinical", "str", ""),
    ("clinical.omt_list", "OMT (ยา+ขนาด)", "Clinical", "list", ""),
    ("clinical.dm", "เบาหวาน", "Clinical", "bool", ""),
    ("clinical.af_oac", "AF / ใช้ OAC", "Clinical", "bool", ""),
    ("clinical.malignancy", "มะเร็งที่ active", "Clinical", "bool", ""),
    ("clinical.grace", "GRACE score", "Clinical", "num", ""),
    ("clinical.culprit_vessel", "Culprit vessel", "Clinical", "str", ""),
    # Ischemia
    ("ischemia.test_type", "ชนิดการตรวจ ischemia", "Ischemia", "str", ""),
    ("ischemia.test_date", "วันที่ตรวจ ischemia", "Ischemia", "date", ""),
    ("ischemia.risk_level", "ischemia risk level / METs", "Ischemia", "str", ""),
    ("ischemia.territory", "Territory ที่ขาดเลือด", "Ischemia", "str", ""),
    ("ischemia.ffr", "FFR", "Ischemia", "num", ""),
    ("ischemia.ifr_rfr", "iFR/RFR", "Ischemia", "num", ""),
    ("ischemia.pullback_delta", "Pullback Δ", "Ischemia", "num", ""),
    ("ischemia.drift_check", "Drift check", "Ischemia", "str", ""),
    ("ischemia.refractory_rationale", "เหตุผล refractory angina", "Ischemia", "str", ""),
    # Echo
    ("echo.date", "วันที่ Echo", "Echo", "date", ""),
    ("echo.lvef", "LVEF", "Echo", "num", "%"),
    ("echo.rwma_territory", "RWMA territory", "Echo", "str", ""),
    # Anatomy
    ("anatomy.dominance", "Dominance", "Anatomy", "str", ""),
    ("anatomy.lm_stenosis_pct", "LM % stenosis", "Anatomy", "num", "%"),
    ("anatomy.diagnosis_label", "Diagnosis label (เช่น TVD, 2VD)", "Anatomy", "str", ""),
    # Decision
    ("decision.heart_team_date", "Heart Team: วันที่", "Decision", "date", ""),
    ("decision.heart_team_cvt", "Heart Team: ชื่อ CVT", "Decision", "str", ""),
    ("decision.heart_team_recommendation", "Heart Team: ข้อเสนอแนะ", "Decision", "str", ""),
    ("decision.syntax_score", "SYNTAX score", "Decision", "num", ""),
    ("decision.sts_euroscore", "STS / EuroSCORE II", "Decision", "str", ""),
    ("decision.patient_decision", "การตัดสินใจของผู้ป่วย", "Decision", "str", ""),
    ("decision.pre_consent", "Pre-consent / consent", "Decision", "bool", ""),
    ("decision.adhoc_or_staged", "Ad hoc หรือ staged", "Decision", "str", ""),
    ("decision.target_rationale", "เหตุผลการเลือก target", "Decision", "str", ""),
    ("decision.cabg_discussion", "CABG discussion (DM+MVD)", "Decision", "bool", ""),
    ("decision.strategy", "กลยุทธ์ (เช่น CTO antegrade/retrograde)", "Decision", "str", ""),
    # QC
    ("qc.start_time", "เวลาเริ่มหัตถการ", "QC", "time", ""),
    ("qc.end_time", "เวลาสิ้นสุด", "QC", "time", ""),
    ("qc.heparin_u", "Heparin", "QC", "num", "U"),
    ("qc.act_values", "ACT (ทุกค่า)", "QC", "list", "s"),
    ("qc.contrast_ml", "Contrast", "QC", "num", "mL"),
    ("qc.air_kerma_gy", "Air Kerma", "QC", "num", "Gy"),
    ("qc.dap", "DAP", "QC", "num", "Gy·cm²"),
    ("qc.fluoro_min", "Fluoro time", "QC", "num", "min"),
    ("qc.hemostasis", "Hemostasis", "QC", "str", ""),
    ("qc.skin_followup_plan", "แผนติดตามผิวหนัง (Air Kerma >5 Gy)", "QC", "str", ""),
    ("qc.vasoactive", "Vasoactive/inotrope/MCS (ชื่อยา/อุปกรณ์)", "QC", "list", ""),
    ("qc.vasoactive_reason", "เหตุผลใช้ vasoactive/MCS", "QC", "str", ""),
    ("qc.vasoactive_bp", "BP ขณะเกิดเหตุ", "QC", "str", ""),
    ("qc.vasoactive_time", "เวลา vasoactive/MCS", "QC", "str", ""),
    ("qc.mcs_type", "IABP/Impella", "QC", "str", ""),
    ("qc.thrombectomy", "ทำ thrombectomy", "QC", "bool", ""),
    ("qc.thrombus_grade", "Thrombus grade", "QC", "str", ""),
    ("qc.lhc_performed", "ทำ LHC/coronary angiography", "QC", "bool", ""),
    # Plan
    ("plan.dapt_agents", "DAPT agents", "Plan", "list", ""),
    ("plan.dapt_duration_months", "ระยะเวลา DAPT", "Plan", "num", "เดือน"),
    ("plan.dapt_reason", "เหตุผลระยะเวลา DAPT", "Plan", "str", ""),
    ("plan.staged_plan", "แผน staged", "Plan", "str", ""),
    ("plan.residual_plan", "แผนรอยโรคที่เหลือ", "Plan", "str", ""),
    ("plan.secondary_prevention", "Secondary prevention", "Plan", "str", ""),
    ("plan.oac_name", "ชื่อ OAC/NOAC", "Plan", "str", ""),
    ("plan.oac_last_dose", "OAC ขนาดสุดท้าย", "Plan", "str", ""),
    ("plan.triple_therapy_duration", "ระยะ triple therapy", "Plan", "str", ""),
    ("plan.p2y12_agent", "P2Y12 agent", "Plan", "str", ""),
    ("plan.dual_therapy_duration", "ระยะ dual therapy", "Plan", "str", ""),
    ("plan.precise_dapt", "PRECISE-DAPT", "Plan", "num", ""),
    # Summary / header / coding
    ("summary.stent_count", "จำนวน stent ใน summary", "Summary", "num", ""),
    ("summary.dcb_count", "จำนวน DCB ใน summary", "Summary", "num", ""),
    ("summary.conclusion_text", "ข้อความ Conclusion", "Summary", "text", ""),
    ("summary.procedure_success", "หัตถการสำเร็จ", "Summary", "bool", ""),
    ("summary.header_procedures", "รายการหัตถการใน header", "Summary", "list", ""),
    ("summary.syntax_components", "องค์ประกอบ SYNTAX (segment:modifier;...)", "Summary", "list", ""),
    ("coding.principal_dx", "Principal diagnosis", "Coding", "str", ""),
    ("coding.icd10", "ICD-10 ที่ส่ง", "Coding", "list", ""),
    ("coding.icd9cm", "ICD-9-CM ที่ส่ง", "Coding", "list", ""),
]

LESION_FIELDS = [
    ("name", "ชื่อ lesion/segment", "str", ""),
    ("vessel", "Vessel", "str", ""),
    ("stenosis_pct", "% stenosis", "num", "%"),
    ("length_mm", "Length", "num", "mm"),
    ("vessel_size_mm", "Vessel size", "num", "mm"),
    ("calcium", "Calcium", "str", ""),
    ("thrombus_grade", "Thrombus grade", "str", ""),
    ("medina", "Medina", "str", ""),
    ("kbi_pot", "KBI/POT", "bool", ""),
    ("side_branch_size_mm", "Side branch size", "num", "mm"),
    ("side_branch_result", "ผล side branch", "str", ""),
    ("bifurcation_technique", "เทคนิค bifurcation", "str", ""),
    ("cto", "label CTO", "bool", ""),
    ("cto_duration_months", "CTO duration", "num", "เดือน"),
    ("j_cto", "J-CTO", "num", ""),
    ("collateral", "Collateral", "str", ""),
    ("ffr", "FFR (lesion)", "num", ""),
    ("ifr_rfr", "iFR/RFR (lesion)", "num", ""),
    ("treated", "ได้รับการรักษา (PCI)", "bool", ""),
    ("wire", "Wire", "str", ""),
    ("microcatheter", "Microcatheter", "str", ""),
    ("prep_device", "Prep device", "str", ""),
    ("atherectomy_type", "Atherectomy (rota/OAS/IVL)", "str", ""),
    ("atherectomy_size", "Burr/crown size", "str", ""),
    ("atherectomy_runs", "Runs", "num", ""),
    ("atherectomy_indication", "ข้อบ่งชี้ atherectomy", "str", ""),
    ("stent_brand", "Stent brand/model", "str", ""),
    ("stent_size", "Stent size", "str", ""),
    ("stent_atm", "Deploy atm", "num", "atm"),
    ("dcb", "ใช้ DCB", "bool", ""),
    ("dcb_rationale", "เหตุผลเลือก DCB", "str", ""),
    ("dcb_balloon_vessel_ratio", "Balloon/vessel ratio", "num", ""),
    ("dcb_inflation_sec", "DCB inflation time", "num", "s"),
    ("residual_after_prep_pct", "Residual หลัง prep", "num", "%"),
    ("dissection_after_prep", "Dissection grade หลัง prep (A–F)", "str", ""),
    ("dissection_rationale", "เหตุผลรับ dissection ≥C", "str", ""),
    ("hbr_device", "อุปกรณ์สำหรับ HBR (BioFreedom/DCB-HBR)", "bool", ""),
    ("post_dilation", "Post-dilation", "bool", ""),
    ("post_dilation_balloon_mm", "ขนาด post-dilation balloon", "num", "mm"),
    ("imaging_used", "Imaging (IVUS/OCT/none)", "str", ""),
    ("pre_mla", "Pre: MLA", "num", "mm²"),
    ("pre_reference", "Pre: reference", "num", "mm"),
    ("pre_calcium_arc", "Pre: calcium arc", "num", "°"),
    ("post_msa", "Post: MSA", "num", "mm²"),
    ("post_expansion_pct", "Post: % expansion", "num", "%"),
    ("post_criterion", "Post: เกณฑ์ที่ใช้", "str", ""),
    ("post_edge_dissection", "Post: edge dissection", "str", ""),
    ("timi_post", "TIMI flow", "str", ""),
    ("residual_pct", "Residual stenosis", "num", "%"),
    ("dissection_grade", "Dissection grade (final)", "str", ""),
    ("perforation", "Perforation", "bool", ""),
    ("no_reflow", "No-reflow", "bool", ""),
    ("result", "ผล (success/failed)", "str", ""),
    ("narrative_text", "ข้อความ narrative ของ lesion", "text", ""),
]

DEVICE_FIELDS = ["slot", "source", "category", "name", "size", "gtin", "ref", "lot", "expiry", "page", "span"]
DEVICE_SOURCES = ("narrative", "log", "sticker")
DEVICE_CATEGORIES = ("stent", "dcb", "balloon", "atherectomy", "imaging", "wire", "other")

CASE_FIELD_KEYS = [f[0] for f in CASE_FIELDS]
CASE_FIELD_META = {f[0]: {"label": f[1], "group": f[2], "type": f[3], "unit": f[4]} for f in CASE_FIELDS}
LESION_FIELD_KEYS = [f[0] for f in LESION_FIELDS]
LESION_FIELD_META = {f[0]: {"label": f[1], "type": f[2], "unit": f[3]} for f in LESION_FIELDS}


def make_field(value=None, unit="", page=None, span="", confidence=None,
               verified_by=None, status=None):
    if status is None:
        status = "not_found" if value in (None, "") else "extracted"
    return {"value": value, "unit": unit, "page": page, "span": span or "",
            "confidence": confidence, "verified_by": verified_by, "status": status}


def empty_case():
    return {"fields": {}, "lesions": [], "devices": [], "review_notes": []}


def normalize_case(case):
    """เติม key ที่ขาดให้ครบ (status=not_found) และแปลงค่า plain → Field"""
    case = copy.deepcopy(case) if case else empty_case()
    case.setdefault("lesions", [])
    case.setdefault("devices", [])
    case.setdefault("review_notes", [])
    fields = case.setdefault("fields", {})
    for key, meta in CASE_FIELD_META.items():
        f = fields.get(key)
        if not isinstance(f, dict) or "value" not in f:
            f = make_field(f)
        fields[key] = {**make_field(), **f}
        fields[key]["unit"] = fields[key]["unit"] or meta["unit"]
    for i, les in enumerate(case["lesions"], 1):
        les.setdefault("id", f"L{i}")
        lf = les.setdefault("fields", {})
        for key, meta in LESION_FIELD_META.items():
            f = lf.get(key)
            if not isinstance(f, dict) or "value" not in f:
                f = make_field(f)
            lf[key] = {**make_field(), **f}
            lf[key]["unit"] = lf[key]["unit"] or meta["unit"]
    for d in case["devices"]:
        for k in DEVICE_FIELDS:
            d.setdefault(k, "")
    return case
