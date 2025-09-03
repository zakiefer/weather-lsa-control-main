#!/usr/bin/env python3
"""
Inventory Streamlit pages under ui/ and emit a normalized JSON array to stdout.

Extracts per-page: route, title, description, widgets (with labels), charts,
data_sources, session_state keys, cache usage, long_jobs, feature_flags, known_issues.

Heuristic and idempotent; safe to run repeatedly.
"""
from __future__ import annotations

import ast
import json
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Any, Optional


WIDGET_FUNCS = {
    "selectbox",
    "multiselect",
    "slider",
    "checkbox",
    "radio",
    "text_input",
    "number_input",
    "date_input",
    "time_input",
    "file_uploader",
    "toggle",
    "button",
}

CHART_FUNCS = {
    "pyplot",
    "plotly_chart",
    "map",
    "altair_chart",
    "vega_lite_chart",
    "pydeck_chart",
    "line_chart",
    "area_chart",
    "bar_chart",
}


def read_text(path: str) -> str:
    with open(path, encoding="utf-8", errors="ignore") as f:
        return f.read()


def find_first_string_arg(node: ast.Call) -> Optional[str]:
    # Prefer label=... named arg
    for kw in getattr(node, "keywords", []) or []:
        if kw.arg == "label" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
            return kw.value.value
    # Fallback to first positional string literal
    for arg in getattr(node, "args", []) or []:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return arg.value
    return None


def find_title(nodes: list[ast.AST]) -> tuple[str | None, str | None]:
    title = None
    desc = None
    # Look for st.set_page_config(page_title=..., page_icon=..., ...)
    for n in nodes:
        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call):
            call = n.value
            func = call.func
            if isinstance(func, ast.Attribute) and func.attr == "set_page_config":
                for kw in call.keywords or []:
                    if kw.arg == "page_title" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                        title = kw.value.value
                    if kw.arg in ("page_description", "description") and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                        desc = kw.value.value
                if title or desc:
                    return title, desc
    # Fallback to first st.title/subheader/header
    for n in nodes:
        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call):
            call = n.value
            func = call.func
            if isinstance(func, ast.Attribute) and func.attr in ("title", "header", "subheader"):
                t = find_first_string_arg(call)
                if t:
                    return t, desc
    return title, desc


def collect_inventory(py_path: str) -> dict[str, Any]:
    src = read_text(py_path)
    try:
        tree = ast.parse(src)
    except SyntaxError:
        tree = ast.parse("")

    route = "/" + os.path.splitext(os.path.basename(py_path))[0]
    title, description = find_title(list(getattr(tree, "body", [])))

    widgets: list[dict[str, Any]] = []
    charts: list[str] = []
    data_sources: list[dict[str, Any]] = []
    session_state: list[str] = []
    caches: list[dict[str, Any]] = []
    long_jobs: list[dict[str, Any]] = []
    feature_flags: list[str] = []
    known_issues: list[str] = []

    # Regex helpers for non-AST hints
    url_regex = re.compile(r"https?://[^'\"\s)]+")
    if "<style>" in src or "<script>" in src:
        known_issues.append("raw_style_rendered")
    if "st.experimental_rerun" in src:
        known_issues.append("map_resets")

    # Feature flags via env vars
    for m in re.finditer(r"os\.getenv\(['\"]([A-Za-z0-9_]+)['\"]", src):
        key = m.group(1)
        if any(p in key for p in ("E2E", "FIXTURE", "AUTH", "SVG")):
            feature_flags.append(key)
    feature_flags = sorted(set(feature_flags))

    # URLs and IO hints
    for m in url_regex.finditer(src):
        u = m.group(0)
        data_sources.append({"type": "http", "url": u, "notes": None})
    if "requests.get" in src:
        data_sources.append({"type": "http", "url": "requests.get", "notes": None})
    for pat, note in (
        ("pd.read_csv", "csv"),
        ("pd.read_json", "json"),
        ("pd.read_parquet", "parquet"),
        ("sqlite3.connect", "sqlite"),
        ("psycopg2.connect", "postgres"),
        ("sqlalchemy.create_engine", "sqlalchemy"),
        ("subprocess.run", "subprocess"),
    ):
        if pat in src:
            data_sources.append({"type": "io", "url": pat, "notes": note})

    # Session state keys
    for m in re.finditer(r"st\.session_state\[['\"]([^'\"]+)['\"]\]", src):
        session_state.append(m.group(1))
    session_state = sorted(set(session_state))

    # AST walk for widgets, charts, caches, sleeps
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            name = node.func.attr
            if name in WIDGET_FUNCS:
                label = find_first_string_arg(node)
                widgets.append({"type": name, "label": label})
            if name in CHART_FUNCS:
                charts.append(name.replace("_chart", "").replace("vega_lite", "vega-lite"))
            if name in {"sleep"} and isinstance(node.func.value, ast.Name) and node.func.value.id == "time":
                long_jobs.append({"kind": "sleep", "notes": ast.get_source_segment(src, node)[:80] if src else None})
        if isinstance(node, ast.FunctionDef) and node.decorator_list:
            for dec in node.decorator_list:
                if isinstance(dec, ast.Attribute) and dec.attr in {"cache_data", "cache_resource"}:
                    ttl_hint = None
                    if isinstance(dec.value, ast.Name) and dec.value.id == "st":
                        # Look for ttl= in possible call form: @st.cache_data(ttl=...)
                        if isinstance(dec, ast.Call):
                            for kw in dec.keywords or []:
                                if kw.arg == "ttl":
                                    ttl_hint = ast.get_source_segment(src, kw.value)
                        caches.append({
                            "func": node.name,
                            "decorator": f"st.{dec.attr}",
                            "ttl_hint": ttl_hint,
                        })

    # Deduplicate
    charts = sorted(set(charts))
    widgets = sorted(widgets, key=lambda w: (w.get("type") or "", w.get("label") or ""))
    data_sources = sorted(data_sources, key=lambda d: (d.get("type") or "", d.get("url") or ""))

    return {
        "route": route,
        "title": title,
        "description": description,
        "widgets": widgets,
        "charts": charts,
        "data_sources": data_sources,
        "session_state": session_state,
        "cache": caches,
        "long_jobs": long_jobs,
        "feature_flags": feature_flags,
        "known_issues": known_issues,
        "file": py_path,
    }


def list_page_files(root: str) -> list[str]:
    files: list[str] = []
    pages_dir = os.path.join(root, "ui", "pages")
    if os.path.isdir(pages_dir):
        for fn in sorted(os.listdir(pages_dir)):
            if fn.endswith(".py"):
                files.append(os.path.join(pages_dir, fn))
    ui_root = os.path.join(root, "ui")
    if os.path.isdir(ui_root):
        for fn in sorted(os.listdir(ui_root)):
            if fn.endswith(".py") and fn not in {"__init__.py"} and fn[0] != "_":
                files.append(os.path.join(ui_root, fn))
    return files


def main(args: list[str]) -> int:
    root = os.getcwd()
    if len(args) > 1:
        root = args[1]
    files = list_page_files(root)
    results = [collect_inventory(p) for p in files]
    # Normalize route casing to preserve original filename intent (e.g., Map.py => /Map)
    for r in results:
        base = os.path.splitext(os.path.basename(r.get("file") or ""))[0]
        r["route"] = "/" + base
    print(json.dumps(results, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
