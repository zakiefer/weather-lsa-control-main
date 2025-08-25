import sys
from pathlib import Path

import streamlit as st

from ui.testids import testid

st.set_page_config(page_title="Profile", page_icon="👤", layout="wide")

# Ensure repo root importable
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ui._bootstrap import *  # noqa: F401,F403

require_auth()

from src.auth import begin_password_reset  # type: ignore

try:
    from src.auth import update_profile as _update_profile  # type: ignore
except Exception:
    _update_profile = None  # type: ignore
from src.notifier import Notifier  # type: ignore

st.title("Profile")

user = st.session_state.get("user") or {}
uid = int(user.get("id") or 0)

st.subheader("Account")
email = st.text_input(testid("prof_email") + "Email", value=user.get("email") or "")
new_pw = st.text_input(
    testid("prof_new_pw") + "New password", value="", type="password", help="Leave blank to keep current password"
)


def _save_profile(uid: int, email: str | None, new_password: str | None) -> bool:
    if callable(_update_profile):
        return _update_profile(uid, email=email or None, new_password=new_password or None)  # type: ignore[misc]
    # Fallback: update directly via auth internals if available
    try:
        from src.auth import _conn, _hash_password  # type: ignore

        with _conn() as conn:  # type: ignore
            c = conn.cursor()
            if email is not None:
                c.execute("UPDATE users SET email = ? WHERE id = ?", (email, uid))
            if new_password:
                c.execute("UPDATE users SET password_hash = ? WHERE id = ?", (_hash_password(new_password), uid))
            conn.commit()
        return True
    except Exception:
        return False


if st.button(testid("prof_save") + "Save changes"):
    if _save_profile(uid, email or None, new_pw or None):
        # Refresh session with updated email
        st.session_state["user"]["email"] = email
        st.success("Updated.")
    else:
        st.error("Failed to update.")

st.subheader("Password reset via email")
if st.button(testid("prof_send_reset") + "Send password reset link"):
    token = begin_password_reset(user.get("username", ""))
    if token:
        # If email is configured, send link
        n = Notifier()
        sent = False
        if email:
            subj = "Password reset"
            body = f"Use this reset token in the app: {token}"
            sent = n.send_email_to(email, subj, body)
        if sent:
            st.info("Reset email sent. Check your inbox for the token.")
        else:
            st.caption("Copy this token and use it on the Dashboard login page reset tab")
            st.code(token)
    else:
        st.error("Could not start reset.")
