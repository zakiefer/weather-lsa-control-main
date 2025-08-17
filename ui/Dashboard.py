import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Use the shared auth + sidebar builder so Dashboard matches other pages
st.set_page_config(page_title="Dashboard", page_icon="📊", layout="wide")
from ui._bootstrap import require_auth  # noqa: E402

try:  # Prefer absolute when running as a package
    from ui._dashboard import render_dashboard  # type: ignore  # noqa: E402
except Exception:  # Fallback for direct runs
    from _dashboard import render_dashboard  # type: ignore  # noqa: E402
require_auth(login_here=True)

# Small, non-intrusive banner with current URL/port and a copy button
st.markdown(
    """
		<div id="__url_banner" style="position: fixed; bottom: 8px; left: 10px; z-index: 1000; background: white; border: 1px solid #ddd; border-radius: 12px; padding: 6px 10px; box-shadow: 0 1px 2px rgba(0,0,0,0.08); font-size: 12px; max-width: 70vw; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
			<span style="margin-right:8px;">🔗 URL:</span>
			<a id="__url_link" href="#" style="text-decoration:none; color:#0366d6;">loading…</a>
			<button id="__copy_btn" style="margin-left:10px; padding:2px 8px; font-size:12px;">Copy</button>
		</div>
		<script>
		(function(){
			try {
				var href = window.location.href;
				var a = document.getElementById('__url_link');
				if (a) { a.textContent = href; a.href = href; }
				var b = document.getElementById('__copy_btn');
				if (b) {
					b.onclick = async function(){
						try { await navigator.clipboard.writeText(href); b.textContent = 'Copied'; setTimeout(function(){ b.textContent = 'Copy'; }, 1000); } catch (e) {}
					};
				}
			} catch(e) { /* ignore */ }
		})();
		</script>
		""",
    unsafe_allow_html=True,
)

render_dashboard()
