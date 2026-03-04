"""
CLI entry point for the trading dashboard.

Usage::

    python -m trading_dashboard dashboard build
    python -m trading_dashboard dashboard refresh
    python -m trading_dashboard dashboard rebuild-ui

    python -m trading_dashboard symbols sync
    python -m trading_dashboard symbols list --group portfolio
    python -m trading_dashboard symbols add AAPL --group watchlist
    python -m trading_dashboard symbols remove AAPL --group watchlist
    python -m trading_dashboard symbols move AAPL --from portfolio --to watchlist
    python -m trading_dashboard symbols show AAPL
    python -m trading_dashboard symbols import my_stocks.csv --group picks

    python -m trading_dashboard research sample --n 100
"""

from __future__ import annotations

import argparse
from pathlib import Path

_DEFAULT_CONFIGS_DIR = Path("apps/dashboard/configs")
_DEFAULT_CONFIG = _DEFAULT_CONFIGS_DIR / "config.json"
_DEFAULT_LISTS_DIR = _DEFAULT_CONFIGS_DIR / "lists"


def _resolve_paths(args: argparse.Namespace) -> tuple[Path, Path]:
    """Return (config_path, lists_dir) from CLI args."""
    config = Path(args.config) if getattr(args, "config", None) else _DEFAULT_CONFIG
    lists_dir = Path(args.lists_dir) if getattr(args, "lists_dir", None) else _DEFAULT_LISTS_DIR
    return config, lists_dir


# -----------------------------------------------------------------------
# Dashboard commands
# -----------------------------------------------------------------------

def _cmd_dashboard(args: argparse.Namespace) -> int:
    from apps.dashboard.build_dashboard import main as dashboard_main

    argv = ["--mode", args.mode]
    if hasattr(args, "indicator_config") and args.indicator_config:
        argv += ["--indicator_config", args.indicator_config]
    if hasattr(args, "export_phase") and args.export_phase:
        argv += ["--export_phase", args.export_phase]
    if getattr(args, "force_recompute", False):
        argv.append("--force_recompute_indicators")
    if getattr(args, "skip_figures", False):
        argv.append("--skip_figures")
    return dashboard_main(argv)


# -----------------------------------------------------------------------
# Symbol commands
# -----------------------------------------------------------------------

def _cmd_symbols_sync(args: argparse.Namespace) -> int:
    from trading_dashboard.symbols.manager import SymbolManager

    config_path, lists_dir = _resolve_paths(args)
    sm = SymbolManager.from_lists_dir(lists_dir, config_path=config_path)

    print(f"Discovered {len(sm.group_names)} groups from {lists_dir}:")
    for g in sm.group_names:
        print(f"  {g}: {len(sm.group(g))} symbols")
    print(f"Total unique symbols: {len(sm)}")

    sm.save_config(config_path)
    print(f"\nConfig saved to {config_path}")
    return 0


def _cmd_symbols_list(args: argparse.Namespace) -> int:
    from trading_dashboard.symbols.manager import SymbolManager

    config_path, lists_dir = _resolve_paths(args)
    sm = SymbolManager.from_lists_dir(lists_dir, config_path=config_path)

    if args.group:
        symbols = sm.group(args.group)
        if not symbols:
            print(f"Group '{args.group}' not found or empty.")
            return 1
        print(f"Group '{args.group}': {len(symbols)} symbols")
    else:
        symbols = sm.symbols
        print(f"All: {len(symbols)} symbols, {len(sm.group_names)} groups ({', '.join(sm.group_names)})")

    for s in symbols:
        print(f"  {s}")
    return 0


def _cmd_symbols_add(args: argparse.Namespace) -> int:
    from trading_dashboard.symbols.manager import SymbolManager

    config_path, lists_dir = _resolve_paths(args)
    group = args.group or "watchlist"
    sm = SymbolManager.from_lists_dir(lists_dir, config_path=config_path)

    normed = sm.add_symbol(args.ticker, group=group)
    if not normed:
        print(f"Invalid ticker: {args.ticker!r}")
        return 1

    csv_path = lists_dir / f"{group}.csv"
    sm.save_group_csv(group, csv_path)
    sm.save_config(config_path)

    print(f"Added {normed} to group '{group}'")
    print(f"  CSV: {csv_path}")
    print(f"  Config: {config_path}")
    return 0


def _cmd_symbols_remove(args: argparse.Namespace) -> int:
    from trading_dashboard.symbols.manager import SymbolManager

    config_path, lists_dir = _resolve_paths(args)
    sm = SymbolManager.from_lists_dir(lists_dir, config_path=config_path)

    group = args.group or None
    removed = sm.remove_symbol(args.ticker, group=group)
    if not removed:
        where = f"group '{args.group}'" if group else "any group"
        print(f"Ticker {args.ticker!r} not found in {where}.")
        return 1

    if group:
        csv_path = lists_dir / f"{group}.csv"
        sm.save_group_csv(group, csv_path)
    else:
        sm.sync_lists_dir(lists_dir)

    sm.save_config(config_path)
    where = f"group '{group}'" if group else "all groups"
    print(f"Removed {args.ticker.upper()} from {where}")
    return 0


def _cmd_symbols_move(args: argparse.Namespace) -> int:
    from trading_dashboard.symbols.manager import SymbolManager

    config_path, lists_dir = _resolve_paths(args)
    sm = SymbolManager.from_lists_dir(lists_dir, config_path=config_path)

    moved = sm.move_symbol(args.ticker, from_group=args.from_group, to_group=args.to_group)
    if not moved:
        print(f"Could not move {args.ticker!r}: not found in group '{args.from_group}'")
        return 1

    for g in (args.from_group, args.to_group):
        sm.save_group_csv(g, lists_dir / f"{g}.csv")

    sm.save_config(config_path)
    print(f"Moved {args.ticker.upper()} from '{args.from_group}' to '{args.to_group}'")
    return 0


def _cmd_symbols_show(args: argparse.Namespace) -> int:
    from trading_dashboard.symbols.manager import SymbolManager

    config_path, lists_dir = _resolve_paths(args)
    sm = SymbolManager.from_lists_dir(lists_dir, config_path=config_path)

    groups = sm.find_groups(args.ticker)
    normed = args.ticker.strip().upper()
    if not groups:
        print(f"{normed}: not found in any group")
        return 1

    print(f"{normed}: member of {len(groups)} group(s)")
    for g in groups:
        print(f"  - {g}")
    return 0


def _cmd_symbols_import(args: argparse.Namespace) -> int:
    from trading_dashboard.symbols.manager import SymbolManager

    config_path, lists_dir = _resolve_paths(args)
    group = args.group or Path(args.file).stem
    sm = SymbolManager.from_lists_dir(lists_dir, config_path=config_path)

    added = sm.add_csv(Path(args.file), group=group)
    if added == 0:
        print(f"No symbols found in {args.file}")
        return 1

    csv_path = lists_dir / f"{group}.csv"
    sm.save_group_csv(group, csv_path)
    sm.save_config(config_path)

    print(f"Imported {added} symbols into group '{group}'")
    print(f"  CSV: {csv_path}")
    print(f"  Config: {config_path}")
    return 0


# -----------------------------------------------------------------------
# Screener commands
# -----------------------------------------------------------------------

def _cmd_screener(args: argparse.Namespace) -> int:
    if args.action == "seed-universe":
        from apps.screener.seed_universe import seed_universe
        seed_universe()
        return 0

    from pathlib import Path as _Path

    from apps.screener.daily_screener import inject_screener_groups, run_screener

    ind_cfg = _Path(args.indicator_config) if args.indicator_config else None
    uni_csv = _Path(args.universe) if args.universe else None
    result = run_screener(
        universe_csv=uni_csv,
        indicator_config_path=ind_cfg,
        use_cached_ohlcv=args.cached,
        dry_run=args.dry_run,
        max_c3=args.max_c3,
        max_c4=args.max_c4,
    )

    if args.dry_run:
        print(f"Dry run: {result['universe_size']} universe, "
              f"{result['after_filters']} after geo/index filter")
        print(f"Sample: {result['tickers_sample']}")
        return 0

    c3 = result.get("c3_hits", [])
    c4 = result.get("c4_hits", [])
    print(f"\nScreener results: {len(c3)} C3 hits, {len(c4)} C4 hits")
    print(f"  (from {result.get('after_filters', '?')} stocks after quality filters)")

    if c3:
        print("\nC3 entries (last 3 bars):")
        for h in c3[:10]:
            bar = h.get("combo_3_bar", 0)
            label = ["today", "1 bar ago", "2 bars ago"][bar] if bar < 3 else f"{bar}b ago"
            print(f"  {h['symbol']:10s} {h.get('name',''):30s} TS={h['trend_score']:+.1f} ({label})")
    if c4:
        print("\nC4 entries (last 3 bars):")
        for h in c4[:10]:
            bar = h.get("combo_4_bar", 0)
            label = ["today", "1 bar ago", "2 bars ago"][bar] if bar < 3 else f"{bar}b ago"
            print(f"  {h['symbol']:10s} {h.get('name',''):30s} TS={h['trend_score']:+.1f} ({label})")

    # Save screener tickers to CSV
    if c3 or c4:
        from pathlib import Path as _P2
        csv_path = _P2(__file__).resolve().parent.parent / "apps" / "screener" / "configs" / "screener_results.csv"
        seen: set[str] = set()
        rows: list[str] = ["symbol,combo,entry_bar,trend_score"]
        for h in c3:
            s = h["symbol"]
            if s not in seen:
                seen.add(s)
                rows.append(f"{s},C3,{h.get('combo_3_bar',0)},{h['trend_score']}")
        for h in c4:
            s = h["symbol"]
            if s not in seen:
                seen.add(s)
                rows.append(f"{s},C4,{h.get('combo_4_bar',0)},{h['trend_score']}")
        csv_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
        print(f"\nScreener CSV saved: {csv_path} ({len(seen)} tickers)")

    if not args.no_dashboard and (c3 or c4):
        print("\nWriting screener results to entry_stocks.csv...")
        all_syms = inject_screener_groups(result)
        print(f"  {len(all_syms)} symbols written to Entry Stocks group")

        print("\nTriggering dashboard build...")
        from apps.dashboard.build_dashboard import main as dashboard_main
        dashboard_main(["--mode", "all"])

    return 0


# -----------------------------------------------------------------------
# Research commands
# -----------------------------------------------------------------------

def _cmd_research_sample(args: argparse.Namespace) -> int:
    from research.optimization.sampler import draw_sample, save_sample
    from trading_dashboard.symbols.manager import SymbolManager

    config_path = Path(args.config) if args.config else _DEFAULT_CONFIG
    sm = SymbolManager.from_config(config_path)
    sample = draw_sample(sm.symbols, n=args.n, seed=args.seed)
    out = Path(args.output)
    save_sample(sample, out, seed=args.seed, universe_size=len(sm), description=args.desc or "")
    print(f"Sample of {len(sample)} symbols saved to {out}")
    return 0


# -----------------------------------------------------------------------
# Parser
# -----------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trading_dashboard",
        description="Trading Dashboard CLI",
    )
    sub = parser.add_subparsers(dest="command", help="Available commands")

    # --- dashboard ---
    dash = sub.add_parser("dashboard", help="Build/refresh the dashboard")
    dash.add_argument("mode", choices=["build", "refresh", "rebuild-ui", "export", "re-enrich"],
                       help="Build mode: build (full), refresh (from cache), rebuild-ui (UI only), "
                            "export (data only), re-enrich (recompute indicators from raw OHLCV)")
    dash.add_argument("--indicator-config", dest="indicator_config", default="")
    dash.add_argument("--export-phase", dest="export_phase", default="all")
    dash.add_argument("--force-recompute", dest="force_recompute", action="store_true")
    dash.add_argument("--skip-figures", dest="skip_figures", action="store_true",
                       help="Skip chart generation (screener-only rebuild)")

    # --- symbols ---
    sym = sub.add_parser("symbols", help="Manage stock symbols")
    sym_sub = sym.add_subparsers(dest="symbols_cmd")

    _common = {"--config": {"default": "", "help": "Path to config.json"},
               "--lists-dir": {"default": "", "dest": "lists_dir",
                               "help": "Path to CSV lists directory"}}

    def _add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--config", default="", help="Path to config.json")
        p.add_argument("--lists-dir", default="", dest="lists_dir",
                        help="Path to CSV lists directory")

    s_sync = sym_sub.add_parser("sync", help="Sync CSVs -> config.json")
    _add_common(s_sync)

    s_list = sym_sub.add_parser("list", help="List symbols")
    s_list.add_argument("--group", default="")
    _add_common(s_list)

    s_add = sym_sub.add_parser("add", help="Add a ticker to a group")
    s_add.add_argument("ticker", help="Ticker to add (e.g. AAPL or EURONEXT:AIR)")
    s_add.add_argument("--group", default="watchlist", help="Target group (default: watchlist)")
    _add_common(s_add)

    s_rm = sym_sub.add_parser("remove", help="Remove a ticker from a group")
    s_rm.add_argument("ticker", help="Ticker to remove")
    s_rm.add_argument("--group", default="", help="Group to remove from (empty = all)")
    _add_common(s_rm)

    s_mv = sym_sub.add_parser("move", help="Move a ticker between groups")
    s_mv.add_argument("ticker", help="Ticker to move")
    s_mv.add_argument("--from", dest="from_group", required=True, help="Source group")
    s_mv.add_argument("--to", dest="to_group", required=True, help="Destination group")
    _add_common(s_mv)

    s_show = sym_sub.add_parser("show", help="Show which groups contain a ticker")
    s_show.add_argument("ticker", help="Ticker to look up")
    _add_common(s_show)

    s_import = sym_sub.add_parser("import", help="Bulk import from CSV/TXT file")
    s_import.add_argument("file", help="Path to CSV/TXT file")
    s_import.add_argument("--group", default="", help="Group name (default: filename stem)")
    _add_common(s_import)

    # --- screener ---
    scr = sub.add_parser("screener", help="Daily stock screener (C3/C4 scan)")
    scr.add_argument("action", choices=["run", "seed-universe"],
                      help="run: scan universe for C3/C4 signals; seed-universe: regenerate universe.csv")
    scr.add_argument("--cached", action="store_true",
                      help="Use cached OHLCV data instead of downloading fresh")
    scr.add_argument("--dry-run", dest="dry_run", action="store_true",
                      help="Show universe after filters, skip download/enrichment")
    scr.add_argument("--no-dashboard", dest="no_dashboard", action="store_true",
                      help="Skip Stage 2 (dashboard integration)")
    scr.add_argument("--indicator-config", dest="indicator_config", default="")
    scr.add_argument("--universe", default="",
                      help="Path to universe CSV (default: apps/screener/configs/universe.csv)")
    scr.add_argument("--max-c3", dest="max_c3", type=int, default=50)
    scr.add_argument("--max-c4", dest="max_c4", type=int, default=20)

    # --- research ---
    res = sub.add_parser("research", help="Research tools")
    res_sub = res.add_subparsers(dest="research_cmd")

    res_sample = res_sub.add_parser("sample", help="Draw a random stock sample")
    res_sample.add_argument("--n", type=int, default=100)
    res_sample.add_argument("--seed", type=int, default=42)
    res_sample.add_argument("--output", default="research/optimization/configs/sample.json")
    res_sample.add_argument("--desc", default="")
    res_sample.add_argument("--config", default="")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "dashboard":
        mode_map = {"build": "all", "refresh": "refresh_dashboard",
                     "rebuild-ui": "rebuild_ui", "export": "stock_export",
                     "re-enrich": "re_enrich"}
        args.mode = mode_map.get(args.mode, args.mode)
        return _cmd_dashboard(args)

    elif args.command == "symbols":
        dispatch = {
            "sync": _cmd_symbols_sync,
            "list": _cmd_symbols_list,
            "add": _cmd_symbols_add,
            "remove": _cmd_symbols_remove,
            "move": _cmd_symbols_move,
            "show": _cmd_symbols_show,
            "import": _cmd_symbols_import,
        }
        handler = dispatch.get(args.symbols_cmd)
        if handler:
            return handler(args)
        parser.parse_args(["symbols", "--help"])
        return 1

    elif args.command == "screener":
        return _cmd_screener(args)

    elif args.command == "research":
        if args.research_cmd == "sample":
            return _cmd_research_sample(args)
        parser.parse_args(["research", "--help"])
        return 1

    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
