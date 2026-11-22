# Supplement plan from a blood report

A patient uploads a blood report and sees a safety-gated Vitamin D and B12 plan on the same page. AI only reads the report. Fixed rules in code decide the doses.

Live demo: <your Streamlit link>

## How it works
1. Upload a PDF or photo (or tap "Try a sample report").
2. Sarvam Document AI reads the report into structured values (age, sex, Vitamin D, B12, calcium, eGFR).
3. A plain-Python rules engine checks safety gates (age, pregnancy, units, high calcium, low eGFR) and picks the dose and pill count. No AI chooses a dose.
4. Results and the plan show on the same page, with an editable values box if reading fails.

## Tools used
Streamlit (app and hosting on Streamlit Community Cloud), Sarvam Document AI, pypdf, Python. Built on the day.

## What is mocked or not done
- Dose rules are not clinician reviewed. Some source links are placeholders.
- Magnesium is not included. Only Vitamin D and B12.
- Pregnancy shows a doctor referral, not a plan.
- Borderline B12 says "see your doctor" with no dose.
- Pill sizes and the product format are assumptions.
- Photo uploads are not fully verified.

## Privacy
The app does not save the report. Sarvam reads it and may keep uploads for a period set by Sarvam.

Not medical advice. Check with your doctor.
