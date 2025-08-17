#!/usr/bin/env python3
"""
Scan src/ for direct requests.post(...) usage that lacks a timeout kwarg, except
inside the helper _post_with_timeout. Intended as a lightweight guard.

Exit codes:
- 0: no violations found
- 1: violations found
"""

from __future__ import annotations

import ast
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


def _gather_py_files() -> list[pathlib.Path]:
    return [p for p in SRC.rglob("*.py") if p.is_file()]


class PostCallVisitor(ast.NodeVisitor):
    def __init__(self, source_lines: list[str]):
        self.source_lines = source_lines
        self.violations: list[tuple[int, str]] = []
        self.fn_stack: list[str | None] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self.fn_stack.append(node.name)
        self.generic_visit(node)
        self.fn_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self.fn_stack.append(node.name)
        self.generic_visit(node)
        self.fn_stack.pop()

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        try:
            is_requests_post = (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "requests"
                and node.func.attr == "post"
            )
            if not is_requests_post:
                return self.generic_visit(node)

            current_fn = self.fn_stack[-1] if self.fn_stack else None
            # Allow inside the helper that intentionally handles timeout fallbacks
            if current_fn == "_post_with_timeout":
                return self.generic_visit(node)

            has_timeout_kw = any(getattr(kw, "arg", None) == "timeout" for kw in node.keywords)
            if not has_timeout_kw:
                lineno = getattr(node, "lineno", 0) or 0
                src = self.source_lines[lineno - 1].rstrip() if 0 < lineno <= len(self.source_lines) else ""
                self.violations.append((lineno, src))
        finally:
            self.generic_visit(node)


def main() -> int:
    files = _gather_py_files()
    violations_total: list[tuple[pathlib.Path, int, str]] = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        visitor = PostCallVisitor(text.splitlines())
        visitor.visit(tree)
        for lineno, line in visitor.violations:
            violations_total.append((path, lineno, line))

    if violations_total:
        print("Found direct requests.post calls without timeout:")
        for p, ln, src in violations_total:
            rel = p.relative_to(ROOT)
            print(f" - {rel}:{ln}: {src}")
        return 1
    print("No direct requests.post without timeout found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
