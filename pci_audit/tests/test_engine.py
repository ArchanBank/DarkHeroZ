import copy
import json
import os
import unittest
from datetime import date

from pci_audit import util
from pci_audit.phrases import addendum, coding_check
from pci_audit.scoring import evaluate
from pci_audit.tests.helpers import clean_ccs_case, stemi_case

SAMPLE = os.path.join(os.path.dirname(__file__), "..", "samples", "sample_case.json")


def fired(ev, include_review=False):
    return {f["rule_id"].split("@")[0] for f in ev["findings"] if not f["overridden"] and (include_review or not f["needs_review"])}


class TestUtil(unittest.TestCase):
    def test_buddhist_era_and_formats(self):
        self.assertEqual(util.parse_date("5 ต.ค. 2569"), date(2026, 10, 5))
        self.assertEqual(util.parse_date("05/10/2569"), date(2026, 10, 5))
        self.assertEqual(util.parse_date("2026-10-05"), date(2026, 10, 5))
        self.assertEqual(util.parse_date("5 Oct 2026"), date(2026, 10, 5))
        self.assertIsNone(util.parse_date("ไม่พบ"))

    def test_time_and_numbers(self):
        self.assertEqual(util.minutes_between("23:50", "00:20"), 30)
        self.assertEqual(util.to_float("70%"), 70.0)
        self.assertIs(util.to_bool("ไม่มี"), False)


class TestClean(unittest.TestCase):
    def test_clean_cases_perfect(self):
        for c in (clean_ccs_case(), stemi_case()):
            ev = evaluate(c)
            self.assertEqual(ev["overall"], 10.0)
            self.assertEqual(ev["findings"], [])
            self.assertEqual(ev["action"], "unlock")


class TestRules(unittest.TestCase):
    def test_sample_fires_expected_rules(self):
        with open(SAMPLE, encoding="utf-8") as f:
            ev = evaluate(json.load(f))
        self.assertTrue({"R01", "R02", "R03", "R04", "R06", "R07", "R08", "R09", "R10", "R11", "Y01", "Y05", "Y07", "Y10", "Y11", "Y12", "G01"} <= fired(ev))
        self.assertEqual(ev["action"], "peer_review")
        for d in ev["domains"].values():
            self.assertLessEqual(d["score"], 6.0)  # red cap

    def test_r04_boundary(self):
        c = stemi_case(**{"clinical.onset_date": "2026-09-07"})  # 28 วันพอดี → ไม่ fire
        self.assertNotIn("R04", fired(evaluate(c)))
        c = stemi_case(**{"clinical.onset_date": "2026-09-06"})  # 29 วัน
        self.assertIn("R04", fired(evaluate(c)))

    def test_r05_ccs_physiology(self):
        c = clean_ccs_case()
        c["fields"]["ischemia.ffr"] = 0.85
        c["lesions"][0]["fields"]["ffr"] = 0.85
        self.assertIn("R05", fired(evaluate(c)))
        c["fields"]["ischemia.refractory_rationale"] = "refractory angina on 3 drugs"
        self.assertNotIn("R05", fired(evaluate(c)))

    def test_stemi_heart_team_na_and_d2b(self):
        c = stemi_case()
        c["lesions"].append({"id": "L2", "fields": {"vessel": "RCA", "stenosis_pct": 80}})
        c["lesions"][0]["fields"]["stenosis_pct"] = 90
        ev = evaluate(c)
        self.assertIn("LM/MVD", ev["case_types"])
        self.assertNotIn("R01", fired(ev))
        self.assertIn("R01", [s["rule_id"] for s in ev["skipped"]])
        c = stemi_case()
        del c["fields"]["clinical.wire_time"]
        self.assertIn("R13", fired(evaluate(c)))

    def test_na_rescale_not_zero(self):
        ev = evaluate(clean_ccs_case())
        self.assertIsNone(next(c for c in ev["criteria"] if c["id"] == "2.1")["score"])  # N/A ไม่นับ
        self.assertEqual(ev["domains"]["Strategy"]["score"], 10.0)

    def test_red_cap_near_miss_and_no_auto_unlock(self):
        c = clean_ccs_case()
        c["fields"]["qc.vasoactive"] = ["dopamine"]
        c["fields"]["qc.vasoactive_time"] = "09:10"
        ev = evaluate(c)
        self.assertEqual(ev["red_rules"], ["R02"])
        self.assertEqual(ev["domains"]["Technical"]["score"], 6.0)
        self.assertGreater(ev["domains"]["Technical"]["raw"], 6.0)
        self.assertGreaterEqual(ev["overall_raw"], 8.5)
        self.assertTrue(ev["near_miss"])
        self.assertNotEqual(ev["action"], "unlock")  # ปรับปรุง: แดงค้างห้าม auto-unlock

    def test_override_requires_and_removes_deduction(self):
        c = clean_ccs_case()
        c["fields"]["qc.vasoactive"] = ["dopamine"]
        base = evaluate(c)
        ov = evaluate(c, {"R02": {"reason": "bailout hypotension documented elsewhere", "user": "dr"}})
        self.assertGreater(ov["overall"], base["overall"])
        f = next(f for f in ov["findings"] if f["rule_id"] == "R02")
        self.assertTrue(f["overridden"])
        self.assertEqual(f["override"]["user"], "dr")

    def test_low_confidence_goes_to_review_not_deduction(self):
        c = clean_ccs_case()
        c["fields"]["clinical.onset_date"] = {"value": "2026-01-01", "confidence": 0.4, "status": "extracted", "span": "x", "page": 1}
        c["fields"]["clinical.syndrome"] = "NSTEMI"
        c["fields"]["clinical.header_syndrome"] = "NSTEMI"
        c["fields"]["clinical.narrative_syndrome"] = "NSTEMI"
        c["fields"]["clinical.dapt_text_syndrome"] = "NSTEMI"
        ev = evaluate(c)
        r04 = next(f for f in ev["findings"] if f["rule_id"] == "R04")
        self.assertTrue(r04["needs_review"])
        self.assertNotIn("R04", ev["red_rules"])
        self.assertEqual(ev["pending_review"], 1)
        c["fields"]["clinical.onset_date"]["verified_by"] = "auditor1"
        self.assertIn("R04", evaluate(c)["red_rules"])

    def test_r03_syndrome_conflict(self):
        c = clean_ccs_case()
        c["fields"]["clinical.dapt_text_syndrome"] = "after ACS"
        self.assertIn("R03", fired(evaluate(c)))
        c["fields"]["clinical.dapt_text_syndrome"] = "CCS"
        c["fields"]["clinical.narrative_syndrome"] = "ACS"
        self.assertIn("R03", fired(evaluate(c)))

    def test_r06_r07_device_consistency(self):
        c = clean_ccs_case()
        c["fields"]["summary.stent_count"] = 2
        self.assertIn("R06", fired(evaluate(c)))
        c = clean_ccs_case()
        c["devices"][2]["size"] = "2.75x24"
        self.assertIn("R07", fired(evaluate(c)))

    def test_y09_skipped_without_real_lookup(self):
        c = clean_ccs_case()
        c["lesions"][0]["fields"]["post_dilation_balloon_mm"] = 5.0
        ev = evaluate(c)
        self.assertNotIn("Y09", fired(ev))
        self.assertIn("Y09", [s["rule_id"] for s in ev["skipped"]])
        lk = {"overexpansion_limits_mm": [{"match": "Brand-X", "max_post_dilation_mm": 4.5}], "hbr_devices": []}
        self.assertIn("Y09", fired(evaluate(c, lookups=lk)))

    def test_y03_hbr_device_dapt(self):
        c = clean_ccs_case()
        c["lesions"][0]["fields"]["stent_brand"] = "BioFreedom"
        c["fields"]["clinical.af_oac"] = True
        c["fields"]["plan.dapt_duration_months"] = 12
        self.assertIn("Y03", fired(evaluate(c)))


class TestOutputs(unittest.TestCase):
    def test_addendum_never_invents_values(self):
        with open(SAMPLE, encoding="utf-8") as f:
            case = json.load(f)
        add = addendum(case)
        self.assertTrue(add["items"])
        r01 = next(i for i in add["items"] if i["rule_id"] == "R01")
        self.assertIn("[____]", r01["text"])
        self.assertGreater(r01["needs_input"], 0)

    def test_coding_check(self):
        c = clean_ccs_case()
        c["fields"]["coding.icd10"] = ["I25.1"]
        c["fields"]["coding.icd9cm"] = ["36.07"]
        r = coding_check(c)
        by = {x["code"]: x["status"] for x in r["icd9cm"]}
        self.assertEqual(by["36.07"], "ส่งแล้ว")
        self.assertEqual(by["00.40"], "ยังไม่พบในที่ส่ง")
        self.assertEqual(by["00.45"], "ยังไม่พบในที่ส่ง")
        self.assertTrue(all(d["consistent"] for d in r["devices"]))


if __name__ == "__main__":
    unittest.main()
