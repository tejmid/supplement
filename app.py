import io, json, os, time
import streamlit as st
from pypdf import PdfReader, PdfWriter
from sarvamai import SarvamAI

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

st.title("Report reader test")
f = st.file_uploader("Upload a blood report (PDF, PNG, JPG)", type=["pdf", "png", "jpg", "jpeg"])

if f and st.button("Read report"):
    data, mime, pages = f.getvalue(), f.type, 1
    if f.name.lower().endswith(".pdf"):
        data, pages = slim_pdf(data)
        mime = "application/pdf"
    st.write("Pages sent:", pages)
    client = SarvamAI(api_subscription_key=get_key("SARVAM_API_KEY"))
    t0 = time.time()
    try:
        job = client.doc_ai.extract(
            file=[(f.name, io.BytesIO(data), mime)],
            schema=json.dumps(SCHEMA), language="en-IN",
            output_format="json", mode="basic")
        with st.spinner("Reading..."):
            while True:
                s = client.doc_ai.get_status(job_id=job.job_id)
                if s.status.lower() in {"completed", "partially_completed", "failed", "rejected"}:
                    break
                if time.time() - t0 > 150:
                    break
                time.sleep(3)
        st.write("Status:", s.status, "| seconds:", round(time.time() - t0, 1))
        if s.status.lower() in ("completed", "partially_completed"):
            res = client.doc_ai.get_results(job_id=job.job_id)
            st.write(res.result)
    except Exception as e:
        st.error(str(e)[:600])
