#!/usr/bin/env python3
import os
import socket
import subprocess
import sys
from pathlib import Path


def _venv_streamlit_exe() -> str | None:
    """Return absolute path to .venv/bin/streamlit if it exists.

    Keeps UI runs consistent with the project's virtualenv.
    """
    try:
        root = Path(__file__).resolve().parents[1]
        exe = root / ".venv" / "bin" / "streamlit"
        if exe.exists():
            return str(exe)
    except Exception:
        pass
    return None


def _venv_python_exe() -> str | None:
    """Return absolute path to .venv/bin/python if it exists."""
    try:
        root = Path(__file__).resolve().parents[1]
        exe = root / ".venv" / "bin" / "python"
        if exe.exists():
            return str(exe)
    except Exception:
        pass
    return None


def is_free(port: int) -> bool:
    """Return True if nothing is listening on localhost:port.

    Use connect_ex to avoid false positives from SO_REUSEADDR bind behavior on macOS.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.2)
        try:
            result = s.connect_ex(("127.0.0.1", port))
            return result != 0  # non-zero => cannot connect => likely free
        except Exception:
            return True


def find_port(start: int = 8510, end: int = 8599) -> int:
    # If env overrides, try it first
    env_port = os.getenv("STREAMLIT_PORT")
    if env_port:
        try:
            p = int(env_port)
            if is_free(p):
                return p
        except Exception:
            pass
    for p in range(start, end + 1):
        if is_free(p):
            return p
    raise SystemExit(f"No free port found in range {start}-{end}.")


def main():
    root = Path(__file__).resolve().parents[1]
    ui = root / "ui" / "app.py"
    if not ui.exists():
        raise SystemExit(f"Missing UI entrypoint: {ui}")
    port = find_port()
    print(f"Launching Streamlit UI on http://localhost:{port}")
    base_args = [
        "run",
        str(ui),
        "--server.headless=true",
        f"--server.port={port}",
    ]
    # Prefer project venv's streamlit, then system streamlit, then python -m streamlit (venv or system)
    cmds = []
    venv_streamlit = _venv_streamlit_exe()
    venv_python = _venv_python_exe()
    if venv_streamlit:
        cmds.append([venv_streamlit, *base_args])
    cmds.append(["streamlit", *base_args])
    if venv_python:
        cmds.append([venv_python, "-m", "streamlit", *base_args])
    cmds.append([sys.executable, "-m", "streamlit", *base_args])
    try:
        attempted_install = False
        for i, cmd in enumerate(cmds):
            try:
                subprocess.run(cmd, check=True)
                break
            except FileNotFoundError:
                # Missing CLI; if we have a .venv python but not streamlit installed, try installing once
                if not attempted_install and venv_python:
                    try:
                        pip_exe = str(Path(venv_python).parent / "pip")
                        print("Installing UI dependencies in .venv (streamlit, streamlit-folium, folium)...")
                        subprocess.run([pip_exe, "install", "streamlit", "streamlit-folium", "folium"], check=True)
                        attempted_install = True
                        # After install, prefer running within venv python -m streamlit
                        subprocess.run([venv_python, "-m", "streamlit", *base_args], check=True)
                        break
                    except Exception:
                        pass
                if i == len(cmds) - 1:
                    raise
                continue
            except subprocess.CalledProcessError:
                # If we failed in venv due to missing module import, attempt a one-time install
                if not attempted_install and venv_python:
                    try:
                        pip_exe = str(Path(venv_python).parent / "pip")
                        print("Installing UI dependencies in .venv (streamlit, streamlit-folium, folium)...")
                        subprocess.run([pip_exe, "install", "streamlit", "streamlit-folium", "folium"], check=True)
                        attempted_install = True
                        subprocess.run([venv_python, "-m", "streamlit", *base_args], check=True)
                        break
                    except Exception:
                        pass
                raise
    except KeyboardInterrupt:
        # Graceful shutdown on Ctrl-C
        print("\nStopping Streamlit UI...", file=sys.stderr)
        sys.exit(0)
    except subprocess.CalledProcessError as e:
        print(f"Streamlit exited with code {e.returncode}", file=sys.stderr)
        sys.exit(e.returncode)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
        sys.exit(0)
