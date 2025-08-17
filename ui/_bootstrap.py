import sys
from pathlib import Path

import streamlit as st

"""
Note: We previously hid Streamlit's built-in nav to show a custom one. To
avoid flicker and simplify, we now use the built-in nav. Helpers retained here
only for potential future use.
"""

# Note: do not call _hide_builtin_nav() at import time; pages must
# call st.set_page_config() first. We call _hide_builtin_nav() inside
# require_auth() and pages can also call it after set_page_config if needed.

# Ensure repo root is importable so we can import as `src.*`
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.auth import (
    authenticate,
    begin_password_reset,
    complete_password_reset,
    ensure_auth_schema,
    register_user,
    seed_admin,
)

# type: ignore
from src.config import settings as cfg  # type: ignore
from src.db import ensure_schema, get_queue_stats, set_config_value, summarize_error_codes

try:
    from src.auth import get_user_by_token, issue_session_token, revoke_session_token  # type: ignore
except Exception:  # pragma: no cover - optional helpers
    issue_session_token = None  # type: ignore
    get_user_by_token = None  # type: ignore
    revoke_session_token = None  # type: ignore


def require_auth(login_here: bool = False):
    """Redirect to root if not signed in. Minimal gate used by all pages."""
    ensure_auth_schema()

    # Helpers for robust query param handling across Streamlit versions
    def _qp_get(name: str):
        try:
            return st.query_params.get(name)
        except Exception:
            try:
                q = st.experimental_get_query_params()  # type: ignore[attr-defined]
                v = q.get(name)
                return v[0] if isinstance(v, list) and v else (v if isinstance(v, str) else None)
            except Exception:
                return None

    def _qp_update(updates: dict | None = None, remove: list[str] | None = None):
        try:
            cur: dict = {}
            try:
                cur = dict(st.query_params)  # type: ignore[arg-type]
            except Exception:
                try:
                    cur = {
                        k: (v[0] if isinstance(v, list) and v else v)
                        for k, v in st.experimental_get_query_params().items()
                    }  # type: ignore[attr-defined]
                except Exception:
                    cur = {}
            for k in remove or []:
                cur.pop(k, None)
            if updates:
                cur.update({k: v for k, v in updates.items() if v is not None})
            try:
                st.query_params.update(cur)
            except Exception:
                try:
                    st.experimental_set_query_params(**cur)  # type: ignore[attr-defined]
                except Exception:
                    pass
        except Exception:
            pass

    # On first render of any page, if there's a token in localStorage but not in the URL,
    # append it once so the server can restore the session, then we scrub it later.
    try:
        st.markdown(
            """
            <script>
            (function(){
                try {
                    var url = new URL(window.location.href);
                    // Simple cookie helpers
                    function setCookie(name, value, days) {
                        try {
                            var d = new Date();
                            d.setTime(d.getTime() + (days*24*60*60*1000));
                            var expires = "expires=" + d.toUTCString();
                            document.cookie = name + "=" + (value || "") + ";" + expires + ";path=/";
                        } catch (e) {}
                    }
                    function getCookie(name) {
                        try {
                            var nameEQ = name + "=";
                            var ca = document.cookie.split(';');
                            for (var i=0;i<ca.length;i++) {
                                var c = ca[i];
                                while (c.charAt(0)==' ') c = c.substring(1,c.length);
                                if (c.indexOf(nameEQ) == 0) return c.substring(nameEQ.length,c.length);
                            }
                        } catch (e) {}
                        return null;
                    }
                    function eraseCookie(name) {
                        try { document.cookie = name+'=; Max-Age=-99999999; path=/'; } catch (e) {}
                    }
                        var cleaned = null;
                        try { cleaned = window.sessionStorage.getItem('tok_cleaned'); } catch(e) { cleaned = null; }
                    // If _tok is already present, cache it to localStorage and cookie immediately
                    try {
                        var ptok = url.searchParams.get('_tok');
                        if (ptok) {
                            try { window.localStorage.setItem('auth_tok', ptok); } catch(e) {}
                            setCookie('auth_tok', ptok, 14);
                        }
                    } catch (e) { /* ignore */ }
                    if (!url.searchParams.get('logout')) {
                        var tok = null;
                        try { tok = window.localStorage.getItem('auth_tok'); } catch(e) { tok = null; }
                        if (!tok) {
                            tok = getCookie('auth_tok');
                            if (tok) {
                                try { window.localStorage.setItem('auth_tok', tok); } catch(e) {}
                            }
                        }
                            // Only add _tok if not already cleaned this tab
                            if (tok && !url.searchParams.get('_tok') && !cleaned) {
                            url.searchParams.set('_tok', tok);
                            window.location.replace(url.toString());
                        }
                    }
                } catch (e) { /* ignore */ }
            })();
            </script>
            """,
            unsafe_allow_html=True,
        )
    except Exception:
        pass

    def _main_script_name() -> str:
        """Return the filename of the launched Streamlit script (main)."""
        try:
            import sys as _sys
            from pathlib import Path as _P

            n = _P(_sys.argv[0]).name if _sys.argv else "Dashboard.py"
            return n or "Dashboard.py"
        except Exception:
            return "Dashboard.py"

    # Attempt session restore from token before redirecting to Dashboard
    if not st.session_state.get("user"):
        # Respect explicit logout flag (avoid auto-bootstrapping from localStorage)
        is_logout = bool(_qp_get("logout"))
        # Try token in URL or session
        tok = _qp_get("_tok")
        if not tok:
            tok = st.session_state.get("_auth_token")
        if tok and not is_logout and callable(get_user_by_token):
            user = get_user_by_token(tok)  # type: ignore[misc]
            if user:
                st.session_state["user"] = user
                st.session_state["_auth_token"] = tok
        # If still no user, either attempt client restore (once on main),
        # show login, or redirect to Dashboard.
        if not st.session_state.get("user"):
            if login_here:
                # On the main page, attempt a one-time client-side restore first
                if (not is_logout) and (not tok):
                    st.info("Restoring session…")
                    st.markdown(
                        """
                        <script>
                        (function(){
                            try {
                                var url = new URL(window.location.href);
                                if (!url.searchParams.get('logout')) {
                                    var cleaned = null;
                                    try {
                                        cleaned = window.sessionStorage.getItem('tok_cleaned');
                                    } catch(e) { cleaned = null; }
                                    var tok = window.localStorage.getItem('auth_tok');
                                    if (tok && !url.searchParams.get('_tok') && !cleaned) {
                                        url.searchParams.set('_tok', tok);
                                        window.location.replace(url.toString());
                                        return;
                                    }
                                }
                            } catch (e) { /* ignore */ }
                        })();
                        </script>
                        """,
                        unsafe_allow_html=True,
                    )
                # Build login UI below if restore didn't happen or token invalid
                pass
            else:
                try:
                    st.switch_page(_main_script_name())
                except Exception:
                    st.warning("Please sign in on the Dashboard page.")
                st.stop()

    # Global sidebar, auth, and navigation injected on every page that imports this
    # ---------- Auth (required for all pages) ----------
    seed_admin()

    def _login_ui():
        st.title("Sign in")
        tab_login, tab_register, tab_reset = st.tabs(["Login", "Register", "Reset Password"])
        with tab_login:
            u = st.text_input("Username", value="", key="_login_username")
            p = st.text_input("Password", type="password", key="_login_password")
            if st.button("Sign in"):
                user = authenticate(u, p)
                if user:
                    st.session_state["user"] = user
                    # Issue a session token for persistence across pages/refresh
                    try:
                        from src.auth import issue_session_token  # type: ignore

                        if callable(issue_session_token):
                            uid = int(user.get("id") or 0)
                            tok = issue_session_token(uid)  # type: ignore[misc]
                            st.session_state["_auth_token"] = tok
                    except Exception:
                        pass
                    st.rerun()
                else:
                    st.error("Invalid credentials")
        with tab_register:
            ru = st.text_input("New username", key="_reg_username")
            re = st.text_input("Email", key="_reg_email")
            rp = st.text_input("Password", type="password", key="_reg_password")
            if st.button("Create account"):
                if ru and rp:
                    if register_user(ru, rp, email=re, is_admin=False):
                        st.success("Account created. Sign in.")
                    else:
                        st.error("Username already exists.")
                else:
                    st.warning("Username and password required.")
        with tab_reset:
            fp_u = st.text_input("Username for reset", key="_fp_user")
            if st.button("Start reset") and fp_u:
                token = begin_password_reset(fp_u)
                if token:
                    st.info("Reset started. Use token below to set a new password.")
                    st.code(token)
            rp_t = st.text_input("Reset token", key="_reset_token")
            rp_p = st.text_input("New password", type="password", key="_reset_password")
            if st.button("Complete reset") and rp_t and rp_p:
                if complete_password_reset(rp_t, rp_p):
                    st.success("Password updated. Sign in.")
                else:
                    st.error("Invalid or expired token")

    if not st.session_state.get("user"):
        if login_here:
            _login_ui()
            st.stop()
        else:
            try:
                st.switch_page(_main_script_name())
            except Exception:
                st.warning("Please sign in on the Dashboard page.")
            st.stop()

    # If authenticated, ensure token is persisted client-side and URL is clean
    try:
        user = st.session_state.get("user") or {}
        tok = st.session_state.get("_auth_token")
        if user and tok:
            script = """
            <script>
            (function(){
                try {
                    var tok = '%s';
                    const cur = window.localStorage.getItem('auth_tok');
                    if (cur !== tok) { window.localStorage.setItem('auth_tok', tok); }
                    // Also persist to cookie for robustness
                    try {
                        var d = new Date();
                        d.setTime(d.getTime() + (14*24*60*60*1000));
                        var expires = "expires=" + d.toUTCString();
                        document.cookie = 'auth_tok=' + tok + ';' + expires + ';path=/';
                    } catch (e) {}
                        // Mark this tab as cleaned and remove _tok from the URL for simplicity
                        try { window.sessionStorage.setItem('tok_cleaned', '1'); } catch(e) {}
                        try {
                            var url = new URL(window.location.href);
                            if (url.searchParams.has('_tok')) {
                                url.searchParams.delete('_tok');
                                // also remove any lingering logout flag
                                if (url.searchParams.has('logout')) url.searchParams.delete('logout');
                                window.history.replaceState({}, document.title, url.toString());
                            }
                        } catch (e) {}
                } catch (e) { /* ignore */ }
            })();
            </script>
            """ % (tok,)
            st.markdown(script, unsafe_allow_html=True)
    except Exception:
        pass

    # ---------- Cached data helpers ----------
    @st.cache_data(ttl=15)
    def _cached_queue_stats():
        ensure_schema()
        return get_queue_stats()

    @st.cache_data(ttl=30)
    def _cached_top_errs(limit: int = 10):
        ensure_schema()
        return summarize_error_codes(limit)

    # Optional URL action: logout
    if _qp_get("logout"):
        # Clear localStorage token on client and remove logout flag
        st.markdown(
            """
            <script>
            try { window.localStorage.removeItem('auth_tok'); } catch(e) {}
            try { document.cookie = 'auth_tok=; Max-Age=-99999999; path=/'; } catch(e) {}
                try { window.sessionStorage.removeItem('tok_cleaned'); } catch(e) {}
            </script>
            """,
            unsafe_allow_html=True,
        )
        st.session_state.pop("user", None)
        st.session_state.pop("_auth_token", None)
        _qp_update(remove=["logout", "_tok"])
        st.rerun()

    # Built-in multipage nav stays visible (no custom CSS overrides).

    # ---------- Sidebar: title, quick status, controls ----------
    with st.sidebar:
        st.title("Dashboard")
        # Profile chip (top of sidebar)
        _user = st.session_state.get("user") or {}
        st.caption(f"Signed in as {_user.get('username')} {'(admin)' if _user.get('is_admin') else ''}")
        if st.button("Sign out"):
            # Revoke token, clear session, set logout flag to prevent bootstrap
            try:
                tok = st.session_state.get("_auth_token")
                if tok and callable(revoke_session_token):
                    revoke_session_token(tok)  # type: ignore[misc]
            except Exception:
                pass
            st.session_state.pop("user", None)
            st.session_state.pop("_auth_token", None)
            _qp_update(updates={"logout": "1"}, remove=["_tok"])
            st.rerun()

        with st.expander("Quick Status", expanded=True):
            qs = _cached_queue_stats()
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Queued", qs.get("queued", 0))
            c2.metric("Running", qs.get("running", 0))
            c3.metric("Done", qs.get("done", 0))
            c4.metric("Error", qs.get("error", 0))

        with st.expander("Controls", expanded=False):
            if _user.get("is_admin"):
                kill = st.toggle(
                    "Kill Switch",
                    value=bool(getattr(cfg, "KILL_SWITCH", False)),
                    help="Blocks worker and live mutations when ON.",
                )
                if st.button("Apply Kill Switch"):
                    set_config_value("KILL_SWITCH", "true" if kill else "false")
                    st.success("Saved.")
                validate_gate = st.toggle("Validate Gate (two-phase)", value=bool(getattr(cfg, "VALIDATE_GATE", True)))
                if st.button("Apply Validate Gate"):
                    set_config_value("VALIDATE_GATE", "true" if validate_gate else "false")
                    st.success("Saved.")
            else:
                st.info("Admin only.")

    # (No duplicate nav below)

    # ---------- Top-right profile chip ----------
    try:
        name = _user.get("username", "user")
        role = "admin" if _user.get("is_admin") else "user"
        html = f"""
        <div style='position: fixed; top: 8px; right: 10px; z-index: 1000;
                    background: white; border: 1px solid #ddd; border-radius: 16px;
                    padding: 6px 10px; box-shadow: 0 1px 2px rgba(0,0,0,0.08);
                    font-size: 13px;'>
            <span style='margin-right:8px;'>👤 {name} ({role})</span>
            <a href='?logout=1' style='text-decoration:none;'>Sign out</a>
        </div>
        """
        st.markdown(html, unsafe_allow_html=True)
    except Exception:
        pass


def sidebar_status_and_controls():
    # Retained as a no-op shim; the real sidebar is built inside require_auth()
    return None
