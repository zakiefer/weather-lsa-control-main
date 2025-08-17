import streamlit as st

st.set_page_config(page_title="Map Layers (deprecated)")
st.title("Map Layers")
st.caption("This page is deprecated. Redirecting you to the Map…")

# Redirect immediately to the main Map page to avoid duplicate sidebar entries
try:
    st.switch_page("pages/Map.py")
except Exception:
    # Fallback: instruct the user to open Map manually
    st.info("Open the ‘Map’ page in the sidebar to manage layers and opacity.")

st.stop()
