import sys
from pathlib import Path

import streamlit as st

# Ensure project root imports work
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ui._bootstrap import require_auth  # type: ignore
from ui._dashboard import render_dashboard  # type: ignore

st.set_page_config(page_title="Dashboard", page_icon="📊", layout="wide")
require_auth(login_here=True)
render_dashboard()
