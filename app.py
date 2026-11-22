import os
import requests
import streamlit as st

def get_key(name):
    try:
        return st.secrets[name]
    except Exception:
        return os.environ.get(name)

st.title("Key test")

if st.button("Test Sarvam"):
    r = requests.post(
        "https://api.sarvam.ai/v1/chat/completions",
        headers={"api-subscription-key": get_key("SARVAM_API_KEY") or ""},
        json={"model": "sarvam-105b",
              "messages": [{"role": "user", "content": "Reply OK"}]},
        timeout=60,
    )
    st.write("Status:", r.status_code)
    st.code(r.text[:600])

if st.button("Test Divyam"):
    r = requests.get(
        "https://api.preview.divyam.ai/v1/models",
        headers={"Authorization": "Bearer " + (get_key("DIVYAM_API_KEY") or "")},
        timeout=60,
    )
    st.write("Status:", r.status_code)
    st.code(r.text[:600])
