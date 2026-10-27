"use strict";
/* PCI Audit & QC — UI (ไม่ใช้ innerHTML กับข้อมูลจากผู้ใช้/โมเดล เพื่อกัน XSS) */
const $ = (s, r = document) => r.querySelector(s);
const SEV = { red: ["แดง", "b-red"], yellow: ["เหลือง", "b-yel"], green: ["เขียว", "b-grn"] };
const ACTION = { unlock: ["ปลดล็อกให้ส่งเบิก", "b-grn"], addendum: ["ต้องแจ้ง addendum", "b-yel"], lock: ["ล็อกการส่งเบิก", "b-red"], peer_review: ["ส่ง peer review", "b-red"] };
const DOMAIN_TH = { Indication: "ข้อบ่งชี้ & เอกสาร", Strategy: "กลยุทธ์", Technical: "เทคนิค PCI", Insurance: "ประกัน/audit" };
let CFG = null, ME = null, state = { tab: "report" };

function h(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (k === "value") e.value = v;
    else e.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids.flat(Infinity)) if (k != null && k !== false) e.append(k.nodeType ? k : document.createTextNode(String(k)));
  return e;
}
const badge = (txt, cls) => h("span", { class: "badge " + cls }, txt);
const fmt = (v, d = 1) => (v == null ? "—" : typeof v === "number" ? (Math.round(v * 10 ** d) / 10 ** d).toString() : String(v));

function toast(msg, err) {
  const t = $("#toast"); t.textContent = msg; t.className = "toast on";
  clearTimeout(toast.t); toast.t = setTimeout(() => (t.className = "toast"), err ? 6000 : 2500);
}
async function api(method, path, body, raw) {
  const opt = { method, headers: { "X-Requested-With": "pci-audit" }, credentials: "same-origin" };
  if (body instanceof FormData) opt.body = body;
  else if (body !== undefined) { opt.body = JSON.stringify(body); opt.headers["Content-Type"] = "application/json"; }
  const r = await fetch("/api" + path, opt);
  if (raw && r.ok) return r;
  let data = {}; try { data = await r.json(); } catch (e) { /* ignore */ }
  if (!r.ok) throw new Error(data.error || "เกิดข้อผิดพลาด (" + r.status + ")");
  return data;
}
const run = async (fn, okMsg) => { try { const r = await fn(); if (okMsg) toast(okMsg); return r; } catch (e) { toast(e.message, true); } };

/* ---------- shell ---------- */
async function boot() {
  try { [ME, CFG] = await Promise.all([api("GET", "/me"), api("GET", "/config")]); }
  catch (e) { $("#app").replaceChildren(h("div", { class: "err" }, e.message)); return; }
  $("#who").textContent = ME.display + " · " + ME.role + (CFG.dev_mode ? " · โหมด dev" : "");
  $("#disclaimer").textContent = CFG.disclaimer + " | ruleset " + CFG.ruleset.version + " (" + CFG.ruleset.status + ")";
  addEventListener("hashchange", route); route();
}
function nav(active) {
  const items = [];
  if (ME.role !== "manager") items.push(["#/cases", "เคส"]);
  if (["manager", "admin", "auditor"].includes(ME.role)) items.push(["#/dashboard", "Dashboard"]);
  items.push(["#/rules", "กฎ & เกณฑ์"]);
  $("#nav").replaceChildren(...items.map(([href, t]) => h("a", { href, class: active === href ? "on" : "" }, t)));
}
function route() {
  const hash = location.hash || (ME.role === "manager" ? "#/dashboard" : "#/cases");
  const m = hash.match(/^#\/case\/(\d+)/);
  if (m) { nav("#/cases"); return viewCase(+m[1]); }
  nav(hash);
  if (hash === "#/dashboard") return viewDashboard();
  if (hash === "#/rules") return viewRules();
  return viewCases();
}
const mount = (...n) => $("#app").replaceChildren(...n.flat(Infinity).filter(x => x != null && x !== false));

/* ---------- เคส ---------- */
async function viewCases() {
  const rows = await run(() => api("GET", "/cases")) || [];
  const canWrite = ["physician", "auditor", "admin"].includes(ME.role);
  const form = h("form", { class: "card", onsubmit: async ev => {
    ev.preventDefault();
    const fd = new FormData(ev.target);
    const r = await run(() => api("POST", "/cases", fd), "อัปโหลดแล้ว — ตรวจ preview ก่อนส่งประมวลผล");
    if (r) location.hash = "#/case/" + r.id + "/source";
  } },
    h("h2", {}, "เพิ่มเคส (อัปโหลด PDF หรือรูปภาพ)"),
    h("div", { class: "row" },
      h("input", { type: "file", name: "file", accept: ".pdf,.jpg,.jpeg,.png,.webp,.gif", required: true }),
      h("input", { name: "title", placeholder: "ชื่อเคส/รหัสอ้างอิง (ไม่ใส่ชื่อผู้ป่วย)", size: 30 }),
      h("input", { name: "known_names", placeholder: "ชื่อที่ต้องซ่อนเพิ่ม (คั่น ,)", size: 28 }),
      h("button", { class: "pri" }, "อัปโหลด")),
    h("p", { class: "small mut" }, "ไฟล์สูงสุด " + CFG.max_upload_mb + " MB · ระบบจะซ่อนชื่อ/HN/DOB ก่อนส่งข้อความไปประมวลผลบน Cloud API (โมเดล " + CFG.model + ")" +
      (CFG.local_ocr ? " · OCR ในเครื่องพร้อมใช้งาน" : " · ไม่มี OCR ในเครื่อง: ภาพสแกนต้องส่งเป็นภาพ" + (CFG.allow_image_upload ? "" : " (เซิร์ฟเวอร์ปิดไว้)"))));
  const tbl = h("table", {}, h("thead", {}, h("tr", {}, ...["#", "เคส", "เจ้าของ", "สถานะ", "คะแนน", "ระดับ", "แดง", "อัปเดต"].map(t => h("th", {}, t)))),
    h("tbody", {}, rows.map(r => h("tr", {}, h("td", {}, r.id),
      h("td", {}, h("a", { href: "#/case/" + r.id }, r.title || r.source_name || "(ไม่มีชื่อ)")), h("td", {}, r.owner), h("td", {}, r.status),
      h("td", { class: "n" }, fmt(r.overall)), h("td", {}, r.level ? badge(r.level, (ACTION[r.action] || [0, "b-gry"])[1]) : "—"),
      h("td", { class: "n" }, r.red_count ?? "—"), h("td", { class: "small mut" }, (r.updated || "").replace("T", " ").slice(0, 16))))));
  mount(h("h1", {}, "เคสทั้งหมด"), canWrite && form,
    canWrite && h("p", {}, h("button", { onclick: async () => { const r = await run(() => api("POST", "/cases/sample")); if (r) location.hash = "#/case/" + r.id; } }, "นำเข้าเคสตัวอย่าง (สังเคราะห์)")),
    h("div", { class: "card" }, rows.length ? tbl : h("p", { class: "mut" }, "ยังไม่มีเคส")));
}

async function viewCase(id) {
  const rep = await run(() => api("GET", "/cases/" + id));
  if (!rep) return mount(h("div", { class: "err" }, "เปิดเคสไม่ได้"));
  const parts = location.hash.split("/");
  if (parts[3]) state.tab = parts[3];
  const coder = ME.role === "coder";
  const tabs = coder ? [["coding", "Coding check"]] : [["report", "รายงาน"], ["data", "ข้อมูล & ยืนยัน"], ["fix", "รายการแก้ & Addendum"], ["coding", "Coding check"], ["source", "ต้นฉบับ / ส่งประมวลผล"], ["audit", "Audit trail"]];
  if (!tabs.find(t => t[0] === state.tab)) state.tab = tabs[0][0];
  const refresh = (r) => { if (r) viewCaseRender(id, r.report || r, tabs); };
  window._refresh = refresh;
  viewCaseRender(id, rep, tabs);
}
function viewCaseRender(id, rep, tabs) {
  const ev = rep.evaluation;
  const head = h("div", { class: "row" }, h("h1", { style: "margin:0" }, rep.title || "เคส #" + id),
    ev && ev.level && badge(ev.level, (ACTION[ev.action] || [0, "b-gry"])[1]),
    ev && ev.near_miss && badge("เกือบดี แต่ติดแดง 1 ข้อ", "b-yel"),
    h("span", { class: "mut small" }, "เจ้าของ " + rep.owner + " · สถานะ " + rep.status),
    h("button", { class: "sm", onclick: () => print() }, "พิมพ์รายงาน"));
  const tabbar = h("div", { class: "tabs", role: "tablist" }, tabs.map(([k, t]) =>
    h("button", { class: state.tab === k ? "on" : "", role: "tab", onclick: () => { state.tab = k; viewCaseRender(id, rep, tabs); } }, t +
      (k === "report" && ev && ev.pending_review ? " (" + ev.pending_review + " รอยืนยัน)" : ""))));
  const body = { report: tabReport, data: tabData, fix: tabFix, coding: tabCoding, source: tabSource, audit: tabAudit }[state.tab](id, rep);
  mount(head, tabbar, body instanceof Promise ? h("div", {}, "กำลังโหลด…") : body);
  if (body instanceof Promise) body.then(n => mount(head, tabbar, n));
}

/* ---- รายงาน ---- */
function tabReport(id, rep) {
  const ev = rep.evaluation;
  if (!ev) return h("div", { class: "card" }, "ยังไม่มีผลประเมิน — ไปที่แท็บ \"ต้นฉบับ / ส่งประมวลผล\" หรือกรอกข้อมูลก่อน");
  const dom = Object.entries(ev.domains).map(([k, d]) => h("div", { class: "card" },
    h("div", { class: "small mut" }, d.label),
    h("div", { class: "row" }, h("span", { class: "score" }, fmt(d.score)), d.raw != null && d.raw !== d.score && h("span", { class: "mut small" }, "ก่อน cap " + fmt(d.raw)), d.score == null && h("span", { class: "mut" }, "N/A")),
    d.red_rules.length > 0 && h("div", {}, badge("cap ≤ 6 จาก " + d.red_rules.join(", "), "b-red"))));
  const overall = h("div", { class: "card" }, h("div", { class: "row" },
    h("div", {}, h("div", { class: "small mut" }, "คะแนนรวม (หลัง cap)"), h("div", { class: "score" }, fmt(ev.overall))),
    h("div", {}, h("div", { class: "small mut" }, "คะแนนดิบ (ก่อน cap)"), h("div", { class: "score" }, fmt(ev.overall_raw))),
    h("div", {}, h("div", { class: "small mut" }, "การดำเนินการ"), badge(ev.action_th || "—", (ACTION[ev.action] || [0, "b-gry"])[1]), ev.action_note && h("div", { class: "small" }, ev.action_note)),
    h("div", {}, h("div", { class: "small mut" }, "Case type"), (ev.case_types || []).map(t => [badge(t, "b-gry"), " "])),
    ev.d2b_min != null && h("div", {}, h("div", { class: "small mut" }, "D2B"), h("b", {}, ev.d2b_min + " นาที"))));
  const crit = h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "เกณฑ์"), h("th", {}, ""), h("th", { class: "n" }, "คะแนน"), h("th", {}, "rule ที่เกี่ยวข้อง"))),
    h("tbody", {}, ev.criteria.map(c => h("tr", {}, h("td", {}, c.id), h("td", {}, c.label), h("td", { class: "n" }, c.score == null ? "N/A" : c.score + "/2"), h("td", { class: "small" }, c.rules.join(", "))))));
  const groups = { red: [], yellow: [], green: [] };
  ev.findings.forEach(f => groups[f.severity].push(f));
  const fblock = (sev) => {
    const main = groups[sev].filter(f => f.kind !== "completeness"), comp = groups[sev].filter(f => f.kind === "completeness");
    return h("div", {}, h("h2", {}, [badge(SEV[sev][0], SEV[sev][1]), " รายการระดับ" + SEV[sev][0] + " (" + groups[sev].length + ")"]),
      main.map(f => findingCard(id, f)),
      comp.length > 0 && h("details", {}, h("summary", {}, "ข้อมูลบังคับตาม case type ที่ขาด (" + comp.length + ")"), comp.map(f => findingCard(id, f))),
      groups[sev].length === 0 && h("p", { class: "mut" }, "ไม่มี"));
  };
  const hist = rep.scores && rep.scores.length > 1 && h("div", { class: "card" }, h("h3", {}, "ประวัติคะแนน (ก่อน/หลังแก้)"),
    h("table", {}, h("tbody", {}, rep.scores.map(s => h("tr", {}, h("td", { class: "small" }, s.ts.replace("T", " ").slice(0, 16)), h("td", {}, s.label), h("td", { class: "n" }, fmt(s.overall)), h("td", {}, s.level), h("td", { class: "small" }, s.user))))));
  const skipped = ev.skipped.length > 0 && h("details", { class: "card" }, h("summary", {}, "rule ที่ประเมินไม่ได้ (" + ev.skipped.length + ") — ไม่นับว่าผ่าน"), h("ul", {}, ev.skipped.map(s => h("li", {}, s.rule_id + ": " + s.reason))));
  return h("div", {}, overall, h("div", { class: "grid" }, dom), h("details", { class: "card" }, h("summary", {}, "คะแนนรายเกณฑ์ (4 หมวด × 5 เกณฑ์)"), crit),
    ev.pending_review > 0 && h("div", { class: "card" }, h("h2", { style: "margin-top:0" }, "รอผู้ตรวจยืนยัน (" + ev.pending_review + ") — ยังไม่หักคะแนน"),
      ev.findings.filter(f => f.needs_review && !f.overridden).map(f => findingCard(id, f))),
    fblock("red"), fblock("yellow"), fblock("green"), skipped, hist,
    rep.overrides && Object.keys(rep.overrides).length > 0 && h("div", { class: "card" }, h("h3", {}, "การ override rule โดยแพทย์"), h("ul", {}, Object.entries(rep.overrides).map(([r, o]) => h("li", {}, r + " — " + o.reason + " (" + o.user + ")")))));
}
function findingCard(id, f) {
  const [sv, cls] = SEV[f.severity];
  return h("div", { class: "finding " + f.severity + (f.overridden || f.needs_review ? " off" : "") },
    h("div", { class: "row" }, badge(sv, cls), h("b", {}, f.rule_id), h("span", {}, f.title),
      f.needs_review && badge("รอยืนยัน" + (f.detail && f.detail.low_confidence ? " (confidence ต่ำ)" : ""), "b-gry"), f.overridden && badge("override แล้ว", "b-gry"),
      f.impact > 0 && h("span", { class: "small mut" }, "แก้แล้ว +" + f.impact.toFixed(2) + " คะแนนรวม")),
    h("div", {}, f.message),
    f.evidence.length > 0 && h("div", { class: "ev" }, "หลักฐาน: ", f.evidence.map(e => h("span", {}, "[", e.key, "=", fmt(Array.isArray(e.value) ? e.value.join("; ") : e.value), e.page ? " หน้า" + e.page : "", e.confidence != null ? " conf " + e.confidence.toFixed(2) : "", e.span ? " \"" + e.span + "\"" : "", "] "))),
    h("div", { class: "ev" }, "แนวทาง: " + f.guideline, " · วิธีแก้: " + f.fix),
    f.overridden && h("div", { class: "ev" }, "เหตุผล override: " + f.override.reason + " (" + f.override.user + ")"),
    ["physician", "admin"].includes(ME.role) && (f.overridden
      ? h("button", { class: "sm", onclick: async () => refreshWith(await run(() => api("POST", "/cases/" + id + "/override", { rule_id: f.rule_id, remove: true }))) }, "ยกเลิก override")
      : f.kind !== "completeness" && h("button", { class: "sm", onclick: async () => { const reason = prompt("เหตุผลที่ override " + f.rule_id + " (จะแสดงใน audit report):"); if (reason) refreshWith(await run(() => api("POST", "/cases/" + id + "/override", { rule_id: f.rule_id, reason }), "บันทึก override แล้ว")); } }, "Override (ระบุเหตุผล)")));
}
const refreshWith = (r) => r && window._refresh(r);

/* ---- ข้อมูล & ยืนยัน (human-in-the-loop) ---- */
function fieldRow(id, f, meta, scope, lesionId) {
  const low = f.status === "extracted" && !f.verified_by && f.confidence != null && f.confidence < CFG.ruleset.thresholds.low_confidence;
  const inp = h("input", { class: "v", value: Array.isArray(f.value) ? f.value.join("; ") : (f.value ?? ""), "aria-label": meta.label, placeholder: f.status === "na" ? "N/A" : "ไม่พบ" });
  const save = async (verify) => refreshWith(await run(() => api("POST", "/cases/" + id + "/field", { scope, lesion_id: lesionId, key: meta.key, value: inp.value, verify }), "บันทึกแล้ว"));
  const can = ["physician", "auditor", "admin"].includes(ME.role);
  return h("tr", { class: low || f.grounded === false ? "lowc" : "" }, h("td", {}, meta.label, meta.unit ? h("span", { class: "small mut" }, " (" + meta.unit + ")") : ""),
    h("td", {}, can ? inp : fmt(f.value)),
    h("td", { class: "small" }, f.status === "not_found" ? "ไม่พบในรายงาน" : f.status === "na" ? "N/A" : f.status === "manual" ? "กรอกเอง" : "AI ดึง", f.confidence != null && " · conf " + f.confidence.toFixed(2), f.grounded === false && " · ⚠ ไม่พบข้อความอ้างอิงในต้นฉบับ"),
    h("td", { class: "small" }, f.page ? "หน้า " + f.page + ": " : "", f.span ? "\"" + f.span + "\"" : ""),
    h("td", {}, f.verified_by ? badge("✓ " + f.verified_by, "b-grn") : (f.status === "not_found" ? "" : badge("ยังไม่ยืนยัน", "b-yel"))),
    h("td", {}, can && [h("button", { class: "sm", onclick: () => save(true) }, "บันทึก/ยืนยัน"), " ", h("button", { class: "sm", title: "ไม่เกี่ยวข้องกับเคสนี้", onclick: async () => refreshWith(await run(() => api("POST", "/cases/" + id + "/field", { scope, lesion_id: lesionId, key: meta.key, na: true }))) }, "N/A")]));
}
function tabData(id, rep) {
  const c = rep.case;
  if (!c) return h("div", { class: "card" }, "ยังไม่มีข้อมูล — ส่งประมวลผลที่แท็บต้นฉบับ หรือนำเข้าข้อมูล");
  const th = h("thead", {}, h("tr", {}, ["Field", "ค่า", "สถานะ", "ตำแหน่งต้นฉบับ (source span)", "ผู้ยืนยัน", ""].map(t => h("th", {}, t))));
  const groups = {};
  CFG.fields.forEach(m => (groups[m.group] = groups[m.group] || []).push(m));
  const sections = Object.entries(groups).map(([g, ms]) => h("details", { class: "card", open: ["Clinical", "Patient"].includes(g) },
    h("summary", {}, h("b", {}, g)), h("table", {}, th.cloneNode(true), h("tbody", {}, ms.map(m => {
      return fieldRow(id, c.fields[m.key], m, "case"); })))));
  const lesions = c.lesions.map(l => h("details", { class: "card" }, h("summary", {}, h("b", {}, l.id + " — " + (fmt(l.fields.name.value) !== "—" ? l.fields.name.value : "(ไม่มีชื่อ)"))),
    h("table", {}, th.cloneNode(true), h("tbody", {}, CFG.lesion_fields.map(m => fieldRow(id, l.fields[m.key], m, "lesion", l.id)))),
    h("button", { class: "sm", onclick: async () => confirm("ลบ " + l.id + "?") && refreshWith(await run(() => api("POST", "/cases/" + id + "/lesions", { action: "remove", id: l.id }))) }, "ลบ lesion นี้")));
  const dev = deviceEditor(id, c.devices);
  return h("div", {}, h("div", { class: "row card" },
    h("button", { class: "pri", onclick: async () => { const r = await run(() => api("POST", "/cases/" + id + "/verify_all", { min_conf: 0.9 })); if (r) { toast("ยืนยัน " + r.verified + " field (conf ≥ 0.9 และมีข้อความอ้างอิง)"); refreshWith(r.report); } } }, "ยืนยันทุก field ที่ conf ≥ 0.9"),
    h("span", { class: "small mut" }, "แถวสีเหลือง = confidence ต่ำหรืออ้างอิงต้นฉบับไม่ได้ ต้องตรวจเอง · ค่าที่ AI ดึงยังไม่ถูกใช้ตัดคะแนนจนกว่าจะยืนยัน (ถ้า conf < " + CFG.ruleset.thresholds.low_confidence + ")")),
    c.review_notes && c.review_notes.length > 0 && h("div", { class: "card" }, h("h3", {}, "ข้อสังเกตจาก AI ให้ผู้ตรวจพิจารณา (ไม่ถูกนับเป็นคะแนน)"), h("ul", {}, c.review_notes.map(n => h("li", {}, n.issue, h("div", { class: "small mut" }, (n.page ? "หน้า " + n.page + " " : "") + (n.span ? "\"" + n.span + "\" — " : "") + n.why))))),
    h("h2", {}, "Lesion / PCI"), lesions, h("button", { onclick: async () => refreshWith(await run(() => api("POST", "/cases/" + id + "/lesions", { action: "add" }))) }, "+ เพิ่ม lesion"),
    h("h2", {}, "อุปกรณ์ (narrative / log / sticker)"), dev,
    h("h2", {}, "Field ทั่วไป"), sections);
}
function deviceEditor(id, devs) {
  const cols = ["slot", "source", "category", "name", "size", "gtin", "ref", "lot", "expiry"];
  const rows = (devs || []).map(d => ({ ...d }));
  const tb = h("tbody", {});
  const add = (d) => { const tr = h("tr", {}, cols.map(c => h("td", {}, h("input", { value: d[c] || "", "data-c": c, size: c === "name" ? 22 : 9, "aria-label": c })))); tr._d = d; tb.append(tr); };
  rows.forEach(add);
  return h("div", { class: "card" }, h("table", {}, h("thead", {}, h("tr", {}, cols.map(c => h("th", {}, c)))), tb),
    h("p", { class: "small mut" }, "source = narrative | log | sticker · slot = ชื่อกลุ่มเดียวกันของอุปกรณ์ชิ้นเดียวกัน (เช่น L1-stent1) · category = stent | dcb | balloon | atherectomy | imaging | wire | other"),
    h("button", { class: "sm", onclick: () => add({}) }, "+ แถว"), " ",
    h("button", { class: "sm pri", onclick: async () => { const out = [...tb.children].map(tr => ({ ...tr._d, ...Object.fromEntries([...tr.querySelectorAll("input")].map(i => [i.dataset.c, i.value.trim()])) })).filter(d => d.name || d.slot);
      refreshWith(await run(() => api("PUT", "/cases/" + id + "/devices", { devices: out }), "บันทึกอุปกรณ์แล้ว")); } }, "บันทึกอุปกรณ์"));
}

/* ---- รายการแก้ & addendum ---- */
function tabFix(id, rep) {
  const ev = rep.evaluation; if (!ev) return h("div", { class: "card" }, "ยังไม่มีผลประเมิน");
  const act = ev.findings.filter(f => !f.overridden && !f.needs_review);
  const seen = new Set(), list = act.filter(f => { const k = f.rule_id.split("@")[0]; if (f.kind === "completeness" || seen.has(k)) return false; seen.add(k); return true; });
  const add = rep.addendum || { items: [] };
  return h("div", {}, h("div", { class: "card" }, h("h2", { style: "margin-top:0" }, "ลำดับการแก้ (เรียงตามระดับและคะแนนที่จะได้เพิ่ม)"),
    list.length ? h("ol", {}, list.map(f => h("li", {}, [badge(SEV[f.severity][0], SEV[f.severity][1]), " ", h("b", {}, f.rule_id), " ", f.fix, f.impact > 0 && h("span", { class: "small mut" }, " (+" + f.impact.toFixed(2) + ")")]))) : h("p", { class: "mut" }, "ไม่มีรายการที่ต้องแก้ 🎉")),
    ev.red_rules.length > 0 && h("div", { class: "card" }, "ต้องแก้ rule ระดับแดงให้ครบก่อนปลดล็อก: ", ev.red_rules.join(", ")),
    h("div", { class: "card" }, h("h2", { style: "margin-top:0" }, "ร่าง Addendum"), h("p", { class: "small mut" }, add.header),
      add.items.map(i => h("div", {}, h("div", { class: "small" }, badge(SEV[i.severity][0], SEV[i.severity][1]), " " + i.rule_id + (i.needs_input ? " · ต้องกรอกเพิ่ม " + i.needs_input + " ช่อง" : "")),
        h("div", { class: "addendum" }, i.text), h("button", { class: "sm", onclick: () => navigator.clipboard.writeText(i.text).then(() => toast("คัดลอกแล้ว")) }, "คัดลอก"))),
      add.items.length === 0 && h("p", { class: "mut" }, "ไม่มี")));
}

/* ---- coding ---- */
function tabCoding(id, rep) {
  const c = rep.coding; if (!c) return h("div", { class: "card" }, "ยังไม่มีข้อมูล");
  const tbl = (rows) => h("table", {}, h("thead", {}, h("tr", {}, ["รหัสที่เสนอ", "ความหมาย", "เหตุผล", "สถานะ"].map(t => h("th", {}, t)))), h("tbody", {}, rows.map(r => h("tr", {}, h("td", {}, h("b", {}, r.code)), h("td", {}, r.desc), h("td", { class: "small" }, r.reason), h("td", {}, badge(r.status, r.status === "ส่งแล้ว" ? "b-grn" : "b-yel"))))));
  const claim = ["coder", "admin", "auditor"].includes(ME.role) && h("div", { class: "card row" }, "ผลการเบิก: ", h("select", { onchange: async e => run(() => api("POST", "/cases/" + id + "/claim", { status: e.target.value }), "บันทึกแล้ว") },
    ["pending", "approved", "denied"].map(s => h("option", { value: s, selected: rep.claim_status === s }, { pending: "รอผล", approved: "อนุมัติ", denied: "ถูกปฏิเสธ" }[s]))));
  return h("div", {}, h("p", { class: "small mut" }, c.note), h("h2", {}, "ICD-10"), tbl(c.icd10), h("h2", {}, "ICD-9-CM"), tbl(c.icd9cm),
    h("p", {}, "ที่ส่งแล้ว: ICD-10 = " + (c.submitted.icd10.join(", ") || "—") + " · ICD-9-CM = " + (c.submitted.icd9cm.join(", ") || "—")),
    (c.extra_submitted.icd10.length + c.extra_submitted.icd9cm.length > 0) && h("p", {}, badge("ที่ส่งแต่ไม่พบเหตุผลรองรับใน field", "b-yel"), " " + c.extra_submitted.icd10.concat(c.extra_submitted.icd9cm).join(", ")),
    h("h2", {}, "อุปกรณ์ที่เบิก เทียบกับ sticker"), h("table", {}, h("thead", {}, h("tr", {}, ["ประเภท", "narrative", "log", "sticker", ""].map(t => h("th", {}, t)))),
      h("tbody", {}, c.devices.map(d => h("tr", {}, h("td", {}, d.category), h("td", {}, d.narrative), h("td", {}, d.log), h("td", {}, d.sticker), h("td", {}, d.consistent ? badge("ตรงกัน", "b-grn") : badge("ไม่ตรง", "b-red")))))), claim);
}

/* ---- ต้นฉบับ / ส่งประมวลผล ---- */
function tabSource(id, rep) {
  const pages = rep.pages || [];
  const hasText = pages.some(p => p.text.trim());
  const t = h("input", { type: "checkbox", id: "ct" }), im = h("input", { type: "checkbox", id: "ci" });
  const btn = h("button", { class: "pri", onclick: async () => {
    btn.disabled = true; btn.textContent = "กำลังประมวลผลบน Cloud API… (อาจใช้เวลา 1–3 นาที)";
    const r = await run(() => api("POST", "/cases/" + id + "/extract", { confirm_text: t.checked, confirm_images: im.checked }), "ประมวลผลเสร็จ — ตรวจและยืนยัน field ที่แท็บ 'ข้อมูล'");
    if (r) { state.tab = "report"; refreshWith(r); } else { btn.disabled = false; btn.textContent = "ส่งประมวลผลด้วย AI"; } } }, "ส่งประมวลผลด้วย AI");
  return h("div", {}, h("div", { class: "card" },
    h("h2", { style: "margin-top:0" }, "ตรวจ preview ข้อมูลที่จะส่งออกไป Cloud API"),
    rep.residual_phi && rep.residual_phi.length > 0 && h("div", { class: "err" }, "⚠ ยังพบรูปแบบข้อมูลส่วนบุคคลที่อาจเหลืออยู่: " + rep.residual_phi.join(", ") + " — ตรวจและเพิ่มชื่อที่ต้องซ่อนก่อนอัปโหลดใหม่"),
    hasText ? h("p", {}, "ข้อความด้านล่างผ่านการซ่อนชื่อ/HN/DOB แล้ว (mapping เก็บเฉพาะในเซิร์ฟเวอร์โรงพยาบาล) และนี่คือสิ่งเดียวที่จะถูกส่งไป") :
      h("p", {}, badge("ภาพสแกน", "b-yel"), " ไม่มีข้อความที่ซ่อนข้อมูลได้ — ภาพทั้งหน้าจะถูกส่งไป Cloud API (ซ่อนชื่อ/HN ในภาพไม่ได้) ต้องมี DPA กับผู้ให้บริการ"),
    h("label", {}, t, " ฉันตรวจ preview แล้วและอนุญาตให้ส่งประมวลผล"),
    !hasText && h("div", {}, h("label", {}, im, " ฉันรับทราบว่าภาพจะถูกส่งทั้งหน้าและโรงพยาบาลมี DPA แล้ว")), h("p", {}, btn)),
    pages.map(p => h("details", { class: "card", open: pages.length < 4 }, h("summary", {}, "หน้า " + p.page), p.text.trim() ? h("pre", { class: "page" }, p.text) : h("p", { class: "mut" }, "(ไม่มีข้อความ)"))),
    !hasText && pages.map(p => h("img", { src: "/api/cases/" + id + "/image/" + p.page, alt: "หน้า " + p.page, style: "max-width:100%;border:1px solid var(--line);margin-bottom:8px", loading: "lazy" })));
}
async function tabAudit(id) {
  const rows = await run(() => api("GET", "/cases/" + id + "/audit")) || [];
  return h("div", { class: "card" }, h("table", {}, h("thead", {}, h("tr", {}, ["เวลา (UTC)", "ผู้ใช้", "การกระทำ", "รายละเอียด"].map(t => h("th", {}, t)))),
    h("tbody", {}, rows.map(r => h("tr", {}, h("td", { class: "small" }, r.ts.replace("T", " ")), h("td", {}, r.user), h("td", {}, r.action), h("td", { class: "small" }, r.detail || ""))))));
}

/* ---------- dashboard ---------- */
async function viewDashboard() {
  const d = await run(() => api("GET", "/dashboard"));
  if (!d) return;
  const o = d.overall, kpi = (label, v, unit = "%") => h("div", { class: "card" }, h("div", { class: "small mut" }, label), h("div", { class: "score" }, v == null ? "—" : v + unit));
  mount(h("h1", {}, "QC Dashboard"), h("p", { class: "small mut" }, d.note),
    h("div", { class: "grid" }, kpi("จำนวนเคส", o.cases, ""), kpi("Overall score เฉลี่ย", o.mean_overall, ""), kpi("เคสที่ score ≥ 7.0", o.pct_ge_7),
      kpi("LM/MVD ที่มี Heart Team note", o.pct_lmmvd_heart_team), kpi("เคสที่มี vasopressor แต่ไม่มีเหตุผล", o.pct_vasopressor_no_reason),
      kpi("STEMI ที่มี D2B", o.pct_stemi_with_d2b), kpi("STEMI ที่ D2B ≤ 90 นาที", o.pct_stemi_d2b_le_90), kpi("ใช้ imaging และมี MSA หลัง PCI", o.pct_imaging_with_msa),
      kpi("มี contrast/eGFR", o.pct_contrast_egfr), kpi("มี Air Kerma", o.pct_air_kerma), kpi("อัตราการถูกปฏิเสธเบิก (n=" + o.claims_known + ")", o.claim_denial_rate)),
    d.close_loop && h("div", { class: "card" }, "Close loop: คะแนนเฉลี่ยก่อนแก้ " + d.close_loop.before + " → หลังแก้ " + d.close_loop.after + " (" + d.close_loop.cases + " เคส)"),
    h("h2", {}, "แนวโน้มรายเดือน"), h("div", { class: "card" }, h("table", {}, h("thead", {}, h("tr", {}, ["เดือน", "เคส", "คะแนนเฉลี่ย", "% ≥7.0", "% Heart Team", "% D2B≤90", "% MSA", "% Air Kerma"].map(t => h("th", {}, t)))),
      h("tbody", {}, d.monthly.map(m => h("tr", {}, h("td", {}, m.month), h("td", {}, m.cases), h("td", {}, fmt(m.mean_overall)), h("td", {}, fmt(m.pct_ge_7)), h("td", {}, fmt(m.pct_lmmvd_heart_team)), h("td", {}, fmt(m.pct_stemi_d2b_le_90)), h("td", {}, fmt(m.pct_imaging_with_msa)), h("td", {}, fmt(m.pct_air_kerma))))))),
    h("h2", {}, "Rule ที่ fire บ่อยที่สุด"), h("div", { class: "card" }, h("table", {}, h("tbody", {}, d.top_rules.map(r => h("tr", {}, h("td", {}, h("b", {}, r.rule_id)), h("td", {}, h("div", { class: "bar" }, h("i", { style: "width:" + r.pct + "%" }))), h("td", { class: "n" }, r.cases + " เคส (" + r.pct + "%)")))))),
    h("h2", {}, "ข้อมูลบังคับที่ขาดบ่อยที่สุด"), h("div", { class: "card" }, h("table", {}, h("tbody", {}, d.top_missing.map(r => h("tr", {}, h("td", {}, h("b", {}, r.rule_id.replace(/^C-/, ""))), h("td", {}, h("div", { class: "bar" }, h("i", { style: "width:" + r.pct + "%" }))), h("td", { class: "n" }, r.cases + " เคส (" + r.pct + "%)")))))),
    h("h2", {}, "Systemic findings — ต้องแก้ที่ template ของระบบรายงาน cath lab"), h("div", { class: "card" }, h("table", {}, h("tbody", {}, d.systemic.map(s => h("tr", {}, h("td", {}, s.flag ? badge("แก้ที่ template", "b-red") : badge("ติดตาม", "b-gry")), h("td", {}, h("b", {}, s.title), h("div", { class: "small mut" }, "rule: " + s.rules.join(", ") + " → " + s.fix)), h("td", { class: "n" }, s.cases + " เคส (" + fmt(s.pct) + "%)")))))),
    h("h2", {}, "แยกตาม case type"), h("div", { class: "card" }, h("table", {}, h("tbody", {}, d.by_case_type.map(t => h("tr", {}, h("td", {}, t.case_type), h("td", { class: "n" }, t.cases + " เคส"), h("td", { class: "n" }, "เฉลี่ย " + t.mean_overall)))))),
    h("h2", {}, "เปรียบเทียบรายแพทย์ (นิรนาม · เรียงตามตัวอักษร ไม่ใช่ ranking)"), h("div", { class: "card" }, h("table", {}, h("tbody", {}, d.physicians.map(p => h("tr", {}, h("td", {}, p.alias), h("td", { class: "n" }, p.cases + " เคส"), h("td", { class: "n" }, "เฉลี่ย " + p.mean_overall)))))));
}

/* ---------- rules ---------- */
function viewRules() {
  const rs = CFG.ruleset;
  mount(h("h1", {}, "กฎ & เกณฑ์ (explainable rules)"),
    h("div", { class: "card" }, h("p", {}, h("b", {}, "Ruleset " + rs.version), " · มีผล " + rs.effective_date + " · " + rs.status), h("p", { class: "small" }, "Rule owner: " + rs.owner),
      h("p", { class: "small mut" }, "ทุก rule ทำงานบน structured field เท่านั้น ไม่ใช้คะแนนจากโมเดลแบบ black box · โมเดล AI ใช้เฉพาะขั้นดึงข้อมูลจากไฟล์ พร้อม source span และ confidence"),
      h("details", {}, h("summary", {}, "Threshold ที่ใช้อยู่"), h("pre", { class: "page" }, JSON.stringify(rs.thresholds, null, 2))),
      h("details", {}, h("summary", {}, "นโยบายคะแนน/ระดับ"), h("pre", { class: "page" }, JSON.stringify(rs.policy, null, 2)))),
    h("div", { class: "card" }, h("table", {}, h("thead", {}, h("tr", {}, ["ID", "ระดับ", "Domain", "เงื่อนไข", "แนวทางอ้างอิง", "วิธีแก้"].map(t => h("th", {}, t)))),
      h("tbody", {}, CFG.rules.map(r => h("tr", {}, h("td", {}, h("b", {}, r.id)), h("td", {}, badge(SEV[r.severity][0], SEV[r.severity][1])), h("td", {}, r.domain), h("td", {}, r.title), h("td", { class: "small" }, r.guideline), h("td", { class: "small" }, r.fix)))))));
}

boot();
