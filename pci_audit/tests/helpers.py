import copy


def clean_ccs_case():
    """เคส CCS ที่เอกสารครบ ไม่ควรมี finding ใด ๆ"""
    return copy.deepcopy({
        "fields": {
            "patient.age": 62, "patient.sex": "ชาย", "patient.egfr": 78, "patient.hb": 14.0, "patient.plt": 250,
            "clinical.syndrome": "CCS", "clinical.header_syndrome": "CCS", "clinical.narrative_syndrome": "CCS",
            "clinical.dapt_text_syndrome": "CCS", "clinical.procedure_date": "2026-10-05",
            "clinical.angina_class": "CCS III", "clinical.omt_list": ["amlodipine 10 mg", "bisoprolol 5 mg"],
            "ischemia.risk_level": "high", "ischemia.territory": "LAD", "ischemia.ffr": 0.71,
            "echo.date": "2026-08-20", "echo.lvef": 58,
            "qc.start_time": "09:00", "qc.end_time": "09:40", "qc.contrast_ml": 90, "qc.air_kerma_gy": 1.2,
            "qc.dap": 4500, "qc.hemostasis": "TR band", "qc.lhc_performed": True,
            "plan.dapt_duration_months": 6, "summary.stent_count": 1, "summary.header_procedures": ["LHC", "PCI"],
            "summary.conclusion_text": "PCI to mid LAD successful", "summary.procedure_success": True,
        },
        "lesions": [{"id": "L1", "fields": {
            "name": "mid LAD", "vessel": "LAD", "stenosis_pct": 85, "vessel_size_mm": 3.0, "treated": True,
            "stent_brand": "Brand-X DES", "stent_size": "3.0x24", "residual_pct": 0, "timi_post": "3", "ffr": 0.71,
            "imaging_used": "none", "narrative_text": "Mid LAD 85% stenosis, predilated, one DES 3.0x24 deployed."}}],
        "devices": [
            {"slot": "S1", "source": "narrative", "category": "stent", "name": "Brand-X DES", "size": "3.0x24"},
            {"slot": "S1", "source": "log", "category": "stent", "name": "Brand-X DES", "size": "3.0 x 24 mm"},
            {"slot": "S1", "source": "sticker", "category": "stent", "name": "BRAND-X DES", "size": "3.0x24"}],
    })


def stemi_case(**over):
    c = clean_ccs_case()
    f = c["fields"]
    f.update({"clinical.syndrome": "STEMI", "clinical.header_syndrome": "STEMI", "clinical.narrative_syndrome": "STEMI",
              "clinical.dapt_text_syndrome": "STEMI", "clinical.onset_date": "2026-10-05", "clinical.fmc_time": "08:00",
              "clinical.door_time": "08:20", "clinical.wire_time": "08:55", "clinical.ecg_leads": "V1-V4",
              "clinical.killip": "I", "clinical.loading_dose": "ticagrelor 180 mg", "clinical.culprit_vessel": "LAD"})
    for k in ("clinical.angina_class", "clinical.omt_list", "ischemia.risk_level", "ischemia.territory", "ischemia.ffr"):
        f.pop(k)
    c["lesions"][0]["fields"].pop("ffr")
    f.update(over)
    return c
