import io, json, os, time
import requests
import streamlit as st
from pypdf import PdfReader, PdfWriter


def get_key(name):
    try:
        return st.secrets[name]
    except Exception:
        return os.environ.get(name)


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
        raise RuntimeError(f"submit {r.status_code}: {r.text[:500]}")
    job_id = r.json()["job_id"]
    t0 = time.time()
    status, s = "", None
    while time.time() - t0 < timeout:
        s = requests.get(f"{API}/{job_id}/status", headers=h, timeout=30)
        if s.status_code >= 400:
            raise RuntimeError(f"status {s.status_code}: {s.text[:500]}")
        status = str(s.json().get("status", "")).lower()
        if status in ("completed", "partially_completed", "failed", "rejected"):
            break
        time.sleep(3)
    else:
        raise RuntimeError("timed out waiting for the job")
    if status not in ("completed", "partially_completed"):
        raise RuntimeError(f"job {status}: {s.text[:500]}")
    res = requests.get(f"{API}/{job_id}/results", headers=h, timeout=30)
    if res.status_code >= 400:
        raise RuntimeError(f"results {res.status_code}: {res.text[:500]}")
    return status, res.json()


st.title("Report reader test")
f = st.file_uploader("Upload a blood report (PDF, PNG, JPG)", type=["pdf", "png", "jpg", "jpeg"])

if f and st.button("Read report"):
    data, mime, pages = f.getvalue(), f.type, 1
    if f.name.lower().endswith(".pdf"):
        data, pages = slim_pdf(data)
        mime = "application/pdf"
    st.write("Pages sent:", pages)
    t0 = time.time()
    try:
        with st.spinner("Reading..."):
            status, out = sarvam_extract(f.name, data, mime)
        st.write("Status:", status, "| seconds:", round(time.time() - t0, 1))
        st.write(out.get("result", out))
    except Exception as e:
        st.error(str(e)[:800])
