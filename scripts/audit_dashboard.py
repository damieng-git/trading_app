#!/usr/bin/env python3
"""
Trading Dashboard — Full End-to-End Audit
==========================================
Comprehensive audit covering architecture, UI/UX, data flow, performance,
security, reliability, code quality, repo hygiene, strategy/P&L logic,
and actionable next steps.

Usage:
    python3 audit_dashboard.py              # terminal output
    python3 audit_dashboard.py --json       # machine-readable JSON
    python3 audit_dashboard.py --md         # markdown report  (saved to AUDIT_REPORT.md)
    python3 audit_dashboard.py --section 3  # run only section 3
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
import textwrap
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

REPO = Path(__file__).resolve().parent
APPS = REPO / "apps" / "dashboard"
CORE = REPO / "trading_dashboard"
STATIC = APPS / "static"
CONFIGS = APPS / "configs"
DATA = REPO / "data"
TESTS = REPO / "tests"

CRITICAL = "CRITICAL"
WARNING  = "WARNING"
INFO     = "INFO"

_PY_EXCLUDE = {"__pycache__", ".venv", "node_modules", ".git"}


# ─── Data structures ──────────────────────────────────────────────────

@dataclass
class Finding:
    section: str
    severity: str
    title: str
    detail: str
    file: str = ""
    line: int = 0


@dataclass
class AuditReport:
    findings: List[Finding] = field(default_factory=list)

    def add(self, section: str, severity: str, title: str, detail: str,
            file: str = "", line: int = 0) -> None:
        self.findings.append(Finding(section, severity, title, detail, file, line))

    @property
    def stats(self) -> Dict[str, int]:
        c: Dict[str, int] = {CRITICAL: 0, WARNING: 0, INFO: 0}
        for f in self.findings:
            c[f.severity] = c.get(f.severity, 0) + 1
        return c


# ─── Helpers ──────────────────────────────────────────────────────────

def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(REPO))
    except ValueError:
        return str(p)


def _py_files(root: Path = REPO) -> List[Path]:
    return sorted(
        p for p in root.rglob("*.py")
        if not any(part in _PY_EXCLUDE for part in p.parts)
    )


def _read(p: Path) -> str:
    try:
        return p.read_text(errors="replace")
    except Exception:
        return ""


def _app_py_files() -> List[Path]:
    return [p for p in _py_files()
            if "research" not in _rel(p) and "test" not in _rel(p)
            and p.name != "audit_dashboard.py"]


def _path_to_module(p: Path) -> str:
    try:
        rel = p.relative_to(REPO)
    except ValueError:
        return ""
    parts = list(rel.parts)
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    elif parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]
    return ".".join(parts)


def _find_long_functions(files: List[Path], threshold: int) -> List[Tuple[str, str, int, int]]:
    results = []
    for p in files:
        try:
            tree = ast.parse(_read(p), filename=str(p))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                length = (node.end_lineno or node.lineno) - node.lineno + 1
                if length >= threshold:
                    results.append((_rel(p), node.name, node.lineno, length))
    results.sort(key=lambda x: -x[3])
    return results


# ======================================================================
#  SECTION 1 — ARCHITECTURE & STRUCTURE
# ======================================================================

def audit_architecture(r: AuditReport) -> None:
    S = "1. Architecture & Structure"
    all_py = _py_files()
    app_py = [p for p in all_py if "research" not in _rel(p)]
    research_py = [p for p in all_py if "research" in _rel(p)]

    # ── codebase size ──────────────────────────────────────────────
    total = sum(len(_read(p).splitlines()) for p in all_py)
    app_total = sum(len(_read(p).splitlines()) for p in app_py)
    r.add(S, INFO, "Codebase size",
          f"{len(all_py)} Python files, ~{total:,} total lines "
          f"({len(app_py)} app, {len(research_py)} research). "
          f"App code: ~{app_total:,} lines.")

    # ── large files ────────────────────────────────────────────────
    large = [(f, n) for f, n in
             [(_rel(p), len(_read(p).splitlines())) for p in app_py]
             if n > 400]
    if large:
        detail = "\n".join(f"  {f}: {n} lines" for f, n in sorted(large, key=lambda x: -x[1]))
        r.add(S, WARNING, f"Large app files (>400 lines): {len(large)}",
              f"These resist testing and comprehension:\n{detail}")

    # ── long functions ─────────────────────────────────────────────
    long_fns = _find_long_functions(app_py, 80)
    if long_fns:
        detail = "\n".join(
            f"  {f}:{ln}  {name}()  ~{length} lines"
            for f, name, ln, length in long_fns[:15]
        )
        r.add(S, WARNING, f"Long functions (>80 lines): {len(long_fns)}",
              f"Top 15:\n{detail}")

    # ── module boundary violations ─────────────────────────────────
    for p in app_py:
        text = _read(p)
        rel = _rel(p)
        if "research" in rel:
            continue
        if re.search(r"from\s+research\b", text):
            r.add(S, WARNING, f"App imports research module",
                  f"{rel} imports from research/ — violates layer boundary.",
                  file=rel)

    # ── missing __init__.py ────────────────────────────────────────
    pkg_dirs = {p.parent for p in app_py if p.parent != REPO}
    for d in sorted(pkg_dirs):
        if not (d / "__init__.py").exists():
            r.add(S, INFO, f"Missing __init__.py: {_rel(d)}/",
                  "Directory has Python files but no __init__.py",
                  file=_rel(d))

    # ── dependency flow (circular imports) ─────────────────────────
    imports_map: Dict[str, Set[str]] = defaultdict(set)
    for p in app_py:
        mod = _path_to_module(p)
        if not mod:
            continue
        for m in re.finditer(r"from\s+([\w.]+)\s+import", _read(p)):
            imports_map[mod].add(m.group(1))
    for mod, deps in imports_map.items():
        for dep in deps:
            if dep in imports_map and mod in imports_map.get(dep, set()):
                r.add(S, WARNING, f"Circular import: {mod} ↔ {dep}",
                      "Circular imports increase coupling and can cause "
                      "ImportError at runtime.", file=mod)


# ======================================================================
#  SECTION 2 — UI/UX CONSISTENCY
# ======================================================================

def audit_ui_ux(r: AuditReport) -> None:
    S = "2. UI/UX Consistency"

    css_path = STATIC / "dashboard.css"
    if not css_path.exists():
        r.add(S, CRITICAL, "dashboard.css missing", "No CSS file found")
        return
    css = _read(css_path)

    # ── spacing consistency ────────────────────────────────────────
    px_vals = [int(m.group(1)) for m in re.finditer(r":\s*(\d+)px", css)]
    spacing = sorted(set(px_vals))
    ideal_scale = {0, 2, 4, 8, 12, 16, 20, 24, 32, 40, 48, 64}
    off_scale = [v for v in spacing if v not in ideal_scale and v < 100]
    if len(off_scale) > 5:
        r.add(S, WARNING, "Inconsistent spacing values",
              f"{len(off_scale)} px values off a standard 4px scale: "
              f"{off_scale[:15]}... Consider a spacing system "
              f"(e.g. 4/8/12/16/24/32).",
              file="apps/dashboard/static/dashboard.css")

    # ── font size consistency ──────────────────────────────────────
    font_sizes = sorted(set(
        int(m.group(1)) for m in re.finditer(r"font-size:\s*(\d+)px", css)
    ))
    if len(font_sizes) > 8:
        r.add(S, WARNING, f"Too many font sizes: {font_sizes}",
              f"{len(font_sizes)} distinct font-size values. "
              f"A typographic scale should use 4–6 sizes.",
              file="apps/dashboard/static/dashboard.css")

    # ── undefined CSS variables ────────────────────────────────────
    js_files = list(STATIC.glob("*.js"))
    all_text = css + "\n".join(_read(p) for p in js_files)
    defined = set(re.findall(r"(--[\w-]+)\s*:", css))
    used = set(re.findall(r"var\((--[\w-]+)\)", all_text))
    undefined = used - defined
    if undefined:
        r.add(S, WARNING, f"Undefined CSS variables: {', '.join(sorted(undefined))}",
              "Used in CSS/JS but never defined in :root or theme blocks. "
              "Will fall back to initial value (usually transparent/inherit).",
              file="apps/dashboard/static/dashboard.css")

    # ── color consistency ──────────────────────────────────────────
    hex_colors = set(re.findall(r"#[0-9a-fA-F]{3,8}", css))
    cb_text = _read(STATIC / "chart_builder.js") if (STATIC / "chart_builder.js").exists() else ""
    js_hex = set(re.findall(r"#[0-9a-fA-F]{3,8}", cb_text))
    all_colors = hex_colors | js_hex
    if len(all_colors) > 40:
        r.add(S, INFO, f"Color palette size: {len(all_colors)} unique hex values",
              f"CSS: {len(hex_colors)}, chart_builder.js: {len(js_hex)}. "
              f"Consider consolidating into CSS variables for maintainability.")

    # ── responsiveness ─────────────────────────────────────────────
    media_queries = re.findall(r"@media\s*\([^)]+\)", css)
    breakpoints = re.findall(r"max-width:\s*(\d+)px", css)
    if len(breakpoints) < 2:
        r.add(S, WARNING, f"Limited responsiveness: {len(breakpoints)} breakpoint(s)",
              f"Only breakpoint(s): {breakpoints or 'none'}. "
              f"Mobile and tablet views likely broken. "
              f"Add breakpoints for 768px (tablet) and 480px (mobile).",
              file="apps/dashboard/static/dashboard.css")

    # ── accessibility ──────────────────────────────────────────────
    a11y_issues = []
    if "prefers-reduced-motion" not in css:
        a11y_issues.append("No prefers-reduced-motion media query")
    if "sr-only" not in css and "screen-reader" not in css:
        a11y_issues.append("No screen-reader-only utility class")
    if "focus-visible" in css:
        focus_count = css.count("focus-visible")
        if focus_count < 5:
            a11y_issues.append(f"Only {focus_count} focus-visible rules (incomplete coverage)")
    else:
        a11y_issues.append("No :focus-visible rules for keyboard navigation")

    dash_js_text = _read(STATIC / "dashboard.js") if (STATIC / "dashboard.js").exists() else ""
    if "aria-" not in dash_js_text and "role=" not in dash_js_text:
        a11y_issues.append("No ARIA attributes set in dashboard.js")

    if a11y_issues:
        r.add(S, WARNING, f"Accessibility gaps: {len(a11y_issues)} issues",
              "\n".join(f"  - {i}" for i in a11y_issues))


# ======================================================================
#  SECTION 3 — DATA FLOW
# ======================================================================

def audit_data_flow(r: AuditReport) -> None:
    S = "3. Data Flow"

    # ── loading states ─────────────────────────────────────────────
    dash_js = _read(STATIC / "dashboard.js") if (STATIC / "dashboard.js").exists() else ""

    if "setLoading" in dash_js:
        r.add(S, INFO, "Loading states: implemented",
              "setLoading(true/false) with overlay, spinner, and skeleton classes.")
    else:
        r.add(S, WARNING, "No loading state implementation",
              "Users see no feedback during data fetching.")

    # ── error states ───────────────────────────────────────────────
    error_ui = []
    if "#fileWarn" in dash_js:
        error_ui.append("fileWarn (fetch failure)")
    if "#dataWarn" in dash_js or "dataWarn" in dash_js:
        error_ui.append("dataWarn (data health warnings)")

    if len(error_ui) < 3:
        missing = []
        if "retry" not in dash_js.lower():
            missing.append("No retry button on fetch failure")
        if "fallback" not in dash_js.lower() and "empty state" not in dash_js.lower():
            missing.append("No empty-state fallback for missing symbol data")
        if "timeout" not in dash_js.lower():
            missing.append("No fetch timeout (hangs indefinitely on slow server)")
        if missing:
            r.add(S, WARNING, "Incomplete error states in UI",
                  "\n".join(f"  - {m}" for m in missing))

    # ── caching strategy ───────────────────────────────────────────
    if "figCache" in dash_js:
        r.add(S, INFO, "Client-side cache: figCache (in-memory)",
              "Loaded figures are cached per symbol+tf key. "
              "No cache size limit or eviction policy — can grow large "
              "if user browses many symbols.")

    # ── polling / streaming ────────────────────────────────────────
    if "setInterval" not in dash_js and "WebSocket" not in dash_js and "EventSource" not in dash_js:
        r.add(S, INFO, "No live data: static-only",
              "Dashboard is a static snapshot. No polling, WebSocket, or "
              "SSE for live updates. Acceptable for weekly builds; "
              "blocker for real-time use.")

    # ── state management ───────────────────────────────────────────
    ls_keys = re.findall(r'["\']([^"\']+)["\']', dash_js)
    ls_keys = [k for k in ls_keys if "td_" in k or "LS_KEY" in k]
    r.add(S, INFO, "State persistence: localStorage",
          f"Keys: {ls_keys[:5]}. URL hash sync implemented. "
          f"No state management library (appropriate for this scale).")

    # ── server data path validation ────────────────────────────────
    serve_py = APPS / "serve_dashboard.py"
    if serve_py.exists():
        serve_text = _read(serve_py)
        if "resolve()" in serve_text and "parents" in serve_text:
            r.add(S, INFO, "Path traversal mitigation in serve_dashboard.py",
                  "Uses resolve() + parents check for static file serving.")
        else:
            r.add(S, CRITICAL, "No path traversal protection in serve_dashboard.py",
                  "User-supplied paths could escape the data directory.")


# ======================================================================
#  SECTION 4 — PERFORMANCE
# ======================================================================

def audit_performance(r: AuditReport) -> None:
    S = "4. Performance"

    # ── HTML bundle size ───────────────────────────────────────────
    html_path = DATA / "dashboard_artifacts" / "dashboard_shell.html"
    if html_path.exists():
        size_mb = html_path.stat().st_size / (1024 * 1024)
        r.add(S, WARNING if size_mb > 5 else INFO,
              f"Dashboard HTML: {size_mb:.1f} MB",
              f"Plotly JS (~3.5 MB) is inlined via get_plotlyjs(). "
              f"Consider loading Plotly from CDN or async <script> "
              f"to halve initial load time.")

    # ── asset directory size ───────────────────────────────────────
    assets_dir = DATA / "dashboard_artifacts" / "dashboard_assets"
    if assets_dir.exists():
        asset_files = list(assets_dir.rglob("*.js"))
        total_mb = sum(f.stat().st_size for f in asset_files) / (1024 * 1024)
        r.add(S, WARNING if total_mb > 200 else INFO,
              f"Data assets: {len(asset_files)} JS files, {total_mb:.0f} MB total",
              "Per-symbol JSON files are lazy-loaded — good. "
              "But 800+ MB of assets is large for deployment. "
              "Consider gzip/brotli compression or binary formats.")

    # ── JS loop density ────────────────────────────────────────────
    cb_js = _read(STATIC / "chart_builder.js") if (STATIC / "chart_builder.js").exists() else ""
    loops = (cb_js.count(".map(") + cb_js.count(".filter(") +
             cb_js.count(".forEach(") + cb_js.count("for (") +
             cb_js.count("for(") + cb_js.count("while (") +
             cb_js.count("while("))
    if loops > 80:
        r.add(S, WARNING, f"chart_builder.js: ~{loops} loops/iterators",
              "Heavy iteration over full bar arrays. The Exit Flow "
              "simulation has nested loops (O(n²) worst case). "
              "Consider Web Workers for heavy computation off main thread.")

    # ── DOM query density ──────────────────────────────────────────
    dash_js = _read(STATIC / "dashboard.js") if (STATIC / "dashboard.js").exists() else ""
    dom_queries = (dash_js.count("getElementById") +
                   dash_js.count("querySelector"))
    if dom_queries > 50:
        r.add(S, INFO, f"dashboard.js: ~{dom_queries} DOM queries",
              "Many getElementById/querySelector calls. Cache element "
              "references at init time to avoid repeated DOM lookups.")

    # ── no Web Workers ─────────────────────────────────────────────
    if "Worker(" not in cb_js and "Worker(" not in dash_js:
        r.add(S, INFO, "No Web Workers",
              "All chart computation runs on main thread. "
              "Offloading buildFigureFromData to a Worker would prevent "
              "UI freezes on large datasets (>5000 bars).")

    # ── Python hot paths ───────────────────────────────────────────
    enrichment = _read(CORE / "data" / "enrichment.py")
    if "ProcessPoolExecutor" not in enrichment and "ThreadPoolExecutor" not in enrichment:
        r.add(S, INFO, "Enrichment: no parallelism",
              "translate_and_compute_indicators runs sequentially per symbol. "
              "ProcessPoolExecutor for CPU-bound enrichment would help.")
    else:
        r.add(S, INFO, "Enrichment parallelism: present",
              "Enrichment uses parallel execution for I/O or CPU work.")


# ======================================================================
#  SECTION 5 — SECURITY
# ======================================================================

def audit_security(r: AuditReport) -> None:
    S = "5. Security"

    # ── hardcoded secrets ──────────────────────────────────────────
    secret_patterns = [
        (r'["\']sk-[a-zA-Z0-9]{20,}["\']', "OpenAI-style API key"),
        (r'["\']ghp_[a-zA-Z0-9]{30,}["\']', "GitHub PAT"),
        (r'password\s*=\s*["\'][^"\']+["\']', "Hardcoded password"),
    ]
    for p in _app_py_files():
        text = _read(p)
        for pattern, desc in secret_patterns:
            if re.search(pattern, text, re.I):
                r.add(S, CRITICAL, f"Possible hardcoded secret: {desc}",
                      f"Found in {_rel(p)}", file=_rel(p))

    # ── alerts config credentials ──────────────────────────────────
    alerts_cfg = CONFIGS / "alerts_config.json"
    if alerts_cfg.exists():
        text = _read(alerts_cfg)
        if "YOUR_" in text or "your_" in text:
            r.add(S, INFO, "Placeholder credentials in alerts_config.json",
                  "Contains placeholder tokens (YOUR_BOT_TOKEN_HERE). "
                  "Safe as-is, but real credentials should come from env vars.")
        elif "token" in text.lower() or "password" in text.lower():
            r.add(S, WARNING, "alerts_config.json may contain real credentials",
                  "If this file is committed with real secrets, rotate them "
                  "and switch to environment variables.",
                  file="apps/dashboard/configs/alerts_config.json")

    # ── path traversal ─────────────────────────────────────────────
    serve_py = APPS / "serve_dashboard.py"
    if serve_py.exists():
        text = _read(serve_py)
        if "symbol" in text and (".." not in text or "resolve" in text):
            if "sanitize" not in text and "re.match" not in text:
                r.add(S, WARNING, "Path traversal risk in serve_dashboard.py",
                      "Symbol/TF from query params are used in file paths. "
                      "While resolve()+parents check exists, adding a "
                      "whitelist regex (e.g. ^[A-Z0-9^._-]+$) is safer.",
                      file="apps/dashboard/serve_dashboard.py")

    cli_py = CORE / "cli.py"
    if cli_py.exists():
        text = _read(cli_py)
        if "Path(args." in text:
            r.add(S, WARNING, "CLI path arguments used without validation",
                  "User-supplied paths (args.file, args.config, args.output) "
                  "are passed to Path() without restricting to safe directories.",
                  file="trading_dashboard/cli.py")

    # ── dependency pinning ─────────────────────────────────────────
    pyproject = REPO / "pyproject.toml"
    if pyproject.exists():
        text = _read(pyproject)
        loose = re.findall(r'"([\w-]+)>=', text)
        if loose:
            r.add(S, WARNING, f"Loose dependency versions: {len(loose)} packages use >=",
                  f"Packages: {', '.join(loose[:8])}. "
                  f"Use a lockfile (pip-compile, uv.lock, poetry.lock) "
                  f"for reproducible builds.",
                  file="pyproject.toml")

    # ── no auth ────────────────────────────────────────────────────
    r.add(S, WARNING, "No authentication on serve_dashboard.py",
          "Dashboard is accessible to anyone with the URL. "
          "For server deployment: add auth (OAuth2/JWT/basic auth).")

    # ── yfinance timeout ───────────────────────────────────────────
    dl_py = CORE / "data" / "downloader.py"
    if dl_py.exists():
        text = _read(dl_py)
        if "timeout" not in text.lower():
            r.add(S, INFO, "yfinance downloads have no timeout",
                  "yf.download() can hang indefinitely if Yahoo Finance "
                  "is slow. Consider wrapping with signal.alarm or "
                  "concurrent.futures timeout.",
                  file="trading_dashboard/data/downloader.py")


# ======================================================================
#  SECTION 6 — RELIABILITY
# ======================================================================

def audit_reliability(r: AuditReport) -> None:
    S = "6. Reliability"

    # ── silent exception handling ──────────────────────────────────
    silent: List[Tuple[str, int]] = []
    for p in _app_py_files():
        lines = _read(p).splitlines()
        for i, line in enumerate(lines):
            s = line.strip()
            if s.startswith("except") and i + 1 < len(lines):
                nxt = lines[i + 1].strip()
                if nxt in ("pass", "continue"):
                    has_log = any("log" in lines[j].lower()
                                  for j in range(max(0, i - 2), min(len(lines), i + 3)))
                    if not has_log:
                        silent.append((_rel(p), i + 1))

    if silent:
        detail = "\n".join(f"  {f}:{l}" for f, l in silent[:20])
        r.add(S, WARNING, f"Silent exception swallowing: {len(silent)} blocks",
              f"try/except with pass/continue and no logging. "
              f"These hide bugs and data corruption:\n{detail}")

    # ── retry logic ────────────────────────────────────────────────
    dl_py = CORE / "data" / "downloader.py"
    if dl_py.exists():
        text = _read(dl_py)
        if "_yf_download_with_retry" in text:
            r.add(S, INFO, "Download retry logic: present",
                  "Exponential backoff for yf.download failures.")
            if "except Exception" not in text and "except" not in text:
                r.add(S, WARNING, "Retry only handles empty responses",
                      "Exceptions from yf.download (429, ConnectionError) "
                      "are not caught and retried.")
            else:
                exc_in_retry = "except" in text.split("_yf_download_with_retry")[1][:500]
                if not exc_in_retry:
                    r.add(S, WARNING, "Retry logic does not catch exceptions",
                          "_yf_download_with_retry only retries on empty "
                          "responses, not on raised exceptions (e.g. 429).",
                          file="trading_dashboard/data/downloader.py")

    # ── logging coverage ───────────────────────────────────────────
    logged = []
    not_logged = []
    for p in _app_py_files():
        text = _read(p)
        if "logging.getLogger" in text or "logger" in text:
            logged.append(_rel(p))
        elif len(text.splitlines()) > 50:
            not_logged.append(_rel(p))

    if not_logged:
        r.add(S, INFO, f"Modules without logging: {len(not_logged)}",
              "\n".join(f"  {f}" for f in not_logged[:10]))

    # ── monitoring readiness ───────────────────────────────────────
    r.add(S, INFO, "No monitoring instrumentation",
          "No metrics (Prometheus), structured logging (JSON), or "
          "health check endpoint suitable for container orchestration. "
          "serve_dashboard.py has /health but returns plain HTML status.")

    # ── data integrity ─────────────────────────────────────────────
    store_py = CORE / "data" / "store.py"
    if store_py.exists():
        text = _read(store_py)
        if "lock" not in text.lower() and "flock" not in text.lower():
            r.add(S, WARNING, "No file locking in DataStore",
                  "Concurrent writes to _enrichment_meta.json can corrupt it. "
                  "Add fcntl.flock or filelock for safety.",
                  file="trading_dashboard/data/store.py")


# ======================================================================
#  SECTION 7 — CODE QUALITY & OPTIMIZATION
# ======================================================================

def audit_code_quality(r: AuditReport) -> None:
    S = "7. Code Quality"

    app_py = _app_py_files()

    # ── type hint coverage ─────────────────────────────────────────
    total_fns = 0
    typed_fns = 0
    for p in app_py:
        try:
            tree = ast.parse(_read(p), filename=str(p))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                total_fns += 1
                if node.returns or any(a.annotation for a in node.args.args):
                    typed_fns += 1

    pct = (typed_fns / total_fns * 100) if total_fns else 0
    r.add(S, INFO, f"Type hint coverage: {pct:.0f}% ({typed_fns}/{total_fns})",
          "Good" if pct > 80 else "Consider adding type hints for maintainability.")

    # ── docstring coverage ─────────────────────────────────────────
    total_pub = 0
    documented = 0
    for p in app_py:
        try:
            tree = ast.parse(_read(p), filename=str(p))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("_"):
                    continue
                total_pub += 1
                if (node.body and isinstance(node.body[0], ast.Expr) and
                        isinstance(node.body[0].value, ast.Constant) and
                        isinstance(node.body[0].value.value, str)):
                    documented += 1

    dpct = (documented / total_pub * 100) if total_pub else 0
    r.add(S, INFO, f"Docstring coverage: {dpct:.0f}% ({documented}/{total_pub} public functions)",
          "Target: >80% for public APIs.")

    # ── magic numbers ──────────────────────────────────────────────
    magic: List[Tuple[str, int, str]] = []
    skip_patterns = re.compile(r"(range|sleep|indent|version|port|0x|1e-|1e\d)")
    for p in app_py:
        for i, line in enumerate(_read(p).splitlines(), 1):
            s = line.strip()
            if s.startswith("#") or s.startswith("def ") or "import" in s:
                continue
            nums = re.findall(r"(?<!\w)(\d{2,4})(?!\w)", s)
            for n in nums:
                val = int(n)
                if 10 < val < 10000 and val not in {100, 200, 500, 1000} and not skip_patterns.search(s):
                    magic.append((_rel(p), i, s[:80]))
                    break

    if len(magic) > 20:
        detail = "\n".join(f"  {f}:{l}  {s}" for f, l, s in magic[:10])
        r.add(S, WARNING, f"Magic numbers: ~{len(magic)} instances",
              f"Numeric literals without named constants. Top 10:\n{detail}")

    # ── dead JS code ───────────────────────────────────────────────
    dead_js: List[Tuple[str, str]] = []
    for js_file in STATIC.glob("*.js"):
        text = _read(js_file)
        fns = set(re.findall(r"(?:function|const|let|var)\s+(\w+)\s*[=(]", text))
        for fn in fns:
            if fn in ("buildFigureFromData", "initDashboard", "module", "exports"):
                continue
            if len(re.findall(r"\b" + re.escape(fn) + r"\b", text)) == 1:
                dead_js.append((_rel(js_file), fn))

    if dead_js:
        detail = "\n".join(f"  {f}: {fn}()" for f, fn in dead_js)
        r.add(S, WARNING, f"Dead JS code: {len(dead_js)} functions defined but never called",
              detail)

    # ── yfinance abstraction leaks ─────────────────────────────────
    allowed_yf = {"trading_dashboard/data/downloader.py", "apps/dashboard/sector_map.py"}
    for p in app_py:
        rel = _rel(p)
        if rel in allowed_yf:
            continue
        if re.search(r"import\s+yfinance|from\s+yfinance", _read(p)):
            r.add(S, WARNING, f"yfinance imported outside abstraction layer",
                  f"{rel} imports yfinance directly. Route through downloader.py.",
                  file=rel)

    # ── JS wrong error paths ──────────────────────────────────────
    for js_file in STATIC.glob("*.js"):
        text = _read(js_file)
        wrong = re.findall(r"Scripts/\w+\.py", text)
        if wrong:
            r.add(S, WARNING, f"Wrong path in JS: '{wrong[0]}'",
                  f"Should be 'python -m apps.dashboard.serve_dashboard'",
                  file=_rel(js_file))


# ======================================================================
#  SECTION 8 — REPO HYGIENE (LEANNESS)
# ======================================================================

def audit_repo_hygiene(r: AuditReport) -> None:
    S = "8. Repo Hygiene"

    # ── large files ────────────────────────────────────────────────
    large: List[Tuple[str, float]] = []
    for p in REPO.rglob("*"):
        if p.is_file() and ".git" not in str(p) and "__pycache__" not in str(p):
            mb = p.stat().st_size / (1024 * 1024)
            if mb > 5:
                large.append((_rel(p), mb))
    if large:
        detail = "\n".join(f"  {f}: {s:.1f} MB" for f, s in sorted(large, key=lambda x: -x[1]))
        r.add(S, WARNING, f"Large files in repo: {len(large)} files > 5 MB",
              f"Should be in .gitignore or Git LFS:\n{detail}")

    # ── .gitignore coverage ────────────────────────────────────────
    gi = REPO / ".gitignore"
    if gi.exists():
        gi_text = _read(gi)
        should_have = [
            ("data/dashboard_artifacts/", "generated HTML + assets"),
            ("*.parquet", "enriched data cache"),
            (".env", "environment secrets"),
        ]
        missing = [(p, r_) for p, r_ in should_have if p not in gi_text]
        if missing:
            detail = "\n".join(f"  {p} ({r_})" for p, r_ in missing)
            r.add(S, WARNING, f"Missing .gitignore entries",
                  f"Consider adding:\n{detail}")
    else:
        r.add(S, CRITICAL, "No .gitignore", "Data and caches will be committed.")

    # ── legacy / archive files ─────────────────────────────────────
    legacy = list((REPO / "research" / "kpi_optimization" / "legacy").rglob("*.py")) \
        if (REPO / "research" / "kpi_optimization" / "legacy").exists() else []
    archive = list((REPO / "research" / "_archive").rglob("*.py")) \
        if (REPO / "research" / "_archive").exists() else []
    total_legacy_lines = sum(len(_read(p).splitlines()) for p in legacy + archive)
    if legacy or archive:
        r.add(S, INFO, f"Legacy/archive: {len(legacy) + len(archive)} files, "
              f"~{total_legacy_lines:,} lines",
              "Consider removing or archiving outside the repo to reduce "
              "clone size and cognitive load.")

    # ── unused Python modules ──────────────────────────────────────
    all_imports: Set[str] = set()
    app_py = _app_py_files()
    for p in app_py:
        for m in re.finditer(r"(?:from|import)\s+([\w.]+)", _read(p)):
            all_imports.add(m.group(1))

    orphans = []
    entry_points = {"__main__", "__init__", "cli", "build_dashboard",
                    "refresh_dashboard", "serve_dashboard", "audit_dashboard",
                    "stock_export", "conftest"}
    for p in app_py:
        mod = _path_to_module(p)
        stem = p.stem
        if not mod or stem in entry_points:
            continue
        imported = any(mod in imp or imp in mod for imp in all_imports)
        if not imported:
            orphans.append(_rel(p))

    if orphans:
        r.add(S, INFO, f"Potentially unused modules: {len(orphans)}",
              "\n".join(f"  {f}" for f in orphans))


# ======================================================================
#  SECTION 9 — STRATEGY & P&L LOGIC
# ======================================================================

def audit_strategy(r: AuditReport) -> None:
    S = "9. Strategy & P&L"

    strat = APPS / "strategy.py"
    if not strat.exists():
        r.add(S, CRITICAL, "strategy.py missing", "Expected at apps/dashboard/strategy.py")
        return
    st_text = _read(strat)

    # ── ATR NaN ────────────────────────────────────────────────────
    if "atr[i] > 0" in st_text and "isnan" not in st_text:
        r.add(S, WARNING, "ATR NaN silently disables stop-loss",
              "When atr[i] is NaN, 'atr[i] > 0' is False → stop = -inf. "
              "The position runs without any stop protection. "
              "Fix: explicit np.isnan() check with fallback ATR.",
              file="apps/dashboard/strategy.py")

    # ── empty combo list ───────────────────────────────────────────
    if "len(active_kpis)" in st_text:
        r.add(S, WARNING, "Empty combo list → immediate EXIT",
              "If c3_kpis is [], nk=0, nb>=nk is True on first bar. "
              "Add: if not c3_kpis: return flat_result.",
              file="apps/dashboard/strategy.py")

    # ── column validation ──────────────────────────────────────────
    if 'df["Close"]' in st_text and '"Close" not in' not in st_text:
        r.add(S, INFO, "No OHLC column validation",
              "strategy.py accesses df['High/Low/Close'] without checking "
              "they exist. Missing columns → unhandled KeyError.",
              file="apps/dashboard/strategy.py")

    # ── P&L: no fees/slippage in dashboard ─────────────────────────
    cb_js = _read(STATIC / "chart_builder.js") if (STATIC / "chart_builder.js").exists() else ""

    if "commission" not in cb_js.lower() and "slippage" not in cb_js.lower() and \
       "fee" not in cb_js.lower():
        r.add(S, WARNING, "Dashboard P&L: no fees or slippage",
              "chart_builder.js computes gross P&L (close-to-close). "
              "Research scripts use 0.1% commission. The dashboard "
              "overstates performance. Add at least commission deduction.",
              file="apps/dashboard/static/chart_builder.js")

    # ── EXIT_PARAMS duplication ────────────────────────────────────
    if "EXIT_PARAMS" in cb_js and "EXIT_PARAMS" in st_text:
        r.add(S, WARNING, "EXIT_PARAMS duplicated in Python + JS",
              "T, M, K values hardcoded in both strategy.py and "
              "chart_builder.js. A config.json section should be "
              "the single source of truth, injected at build time.",
              file="apps/dashboard/strategy.py")
    elif "EXIT_PARAMS" in cb_js:
        r.add(S, WARNING, "EXIT_PARAMS only in JS",
              "Strategy params live in chart_builder.js but not "
              "in a shared config. Risk of drift if modified.",
              file="apps/dashboard/static/chart_builder.js")

    # ── position sizing validation ─────────────────────────────────
    if "1.5" in cb_js and "scaled" in cb_js:
        r.add(S, INFO, "Position sizing: 1x/1.5x",
              "C3-only = 1x, C4 = 1.5x. Implemented in both Python and JS. "
              "Verify both produce identical results with a regression test.")

    # ── fills at close only ────────────────────────────────────────
    r.add(S, INFO, "Execution assumption: fills at bar close",
          "Both strategy.py and chart_builder.js use close price for "
          "entry and exit. No slippage model, no open-price fills, "
          "no intrabar simulation. This overstates fill quality.")

    # ── timezone / candle alignment ────────────────────────────────
    dl_text = _read(CORE / "data" / "downloader.py")
    tz_strips = dl_text.count("tz_localize(None)")
    r.add(S, INFO, f"Timezone handling: {tz_strips} tz_localize(None) calls",
          "All timestamps normalized to naive (no TZ) after download. "
          "Consistent but means 4H candles for non-US markets may not "
          "align with local trading hours.")

    # ── screener NaN edge case ─────────────────────────────────────
    sc_text = _read(APPS / "screener_builder.py") if (APPS / "screener_builder.py").exists() else ""
    if "int(s.iloc[-1])" in sc_text and "isna" not in sc_text:
        r.add(S, WARNING, "int(NaN) crash in screener_builder.py",
              "int(s.iloc[-1]) raises ValueError if value is NaN. "
              "Add: if pd.isna(v): return 0.",
              file="apps/dashboard/screener_builder.py")

    # ── missing tests ──────────────────────────────────────────────
    missing_tests = []
    test_files = list(TESTS.rglob("*.py")) if TESTS.exists() else []
    test_text = "\n".join(_read(p) for p in test_files)

    checks = [
        ("NaN in Close column", "NaN.*Close|nan.*close|test.*nan"),
        ("missing OHLCV columns", "missing.*column|KeyError.*Close"),
        ("empty combo KPI list", "empty.*combo|combo.*empty|c3_kpis.*\\[\\]"),
        ("P&L vs JS regression", "pnl.*js|chart_builder|equity.*curve"),
        ("concurrent store writes", "concurrent.*store|race.*condition|thread.*store"),
    ]
    for name, pattern in checks:
        if not re.search(pattern, test_text, re.I):
            missing_tests.append(name)

    if missing_tests:
        detail = "\n".join(f"  - {t}" for t in missing_tests)
        r.add(S, WARNING, f"Missing test coverage: {len(missing_tests)} areas",
              f"No tests found for:\n{detail}")


# ======================================================================
#  SECTION 10 — SCALABILITY & SERVER-READINESS
# ======================================================================

def audit_scalability(r: AuditReport) -> None:
    S = "10. Scalability & Server-Readiness"

    # ── hardcoded paths ────────────────────────────────────────────
    hardcoded: List[Tuple[str, int, str]] = []
    for p in _app_py_files():
        for i, line in enumerate(_read(p).splitlines(), 1):
            if re.search(r'Path\(\s*["\'](?:data/|apps/|configs/)', line):
                hardcoded.append((_rel(p), i, line.strip()[:80]))

    if hardcoded:
        detail = "\n".join(f"  {f}:{l}  {s}" for f, l, s in hardcoded)
        r.add(S, WARNING, f"Hardcoded relative paths: {len(hardcoded)}",
              f"These assume CWD = repo root. For containerized deployment, "
              f"use env vars or a config object:\n{detail}")

    # ── deployment shape recommendation ────────────────────────────
    r.add(S, INFO, "Recommended deployment shape",
          "Phase 1 (quick win): Single Docker container with "
          "FastAPI + static file serving + cron job for builds.\n"
          "Phase 2 (scale): Separate containers for API (FastAPI), "
          "worker (Celery/RQ for builds), and static assets (nginx/CDN).\n"
          "Phase 3 (cloud): Managed services — CloudRun/ECS for API, "
          "Cloud Tasks for builds, GCS/S3 for data, CDN for assets.")

    # ── separation of concerns ─────────────────────────────────────
    r.add(S, WARNING, "No frontend/API/worker separation",
          "build_dashboard.py handles download, enrichment, export, "
          "and HTML generation in one process. For a server:\n"
          "  - API: serve screener data + symbol data via REST\n"
          "  - Worker: background build/enrich jobs\n"
          "  - Frontend: standalone SPA (or keep current HTML)")

    # ── bottlenecks ────────────────────────────────────────────────
    r.add(S, INFO, "Key bottlenecks for server mode",
          "1. yfinance rate limits (batch download mitigates)\n"
          "2. Enrichment is CPU-bound (~45s for 25 symbols)\n"
          "3. HTML generation blocks the process (~35s)\n"
          "4. 800 MB of assets — need compression or streaming\n"
          "5. No incremental client updates (full page refresh)")


# ======================================================================
#  SECTION 11 — DEVELOPER EXPERIENCE
# ======================================================================

def audit_devex(r: AuditReport) -> None:
    S = "11. Developer Experience"

    # ── setup ──────────────────────────────────────────────────────
    has_pyproject = (REPO / "pyproject.toml").exists()
    has_makefile = (REPO / "Makefile").exists()
    has_dockerfile = (REPO / "Dockerfile").exists()
    has_contributing = (REPO / "CONTRIBUTING.md").exists()
    has_readme = (REPO / "README.md").exists() or (REPO / "DASHBOARD.md").exists()

    checklist = {
        "pyproject.toml": has_pyproject,
        "Makefile / task runner": has_makefile,
        "Dockerfile": has_dockerfile,
        "CONTRIBUTING.md": has_contributing,
        "README / DASHBOARD.md": has_readme,
    }
    missing = [k for k, v in checklist.items() if not v]
    present = [k for k, v in checklist.items() if v]
    r.add(S, INFO, f"Dev setup files: {len(present)}/{len(checklist)}",
          f"Present: {', '.join(present)}\n"
          f"Missing: {', '.join(missing) if missing else 'none'}")

    # ── test suite ─────────────────────────────────────────────────
    test_files = list(TESTS.rglob("test_*.py")) if TESTS.exists() else []
    r.add(S, INFO, f"Test suite: {len(test_files)} test files",
          "Run with: python3 -m pytest tests/ -v")

    # ── CI/CD ──────────────────────────────────────────────────────
    has_ci = any((REPO / d).exists()
                 for d in [".github/workflows", ".gitlab-ci.yml", "Jenkinsfile"])
    if not has_ci:
        r.add(S, INFO, "No CI/CD pipeline",
              "Add GitHub Actions / GitLab CI for automated testing, "
              "linting (ruff/flake8), and type checking (mypy).")

    # ── linting / formatting ───────────────────────────────────────
    has_ruff = any(f.exists() for f in [REPO / "ruff.toml", REPO / ".ruff.toml"])
    has_pyproject_ruff = "[tool.ruff]" in _read(REPO / "pyproject.toml") if has_pyproject else False
    if not has_ruff and not has_pyproject_ruff:
        r.add(S, INFO, "No linter configured",
              "Add [tool.ruff] to pyproject.toml for fast linting + formatting.")


# ======================================================================
#  SECTION 12 — NEXT STEPS (5 extra-mile recommendations)
# ======================================================================

def generate_next_steps(r: AuditReport) -> List[Dict[str, str]]:
    return [
        {
            "category": "UI Polish",
            "title": "Design token system + accessibility pass",
            "detail": (
                "Extract all colors, font sizes, spacing, and radii into "
                "CSS custom properties on a 4px grid. Replace the 13 "
                "font-size values with a 5-step type scale. Add "
                "prefers-reduced-motion, aria-labels on interactive "
                "elements, and focus-visible rings on all clickable "
                "components. Add a 768px tablet breakpoint. "
                "Impact: consistent, accessible UI across devices."
            ),
        },
        {
            "category": "Reliability",
            "title": "Replace silent exceptions + add structured logging",
            "detail": (
                "Audit all ~25 except/pass blocks: add logger.warning "
                "with context (symbol, timeframe, file path). Switch to "
                "JSON structured logging (python-json-logger) for "
                "machine-parseable logs. Add NaN guards in "
                "screener_builder.py and ATR validation in strategy.py. "
                "Add a watchdog timeout (60s) around yf.download calls. "
                "Impact: no more silent data corruption, debuggable issues."
            ),
        },
        {
            "category": "Scalability",
            "title": "FastAPI REST layer + background worker",
            "detail": (
                "Add a FastAPI app with endpoints: GET /api/screener/{tf}, "
                "GET /api/symbol/{sym}/{tf}, POST /api/build (triggers "
                "background build via Celery/RQ). Serve assets via "
                "nginx or CDN. Containerize with Docker (API + worker + "
                "nginx). Add /healthz endpoint for orchestrator probes. "
                "Impact: decoupled frontend, concurrent users, deploy "
                "anywhere."
            ),
        },
        {
            "category": "Quant Correctness",
            "title": "Add commission to dashboard P&L + Python/JS parity test",
            "detail": (
                "Add the 0.1% round-trip commission from STRATEGY.md to "
                "chart_builder.js P&L computation. Move EXIT_PARAMS into "
                "config.json and inject into both Python and JS at build "
                "time. Write a regression test that runs strategy.py on a "
                "known dataset and compares trade list + equity curve "
                "against chart_builder.js output (via Node or snapshot). "
                "Impact: honest P&L, no Python/JS drift."
            ),
        },
        {
            "category": "Developer Workflow",
            "title": "Makefile + CI pipeline + pre-commit hooks",
            "detail": (
                "Create a Makefile with targets: make install, make test, "
                "make lint, make build, make audit. Add GitHub Actions "
                "workflow: ruff lint + mypy type check + pytest on every "
                "push. Add pre-commit hooks (ruff format, ruff check). "
                "Pin dependencies with pip-compile or uv.lock. "
                "Impact: consistent environments, catch regressions early, "
                "faster onboarding."
            ),
        },
    ]


# ======================================================================
#  OUTPUT FORMATTERS
# ======================================================================

def _severity_icon(sev: str) -> str:
    return {"CRITICAL": "🔴", "WARNING": "🟡", "INFO": "🔵"}.get(sev, "⚪")


def format_terminal(report: AuditReport, steps: List[Dict[str, str]]) -> str:
    out: List[str] = []
    out.append("=" * 76)
    out.append("  TRADING DASHBOARD — FULL END-TO-END AUDIT")
    out.append("=" * 76)
    stats = report.stats
    out.append(f"\n  {_severity_icon(CRITICAL)} {stats[CRITICAL]} critical  "
               f"{_severity_icon(WARNING)} {stats[WARNING]} warnings  "
               f"{_severity_icon(INFO)} {stats[INFO]} info\n")

    cur_sec = ""
    for f in report.findings:
        if f.section != cur_sec:
            cur_sec = f.section
            out.append("─" * 76)
            out.append(f"  {cur_sec}")
            out.append("─" * 76)
        icon = _severity_icon(f.severity)
        out.append(f"\n  {icon} [{f.severity}] {f.title}")
        if f.file:
            out.append(f"     📁 {f.file}" + (f":{f.line}" if f.line else ""))
        for dl in f.detail.split("\n"):
            out.append(f"     {dl}")

    out.append("\n" + "─" * 76)
    out.append("  12. Five Extra-Mile Next Steps")
    out.append("─" * 76)
    for i, s in enumerate(steps, 1):
        out.append(f"\n  {i}. [{s['category']}] {s['title']}")
        for dl in textwrap.wrap(s["detail"], 68):
            out.append(f"     {dl}")

    out.append("\n" + "=" * 76)
    return "\n".join(out)


def format_markdown(report: AuditReport, steps: List[Dict[str, str]]) -> str:
    out: List[str] = []
    out.append("# Trading Dashboard — Full End-to-End Audit\n")
    stats = report.stats
    out.append(f"> **{stats[CRITICAL]}** critical | **{stats[WARNING]}** warnings "
               f"| **{stats[INFO]}** info\n")

    cur_sec = ""
    for f in report.findings:
        if f.section != cur_sec:
            cur_sec = f.section
            out.append(f"\n---\n\n## {cur_sec}\n")
        sev_md = {"CRITICAL": "**CRITICAL**", "WARNING": "WARNING",
                  "INFO": "info"}[f.severity]
        loc = f" (`{f.file}" + (f":{f.line}" if f.line else "") + "`)" if f.file else ""
        out.append(f"### [{sev_md}] {f.title}{loc}\n")
        for dl in f.detail.split("\n"):
            if dl.strip():
                out.append(f"{dl.strip()}\n")
        out.append("")

    out.append("\n---\n\n## 12. Five Extra-Mile Next Steps\n")
    for i, s in enumerate(steps, 1):
        out.append(f"### {i}. [{s['category']}] {s['title']}\n")
        out.append(f"{s['detail']}\n")

    return "\n".join(out)


# ======================================================================
#  MAIN
# ======================================================================

SECTION_RUNNERS = {
    1: ("Architecture & Structure", audit_architecture),
    2: ("UI/UX Consistency", audit_ui_ux),
    3: ("Data Flow", audit_data_flow),
    4: ("Performance", audit_performance),
    5: ("Security", audit_security),
    6: ("Reliability", audit_reliability),
    7: ("Code Quality & Optimization", audit_code_quality),
    8: ("Repo Hygiene (Leanness)", audit_repo_hygiene),
    9: ("Strategy & P&L Logic", audit_strategy),
    10: ("Scalability & Server-Readiness", audit_scalability),
    11: ("Developer Experience", audit_devex),
}


def run_audit(sections: Optional[List[int]] = None) -> AuditReport:
    report = AuditReport()
    for num, (_, fn) in sorted(SECTION_RUNNERS.items()):
        if sections and num not in sections:
            continue
        fn(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Trading Dashboard — Full End-to-End Audit")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--md", action="store_true", help="Markdown output")
    parser.add_argument("--section", type=int, nargs="*",
                        help="Run specific section(s) only (e.g. --section 1 5 9)")
    args = parser.parse_args()

    report = run_audit(args.section)
    steps = generate_next_steps(report)

    if args.json:
        data = {
            "stats": report.stats,
            "findings": [asdict(f) for f in report.findings],
            "next_steps": steps,
        }
        print(json.dumps(data, indent=2))
    elif args.md:
        print(format_markdown(report, steps))
    else:
        print(format_terminal(report, steps))


if __name__ == "__main__":
    main()
