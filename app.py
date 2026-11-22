import hashlib, html, io, json, os, time
import requests
import streamlit as st
from pypdf import PdfReader, PdfWriter

# =====================================================================
# SCHEMA sent to Sarvam Document AI Extract
# =====================================================================


def num(desc):
    return {"type": "number", "description": desc}


def txt(desc):
    return {"type": "string", "description": desc}


SCHEMA = {"type": "object", "properties": {
    "patient_age_years": num("Patient age in years from the patient name line, e.g. 35 from (35Y/M)"),
    "patient_sex": txt("M or F from the patient name line, e.g. M from (35Y/M)"),
    "vitamin_d_value": num("Result value of 25-OH VITAMIN D (TOTAL), digits only"),
    "vitamin_d_unit": txt("Unit printed next to the 25-OH VITAMIN D result, exactly as printed"),
    "b12_value": num("Result value of VITAMIN B-12, digits only"),
    "b12_unit": txt("Unit printed next to the VITAMIN B-12 result, exactly as printed"),
    "calcium_value": num("Result value of serum CALCIUM, digits only"),
    "calcium_unit": txt("Unit printed next to CALCIUM, exactly as printed"),
    "calcium_ref_low": num("Lower end of the printed reference range for CALCIUM"),
    "calcium_ref_high": num("Upper end of the printed reference range for CALCIUM"),
    "egfr_value": num("Result value of EST. GLOMERULAR FILTRATION RATE (eGFR), digits only"),
    "egfr_unit": txt("Unit printed next to eGFR, exactly as printed"),
}}

SAMPLE = {
    "patient_age_years": 35, "patient_sex": "M",
    "vitamin_d_value": 5.9, "vitamin_d_unit": "ng/mL",
    "b12_value": 151, "b12_unit": "pg/mL",
    "calcium_value": 9.52, "calcium_unit": "mg/dL",
    "calcium_ref_low": 8.8, "calcium_ref_high": 10.6,
    "egfr_value": 122, "egfr_unit": "mL/min/1.73 m2",
}

# =====================================================================
# RULES (same content as your rules table; edit text here)
# action: DOSE, NO_SUPPLEMENT, REFER
# =====================================================================

D3_WEEKLY_2M = "Vitamin D3 60K: one capsule once a week for 2 months, then one a month"
D3_WEEKLY_3M = "Vitamin D3 60K: one capsule once a week for 3 months, then one a month"
CLIN_D_8W = "60,000 IU once a week for 8 weeks, then 60,000 IU once a month"
CLIN_D_12W = "60,000 IU once a week for 12 weeks, then 60,000 IU once a month"

RULES_D = [
    dict(id="D1", test=lambda v: v >= 100, status="Above range", action="REFER",
         clinical="No dose", plain="Do not take vitamin D. Please see your doctor.", pills="",
         basis="Lab toxicity line is above 100 ng/mL; Indian consensus gives toxic above 150 (sources conflict)."),
    dict(id="D2", test=lambda v: 30 <= v < 100, status="Sufficient", action="NO_SUPPLEMENT",
         clinical="No supplement needed", plain="No vitamin D supplement needed.", pills="",
         basis="Lab sufficiency band 30-100 ng/mL."),
    dict(id="D3", test=lambda v: 20 <= v < 30, status="Insufficient", action="DOSE",
         clinical=CLIN_D_8W, plain=D3_WEEKLY_2M, pills="1 weekly capsule",
         basis="Indian consensus (IJEM 2025), insufficiency regimen."),
    dict(id="D4", test=lambda v: 10 <= v < 20, status="Deficient", action="DOSE",
         clinical=CLIN_D_12W, plain=D3_WEEKLY_3M, pills="1 weekly capsule",
         basis="Indian consensus (IJEM 2025), deficiency regimen."),
    dict(id="D5", test=lambda v: v < 10, status="Severely deficient", action="DOSE",
         clinical=CLIN_D_12W, plain=D3_WEEKLY_3M, pills="1 weekly capsule",
         basis="Indian consensus gives no separate dose below 10 ng/mL; a single loading dose of 1 lakh IU or more is not recommended in India. Doctor review recommended."),
]

RULES_B12 = [
    dict(id="B1", test=lambda v: v > 911, status="Above range", action="REFER",
         clinical="No dose", plain="No vitamin B12 supplement needed. Please check with your doctor.", pills="",
         basis="Lab range 211-911 pg/mL."),
    dict(id="B2", test=lambda v: 300 <= v <= 911, status="Within range", action="NO_SUPPLEMENT",
         clinical="No supplement needed", plain="No vitamin B12 supplement needed.", pills="",
         basis="Other labs also call 300 pg/mL or above normal."),
    dict(id="B3", test=lambda v: 211 <= v < 300, status="Borderline", action="REFER",
         clinical="No dose from tool",
         plain="Within range for this lab. Ask your doctor if you take metformin or acid-reducing medicine.", pills="",
         basis="Lab range 211-911; other labs call 200-299 borderline; no dose found for borderline."),
    dict(id="B4", test=lambda v: 200 <= v < 211, status="Just below range", action="REFER",
         clinical="No dose from tool", plain="Slightly low for this lab. Please see your doctor.", pills="",
         basis="Between 200 and 211 the lab and other labs disagree; no dose found."),
    dict(id="B5", test=lambda v: v < 200, status="Deficient", action="DOSE",
         clinical="Oral 1000 to 2000 mcg once a day",
         plain="Vitamin B12 (methylcobalamin) tablet: one a day", pills="1 daily tablet",
         basis="JAPI Delphi: oral maintenance 1000-2000 mcg per day after correction; no band-by-band starting oral dose found."),
]

NOTE_D6 = "Tell your doctor if you have obesity or malabsorption, or take anticonvulsants or steroids. Higher vitamin D doses may be needed."
NOTE_B6 = "If you have numbness, tingling or balance trouble, see a doctor first. Injections are usually given first for B12 deficiency."
NOTE_B7 = "Tell your doctor which medicines you take, especially metformin or acid-reducing medicines."


def find_rule(rules, v):
    for r in rules:
        if r["test"](v):
            return r
    return None


def norm_unit(u):
    return str(u or "").lower().replace(" ", "")


def tonum(x):
    try:
        if x is None or str(x).strip() == "":
            return None
        return float(str(x).strip())
    except Exception:
        return None


def evaluate(v, pregnant):
    """v keys: age, d, d_unit, b12, b12_unit, ca, ca_high, egfr. Returns dict."""
    out = {"stop": None, "items": [], "notices": [], "notes": []}

    # G2 age (age is required)
    if v.get("age") is None:
        out["stop"] = ("warning", "Age not found",
                       "Please enter your age in the values box above.")
        return out
    if v["age"] < 18:
        out["stop"] = ("warning", "Refer to doctor",
                       "This tool is for adults. Please see your doctor.")
        return out
    # G3 pregnancy
    if pregnant:
        out["stop"] = ("warning", "Not covered",
                       "This tool does not cover pregnancy or breastfeeding. Please see your doctor.")
        return out
    # nothing found
    if v.get("d") is None and v.get("b12") is None:
        out["stop"] = ("warning", "Cannot read this report",
                       "Could not find Vitamin D or Vitamin B12. Please check the values box above or upload a report that shows both.")
        return out
    # G1 units
    bad = False
    if v.get("d") is not None and norm_unit(v.get("d_unit")) != "ng/ml":
        bad = True
    if v.get("b12") is not None and norm_unit(v.get("b12_unit")) != "pg/ml":
        bad = True
    if bad:
        out["stop"] = ("warning", "Cannot read this report",
                       "Please upload a report that shows the value in ng/mL (vitamin D) or pg/mL (vitamin B12).")
        return out

    # G6 missing calcium / eGFR
    ca_ok = v.get("ca") is not None and v.get("ca_high") is not None
    egfr_ok = v.get("egfr") is not None
    if not ca_ok or not egfr_ok:
        out["notices"].append("Kidney and calcium check not done. Kidney and calcium values were not on your report.")

    # Vitamin D
    if v.get("d") is not None:
        if ca_ok and v["ca"] > v["ca_high"]:
            out["items"].append(dict(
                nutrient="Vitamin D", value=v["d"], unit="ng/mL", id="G4", status="Refer to doctor first",
                action="REFER", clinical="No dose",
                plain="High calcium on your report. Please see a doctor before taking vitamin D.",
                pills="", basis="Vitamin D is not suggested when calcium is high."))
        elif egfr_ok and v["egfr"] < 60:
            out["items"].append(dict(
                nutrient="Vitamin D", value=v["d"], unit="ng/mL", id="G5", status="Refer to doctor first",
                action="REFER", clinical="No dose",
                plain="Your kidney test is reduced. Please see a doctor before taking vitamin D.",
                pills="", basis="Vitamin D with eGFR under 60 needs monitoring."))
        else:
            r = find_rule(RULES_D, v["d"])
            out["items"].append(dict(nutrient="Vitamin D", value=v["d"], unit="ng/mL", id=r["id"],
                                     status=r["status"], action=r["action"], clinical=r["clinical"],
                                     plain=r["plain"], pills=r["pills"], basis=r["basis"]))
            if r["action"] == "DOSE":
                out["notes"].append(NOTE_D6)
    else:
        out["items"].append(dict(nutrient="Vitamin D", value=None, unit="ng/mL", id="-", status="Not found",
                                 action="MISSING", clinical="", plain="Vitamin D was not found on this report.",
                                 pills="", basis=""))

    # B12
    if v.get("b12") is not None:
        r = find_rule(RULES_B12, v["b12"])
        out["items"].append(dict(nutrient="Vitamin B12", value=v["b12"], unit="pg/mL", id=r["id"],
                                 status=r["status"], action=r["action"], clinical=r["clinical"],
                                 plain=r["plain"], pills=r["pills"], basis=r["basis"]))
        if r["id"] == "B5":
            out["notes"].append(NOTE_B6)
        if r["action"] == "DOSE":
            out["notes"].append(NOTE_B7)
    else:
        out["items"].append(dict(nutrient="Vitamin B12", value=None, unit="pg/mL", id="-", status="Not found",
                                 action="MISSING", clinical="", plain="Vitamin B12 was not found on this report.",
                                 pills="", basis=""))
    return out


# =====================================================================
# Reading the report with Sarvam Document AI (Extract)
# =====================================================================


def get_key(name):
    try:
        return st.secrets[name]
    except Exception:
        return os.environ.get(name)


def slim_pdf(data):
    """Keep only the pages with the values we need. Falls back to the first 10 pages."""
    reader = PdfReader(io.BytesIO(data))
    n = len(reader.pages)
    if n <= 8:
        return data, n
    keep = []
    for i, p in enumerate(reader.pages):
        t = (p.extract_text() or "").upper()
        if (("25-OH VITAMIN D" in t and "C.M.I.A" in t)
                or ("VITAMIN B-12" in t and "C.L.I.A" in t)
                or ("CALCIUM" in t and "CREATININE" in t)
                or "GLOMERULAR FILTRATION RATE" in t):
            keep.append(i)
    if len(keep) < 3:
        keep = list(range(min(n, 10)))
    w = PdfWriter()
    for i in keep[:10]:
        w.add_page(reader.pages[i])
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue(), len(keep[:10])


API = "https://api.sarvam.ai/doc-ai/v1/job"


def sarvam_extract(name, data, mime, timeout=150):
    h = {"api-subscription-key": get_key("SARVAM_API_KEY") or ""}
    r = requests.post(
        f"{API}/extract", headers=h,
        files={"file": (name, data, mime)},
        data={"schema": json.dumps(SCHEMA), "language": "en-IN",
              "output_format": "json", "mode": "basic"},
        timeout=60)
    if r.status_code >= 400:
        raise RuntimeError(f"submit {r.status_code}: {r.text[:300]}")
    job_id = r.json()["job_id"]
    t0 = time.time()
    status, s = "", None
    while time.time() - t0 < timeout:
        s = requests.get(f"{API}/{job_id}/status", headers=h, timeout=30)
        if s.status_code >= 400:
            raise RuntimeError(f"status {s.status_code}: {s.text[:300]}")
        status = str(s.json().get("status", "")).lower()
        if status in ("completed", "partially_completed", "failed", "rejected"):
            break
        time.sleep(3)
    else:
        raise RuntimeError("timed out waiting for the report to be read")
    if status not in ("completed", "partially_completed"):
        raise RuntimeError(f"job {status}")
    res = requests.get(f"{API}/{job_id}/results", headers=h, timeout=30)
    if res.status_code >= 400:
        raise RuntimeError(f"results {res.status_code}: {res.text[:300]}")
    body = res.json()
    return body.get("result", body)


# =====================================================================
# Display helpers (design: calm blue, cards, status chips)
# =====================================================================

REF = {"Vitamin D": (30, 100, "ng/mL"), "Vitamin B12": (211, 911, "pg/mL")}

# chip kind, label per rule id
CHIP = {
    "D1": ("safety", "Above range"), "D2": ("normal", "Normal"), "D3": ("low", "Low"),
    "D4": ("low", "Low"), "D5": ("low", "Very low"),
    "B1": ("borderline", "Above range"), "B2": ("normal", "Normal"), "B3": ("borderline", "Borderline"),
    "B4": ("borderline", "Borderline"), "B5": ("low", "Low"),
    "G4": ("safety", "See doctor first"), "G5": ("safety", "See doctor first"),
}


def chip_for(it):
    if it["action"] == "MISSING":
        return ("grey", "Not tested")
    return CHIP.get(it["id"], ("grey", it["status"]))


def how_far(it):
    if it["value"] is None:
        return "Not in your report."
    lo, hi, _u = REF[it["nutrient"]]
    v = it["value"]
    if v < lo:
        return f"{lo - v:g} below the lower limit"
    if v > hi:
        return f"{v - hi:g} above the upper limit"
    return "Within the reference range"


def summary_line(items):
    tested = [i for i in items if i["value"] is not None]
    n_low = sum(1 for i in tested if chip_for(i)[0] == "low")
    if n_low:
        return f"{n_low} of {len(tested)} {'is' if n_low == 1 else 'are'} below range."
    if any(chip_for(i)[0] in ("safety", "borderline") for i in tested):
        return "Please read the notes below."
    if tested:
        return "Everything tested is in range."
    return "We could not find your values."


CSS = """
<style>
.stApp{background:#F5F9FF;}
html, body, .stApp, .stMarkdown, p, label, input, button, textarea{
  font-family:Inter,system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif !important;}
.stApp, .stMarkdown, p, label{color:#0F2A4A;}
.block-container{max-width:520px;padding:4rem 1rem 3rem 1rem;}
[data-testid="stHeader"]{background:transparent;}
#MainMenu, footer{visibility:hidden;}
.h1{font-size:30px;line-height:1.2;font-weight:700;color:#0F2A4A;margin:0.5rem 0 0.4rem 0;}
.sub{font-size:16px;color:#5B6B80;margin:0 0 1rem 0;}
.sec{font-size:14px;font-weight:600;color:#5B6B80;letter-spacing:.04em;text-transform:uppercase;margin:1.4rem 0 .2rem 0;}
.summary{font-size:22px;font-weight:700;color:#0F2A4A;margin:.4rem 0 .6rem 0;}
.card{background:#fff;border:1px solid #DCE8F8;border-radius:12px;padding:16px;margin:12px 0;}
.row{display:flex;justify-content:space-between;align-items:center;gap:8px;}
.name{font-size:16px;font-weight:600;color:#0F2A4A;}
.num{font-size:38px;font-weight:700;color:#0F2A4A;line-height:1.1;margin:10px 0 2px 0;}
.unit{font-size:16px;font-weight:500;color:#5B6B80;margin-left:6px;}
.muted{font-size:14px;color:#5B6B80;margin-top:2px;}
.chip{display:inline-block;padding:4px 12px;border-radius:999px;font-size:14px;font-weight:600;border:1px solid transparent;white-space:nowrap;}
.chip.low{background:#FEF3E2;color:#B45309;border-color:#F59E0B;}
.chip.borderline{background:#FEF9E0;color:#854D0E;border-color:#CA8A04;}
.chip.normal{background:#E8F6EC;color:#166534;border-color:#16A34A;}
.chip.grey{background:#F1F3F5;color:#4B5563;border-color:#9CA3AF;}
.chip.safety{background:#FDECEA;color:#B42318;border-color:#E5776E;}
.note{background:#FEF3E2;border:1px solid #F5C77E;border-radius:12px;padding:14px 16px;margin:12px 0;color:#7A4B07;font-size:16px;}
.plan{padding:0;overflow:hidden;}
.prow{padding:14px 16px;border-top:1px solid #DCE8F8;}
.prow:first-child{border-top:none;}
.pname{font-size:14px;font-weight:600;color:#5B6B80;}
.ptext{font-size:16px;color:#0F2A4A;margin-top:2px;}
.pgrey{background:#F8F9FA;}
.pgrey .ptext{color:#5B6B80;}
.quiet{font-size:14px;color:#5B6B80;margin:6px 0;}
.stButton, [data-testid="stButton"], [data-testid="stElementContainer"]:has(> [data-testid="stButton"]){width:100% !important;}
.stButton>button, [data-testid="stBaseButton-secondary"]{width:100% !important;min-height:52px;border-radius:12px;font-size:16px;font-weight:600;
  border:1px solid #2563EB;color:#2563EB;background:#fff;}
.stButton>button:hover{background:#EEF4FF;border-color:#2563EB;color:#1D4ED8;}
[data-testid="stFileUploaderDropzone"]{background:#fff;border:1.5px dashed #9DB8E8;border-radius:12px;padding:26px 16px;}
[data-testid="stFileUploaderDropzone"] button{border-radius:12px;min-height:44px;}
[data-testid="stExpander"]{background:#fff;border:1px solid #DCE8F8;border-radius:12px;}
[data-testid="stExpander"] summary{font-size:16px;font-weight:600;}
input{font-size:16px !important;}
</style>
"""


def card_html(it):
    kind, label = chip_for(it)
    name = html.escape(it["nutrient"])
    if it["value"] is None:
        body = '<div class="num">Not in your report</div>'
        ref = ""
    else:
        lo, hi, u = REF[it["nutrient"]]
        body = f'<div class="num">{it["value"]:g}<span class="unit">{html.escape(u)}</span></div>'
        ref = (f'<div class="muted">Reference range: {lo} to {hi}</div>'
               f'<div class="muted">{html.escape(how_far(it))}</div>')
    return (f'<div class="card"><div class="row"><span class="name">{name}</span>'
            f'<span class="chip {kind}">{html.escape(label)}</span></div>{body}{ref}</div>')


def plan_html(items):
    rows = []
    for it in items:
        grey = it["action"] in ("REFER", "MISSING")
        if it["action"] == "DOSE":
            text = it["plain"]
        elif it["action"] == "NO_SUPPLEMENT":
            text = "No supplement needed"
        else:
            text = "Held back: talk to your doctor. " + it["plain"]
        cls = "prow pgrey" if grey else "prow"
        rows.append(f'<div class="{cls}"><div class="pname">{html.escape(it["nutrient"])}</div>'
                    f'<div class="ptext">{html.escape(text)}</div></div>')
    return '<div class="card plan">' + "".join(rows) + "</div>"

# ---- UI ----
st.set_page_config(page_title="Understand your blood test", layout="centered")
st.markdown(CSS, unsafe_allow_html=True)
st.markdown('<div class="h1">Understand your blood test in 1 minute.</div>'
            '<div class="sub">Upload your report. Get a simple supplement plan on this page.</div>',
            unsafe_allow_html=True)

pregnant = st.checkbox("I am pregnant or breastfeeding")
st.markdown('<div class="quiet">Pick the PDF from WhatsApp, or take a photo of the report.</div>',
            unsafe_allow_html=True)
f = st.file_uploader("Upload your report", type=["pdf", "png", "jpg", "jpeg"],
                     label_visibility="collapsed")
st.markdown('<div class="quiet">Your report is read once to pull out the values. This app does not save it.</div>',
            unsafe_allow_html=True)
if st.button("Try a sample report"):
    st.session_state["use_sample"] = True

st.session_state.setdefault("cache", {})
source, data_dict, read_error = None, None, None

if f is not None:
    raw = f.getvalue()
    source = hashlib.md5(raw).hexdigest()[:10]
    cache = st.session_state["cache"]
    if source not in cache:
        t0 = time.time()
        try:
            with st.spinner("Reading your report..."):
                if f.name.lower().endswith(".pdf"):
                    payload, _pages = slim_pdf(raw)
                    mime = "application/pdf"
                else:
                    payload, mime = raw, f.type
                cache[source] = ("ok", sarvam_extract(f.name, payload, mime), round(time.time() - t0, 1))
        except Exception as e:
            cache[source] = ("err", str(e)[:300], 0)
    kind, payload, secs = cache[source]
    if kind == "ok":
        data_dict = payload
    else:
        data_dict, read_error = {}, payload
elif st.session_state.get("use_sample"):
    source, data_dict = "sample", SAMPLE
    st.markdown('<div class="quiet">Showing a sample report with demo values.</div>', unsafe_allow_html=True)
else:
    st.stop()

if read_error:
    st.markdown('<div class="note">We could not read this report. Please type the values below. '
                f'({html.escape(read_error)})</div>', unsafe_allow_html=True)

need_check = bool(read_error) or any(
    data_dict.get(k) is None for k in ("patient_age_years", "vitamin_d_value", "b12_value"))
with st.expander("Check or edit the values", expanded=need_check):
    st.caption("If a value is wrong, fix it here and your plan updates.")

    def fld(label, key, default):
        return st.text_input(label, value="" if default is None else str(default), key=f"{source}_{key}")

    c1, c2, c3 = st.columns(3)
    with c1:
        age_s = fld("Age (years)", "age", data_dict.get("patient_age_years"))
        d_s = fld("Vitamin D", "d", data_dict.get("vitamin_d_value"))
        d_u = fld("Vitamin D unit", "du", data_dict.get("vitamin_d_unit"))
    with c2:
        b_s = fld("Vitamin B12", "b", data_dict.get("b12_value"))
        b_u = fld("B12 unit", "bu", data_dict.get("b12_unit"))
        egfr_s = fld("eGFR", "egfr", data_dict.get("egfr_value"))
    with c3:
        ca_s = fld("Calcium", "ca", data_dict.get("calcium_value"))
        cah_s = fld("Calcium upper limit", "cah", data_dict.get("calcium_ref_high"))

vals = {
    "age": tonum(age_s), "d": tonum(d_s), "d_unit": d_u, "b12": tonum(b_s), "b12_unit": b_u,
    "ca": tonum(ca_s), "ca_high": tonum(cah_s), "egfr": tonum(egfr_s),
}
res = evaluate(vals, pregnant)

if res["stop"]:
    _lvl, title, msg = res["stop"]
    st.markdown(f'<div class="note"><b>{html.escape(title)}.</b> {html.escape(msg)}</div>',
                unsafe_allow_html=True)
else:
    st.markdown('<div class="sec">Your results</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="summary">{html.escape(summary_line(res["items"]))}</div>',
                unsafe_allow_html=True)
    for it in res["items"]:
        st.markdown(card_html(it), unsafe_allow_html=True)

    st.markdown('<div class="sec">Your plan</div>', unsafe_allow_html=True)
    dose_items = [i for i in res["items"] if i["action"] == "DOSE"]
    if dose_items:
        kinds = " + ".join(i["pills"] for i in dose_items)
        st.markdown(f'<div class="summary">{html.escape(kinds)}</div>', unsafe_allow_html=True)
    st.markdown(plan_html(res["items"]), unsafe_allow_html=True)

    for n in res["notices"]:
        st.markdown(f'<div class="note">{html.escape(n)}</div>', unsafe_allow_html=True)
    if res["notes"]:
        st.markdown('<div class="sec">Tell your doctor if</div>', unsafe_allow_html=True)
        for n in res["notes"]:
            st.markdown(f'<div class="quiet" style="font-size:16px;color:#0F2A4A">{html.escape(n)}</div>',
                        unsafe_allow_html=True)

    with st.expander("For your doctor"):
        for it in res["items"]:
            if it["clinical"] or it["basis"]:
                st.markdown(f"**{it['nutrient']}** (rule {it['id']})")
                if it["clinical"]:
                    st.write("Clinical dose:", it["clinical"])
                if it["basis"]:
                    st.caption("Basis: " + it["basis"])
    st.markdown('<div class="quiet">Check with your doctor before you start any supplement.</div>',
                unsafe_allow_html=True)

st.divider()
st.markdown('<div class="quiet">Not a diagnosis. Doses come from a fixed rules table. '
            'AI only reads the report. Your report is sent to Sarvam AI to be read; this app does not save it.</div>',
            unsafe_allow_html=True)
