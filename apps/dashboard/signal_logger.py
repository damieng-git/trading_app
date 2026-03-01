"""Signal logging: alerts export, combo signal track record, and notifications."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)


def export_alerts(
    *,
    state_cache: dict,
    all_data: dict,
    alerts_lookback_bars: int,
    kpi_breakout_order: list[str],
    alert_files_dir: Path,
) -> None:
    """Write per-bar breakout alerts to a timestamped CSV."""
    try:
        alerts_rows: list[dict] = []
        for (sym, tf), st in state_cache.items():
            df = (all_data.get(sym) or {}).get(tf)
            if df is None or df.empty:
                continue
            n = int(max(1, min(alerts_lookback_bars, len(df))))
            tail_idx = df.tail(n).index
            for k in kpi_breakout_order:
                s = st.get(k)
                if s is None or not len(s):
                    continue
                ev = s.reindex(tail_idx)
                for ts, val in ev[ev.isin([1, -1])].items():
                    alerts_rows.append(
                        {
                            "timestamp": pd.to_datetime(ts).isoformat(),
                            "symbol": sym,
                            "timeframe": tf,
                            "kpi": k,
                            "direction": "bullish" if int(val) == 1 else "bearish",
                        }
                    )
        if alerts_rows:
            out_csv = alert_files_dir / f"alerts_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            pd.DataFrame(alerts_rows).to_csv(out_csv, index=False)
    except Exception as e:
        logger.warning("Could not export alerts: %s", e)


def append_combo_signal_log(
    *,
    signal_log_path: Path,
    rows_by_tf: dict[str, list[dict]],
    run_started_utc: str,
) -> Path:
    """Append new combo signals to the persistent signal log CSV. Returns the path."""
    try:
        existing_keys: set[str] = set()
        if signal_log_path.exists() and signal_log_path.stat().st_size > 0:
            try:
                existing_df = pd.read_csv(signal_log_path, usecols=["signal_key"])
                existing_keys = set(existing_df["signal_key"].astype(str))
            except Exception as e:
                logger.warning("Could not read existing combo signal log: %s", e)

        new_signals: list[dict] = []
        for tf_name, tf_rows in rows_by_tf.items():
            for rec_row in tf_rows:
                for combo_level in ("combo_3", "combo_4"):
                    is_new = rec_row.get(f"{combo_level}_new", False)
                    if not is_new:
                        continue
                    sig_date = rec_row.get("last") or run_started_utc
                    sig_key = f"{rec_row['symbol']}|{tf_name}|{combo_level}|{sig_date}"
                    if sig_key in existing_keys:
                        continue
                    kpi_list = rec_row.get(f"{combo_level}_kpis", [])
                    total_kpis = len(rec_row.get("kpi_states", {}))
                    bull_count = sum(1 for v in rec_row.get("kpi_states", {}).values() if v == 1)
                    conviction_pct = round(bull_count / max(total_kpis, 1) * 100, 1)
                    new_signals.append({
                        "signal_key": sig_key,
                        "run_utc": run_started_utc,
                        "signal_date": sig_date,
                        "symbol": rec_row["symbol"],
                        "name": rec_row.get("name", ""),
                        "timeframe": tf_name,
                        "combo_level": combo_level.replace("combo_", "C"),
                        "signal_action": rec_row.get("signal_action", ""),
                        "entry_price": rec_row.get("last_close"),
                        "atr_stop": rec_row.get("atr_stop"),
                        "position_size": "1.5x" if rec_row.get("c4_scaled") else "1x",
                        "trend_score": rec_row.get("trend_score"),
                        "conviction_pct": conviction_pct,
                        "breakout_score": rec_row.get("breakout_score"),
                        "trend_delta": rec_row.get("trend_delta"),
                        "kpis_triggered": ", ".join(kpi_list),
                    })
        if new_signals:
            new_df = pd.DataFrame(new_signals)
            write_header = not signal_log_path.exists() or signal_log_path.stat().st_size == 0
            new_df.to_csv(signal_log_path, mode="a", header=write_header, index=False)
            print(f"  Signal log: {len(new_signals)} new combo signal(s) appended to {signal_log_path.name}")
    except Exception as e:
        logger.warning("Could not append combo signal log: %s", e)
    return signal_log_path


def dispatch_notifications(signal_log_path: Path) -> None:
    """Send Telegram/email notifications for new signals."""
    try:
        from .alert_notifier import send_notifications
        send_notifications(signal_log_path=signal_log_path)
    except Exception as e:
        logger.warning("Could not send notifications: %s", e)
