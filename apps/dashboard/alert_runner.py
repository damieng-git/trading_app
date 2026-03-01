"""Alert runner — standalone pipeline: download → enrich → screener → alerts.

This module provides the automation infrastructure without scheduling.
Run manually or integrate into an external scheduler (cron, Airflow, etc.).

Usage:
  # Full pipeline: download, enrich, scan, alert
  python -m apps.dashboard.alert_runner

  # Scan + alert only (skip download/enrich, use cached data)
  python -m apps.dashboard.alert_runner --scan-only

  # Dry-run: show what would be alerted, don't send
  python -m apps.dashboard.alert_runner --dry-run

  # Specific group only
  python -m apps.dashboard.alert_runner --group portfolio

  # External scheduler integration (cron example):
  # 0 9,16 * * 1-5 cd /path/to/trading_dashboard && python -m apps.dashboard.alert_runner
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("alert_runner")


_ROOT = Path(__file__).resolve().parents[2]
_CONFIGS_DIR = Path(__file__).resolve().parent / "configs"
_ARTIFACTS_DIR = _ROOT / "data" / "dashboard_artifacts"
_ALERT_FILES_DIR = _ARTIFACTS_DIR / "alert_files"
_SIGNAL_LOG = _ALERT_FILES_DIR / "combo_signal_log.csv"
_RUN_LOG = _ALERT_FILES_DIR / "pipeline_runs.jsonl"


def _log_run(status: str, details: dict | None = None) -> None:
    """Append a run record to the pipeline run log."""
    _RUN_LOG.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": status,
        **(details or {}),
    }
    with open(_RUN_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def run_pipeline(
    *,
    scan_only: bool = False,
    dry_run: bool = False,
    group: str | None = None,
) -> dict:
    """Execute the full alert pipeline.

    Returns a summary dict with signal counts and status.
    """
    run_start = datetime.now(timezone.utc)
    summary: dict = {"started": run_start.isoformat(), "signals": 0, "status": "ok"}

    try:
        from apps.dashboard.alert_notifier import load_config
        cfg = load_config()
    except Exception as e:
        logger.error("Failed to load config: %s", e)
        summary["status"] = "config_error"
        _log_run("error", summary)
        return summary

    if not scan_only:
        logger.info("Step 1/3: Running full build (download + enrich + screener) ...")
        try:
            from apps.dashboard.build_dashboard import main as build_main
            build_args = ["--mode", "all"]
            rc = build_main(build_args)
            if rc != 0:
                logger.error("Build failed with exit code %d", rc)
                summary["status"] = "build_error"
                _log_run("error", summary)
                return summary
            logger.info("Build complete.")
        except Exception as e:
            logger.error("Build failed: %s", e)
            summary["status"] = "build_error"
            _log_run("error", summary)
            return summary
    else:
        logger.info("Scan-only: running screener rebuild only ...")
        try:
            from apps.dashboard.build_dashboard import main as build_main
            build_args = ["--mode", "rebuild_ui"]
            build_main(build_args)
        except Exception as e:
            logger.warning("Screener rebuild warning: %s", e)

    logger.info("Step 2/3: Checking for new signals ...")
    try:
        from apps.dashboard.alert_notifier import (
            get_new_signals,
            NOTIFIED_KEYS_PATH,
        )

        new_sigs = get_new_signals(_SIGNAL_LOG, NOTIFIED_KEYS_PATH)
        summary["signals"] = len(new_sigs)
        if new_sigs.empty:
            logger.info("No new signals detected.")
        else:
            logger.info("%d new signal(s) detected.", len(new_sigs))
    except Exception as e:
        logger.error("Signal detection failed: %s", e)
        summary["status"] = "signal_error"
        _log_run("error", summary)
        return summary

    logger.info("Step 3/3: Dispatching alerts ...")
    try:
        if not dry_run and summary["signals"] > 0:
            from apps.dashboard.signal_logger import dispatch_notifications
            dispatch_notifications(_SIGNAL_LOG)
        elif dry_run:
            logger.info("Dry-run: skipping notification dispatch.")
            if not new_sigs.empty:
                logger.info("Would notify for:\n%s", new_sigs[["symbol", "timeframe", "combo_level"]].to_string(index=False))
        else:
            logger.info("No signals to dispatch.")
    except Exception as e:
        logger.error("Alert dispatch failed: %s", e)
        summary["status"] = "alert_error"
        _log_run("error", summary)
        return summary

    elapsed = (datetime.now(timezone.utc) - run_start).total_seconds()
    summary["elapsed_seconds"] = round(elapsed, 1)
    _log_run("success", summary)
    logger.info("Pipeline complete in %.1fs — %d signal(s)", elapsed, summary["signals"])
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Trading Dashboard Alert Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--scan-only", action="store_true",
                        help="Skip download/enrich, use cached data")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show alerts without sending notifications")
    parser.add_argument("--group", type=str, default=None,
                        help="Restrict to a specific symbol group")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    result = run_pipeline(
        scan_only=args.scan_only,
        dry_run=args.dry_run,
        group=args.group,
    )

    if result["status"] != "ok":
        logger.error("Pipeline ended with status: %s", result["status"])
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
