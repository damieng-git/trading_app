"""
Alert Notifier — Sends Telegram and/or email notifications for new combo signals.

Usage:
  Stand-alone:
    python -m apps.dashboard.alert_notifier

  Integrated (called from build_dashboard.py after signal logging):
    from apps.dashboard.alert_notifier import send_notifications
    send_notifications(signal_log_path, config_path)

Configuration (apps/dashboard/configs/alerts_config.json):
  {
    "telegram": {
      "enabled": false,
      "bot_token": "YOUR_BOT_TOKEN",
      "chat_id": "YOUR_CHAT_ID"
    },
    "email": {
      "enabled": false,
      "smtp_host": "smtp.gmail.com",
      "smtp_port": 587,
      "username": "you@gmail.com",
      "password": "app-password",
      "from_addr": "you@gmail.com",
      "to_addrs": ["you@gmail.com"]
    },
    "min_combo_level": "C3"
  }
"""

from __future__ import annotations

import json
import os
import smtplib
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

ALERTS_CONFIG_PATH = Path(__file__).parent / "configs" / "alerts_config.json"
DEFAULT_SIGNAL_LOG = (
    Path(__file__).resolve().parents[2]
    / "data" / "dashboard_artifacts" / "alert_files" / "combo_signal_log.csv"
)
NOTIFIED_KEYS_PATH = (
    Path(__file__).resolve().parents[2]
    / "data" / "dashboard_artifacts" / "alert_files" / ".notified_signal_keys"
)

COMBO_PRIORITY = {"C4": 2, "C3": 1}


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def load_config(config_path: Path | None = None) -> Dict[str, Any]:
    path = config_path or ALERTS_CONFIG_PATH
    if not path.exists():
        return {"telegram": {"enabled": False}, "email": {"enabled": False}, "min_combo_level": "C3"}
    config = json.loads(path.read_text(encoding="utf-8"))
    # Environment variable overrides (take precedence over JSON)
    tg = config.get("telegram", {})
    if os.environ.get("TELEGRAM_BOT_TOKEN"):
        tg = dict(tg)
        tg["bot_token"] = os.environ["TELEGRAM_BOT_TOKEN"]
    if os.environ.get("TELEGRAM_CHAT_ID"):
        tg = dict(tg)
        tg["chat_id"] = os.environ["TELEGRAM_CHAT_ID"]
    if tg != config.get("telegram", {}):
        config = dict(config)
        config["telegram"] = tg

    em = config.get("email", {})
    if os.environ.get("SMTP_HOST"):
        em = dict(em)
        em["smtp_host"] = os.environ["SMTP_HOST"]
    if os.environ.get("SMTP_PORT"):
        em = dict(em)
        try:
            em["smtp_port"] = int(os.environ["SMTP_PORT"])
        except ValueError:
            pass
    if os.environ.get("SMTP_USER"):
        em = dict(em)
        em["username"] = os.environ["SMTP_USER"]
    if os.environ.get("SMTP_PASS"):
        em = dict(em)
        em["password"] = os.environ["SMTP_PASS"]
    if em != config.get("email", {}):
        config = dict(config)
        config["email"] = em

    return config


# ---------------------------------------------------------------------------
# Signal detection
# ---------------------------------------------------------------------------

def get_new_signals(
    signal_log_path: Path,
    notified_keys_path: Path,
    min_combo: str = "C3",
    lookback_hours: int = 48,
) -> pd.DataFrame:
    """Return signals not yet notified, filtered by combo level and recency."""
    if not signal_log_path.exists():
        return pd.DataFrame()

    df = pd.read_csv(signal_log_path)
    if df.empty:
        return pd.DataFrame()

    # Filter by recency
    df["run_utc"] = pd.to_datetime(df["run_utc"], errors="coerce")
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    df = df[df["run_utc"] >= cutoff]

    # Filter by minimum combo level
    min_priority = COMBO_PRIORITY.get(min_combo, 1)
    df["_priority"] = df["combo_level"].map(COMBO_PRIORITY).fillna(0)
    df = df[df["_priority"] >= min_priority]

    # Exclude already-notified keys
    notified: set[str] = set()
    if notified_keys_path.exists():
        notified = set(notified_keys_path.read_text().strip().splitlines())

    df = df[~df["signal_key"].isin(notified)]
    df = df.drop(columns=["_priority"], errors="ignore")
    return df.reset_index(drop=True)


def mark_as_notified(signal_keys: List[str], notified_keys_path: Path) -> None:
    notified_keys_path.parent.mkdir(parents=True, exist_ok=True)
    with open(notified_keys_path, "a", encoding="utf-8") as f:
        for k in signal_keys:
            f.write(k + "\n")


# ---------------------------------------------------------------------------
# Message formatting
# ---------------------------------------------------------------------------

def format_signal_text(row: pd.Series) -> str:
    """Format a single signal as a compact text line."""
    sym = row.get("symbol", "?")
    name = row.get("name", "")
    tf = row.get("timeframe", "?")
    combo = row.get("combo_level", "?")
    price = row.get("entry_price")
    ts = row.get("trend_score")
    conv = row.get("conviction_pct")
    kpis = row.get("kpis_triggered", "")

    action = row.get("signal_action", "")
    atr_stop = row.get("atr_stop")
    pos_size = row.get("position_size", "1x")

    price_str = f"${price:,.2f}" if pd.notna(price) else "N/A"
    ts_str = f"{ts:+.0f}" if pd.notna(ts) else "N/A"
    conv_str = f"{conv:.0f}%" if pd.notna(conv) else "N/A"
    stop_str = f"${atr_stop:,.2f}" if pd.notna(atr_stop) else "N/A"

    header = f"{combo} | {sym}"
    if name:
        header += f" ({name})"
    header += f" | {tf}"

    action_line = ""
    if action:
        action_line = f"\n  Action: {action} | Size: {pos_size} | ATR Stop: {stop_str}"

    return (
        f"{header}\n"
        f"  Price: {price_str} | TrendScore: {ts_str} | Conviction: {conv_str}"
        f"{action_line}\n"
        f"  KPIs: {kpis}"
    )


def build_telegram_message(signals: pd.DataFrame) -> str:
    lines = [f"🚨 {len(signals)} New Combo Signal{'s' if len(signals) != 1 else ''}\n"]
    for combo_level in ["C4", "C3"]:
        subset = signals[signals["combo_level"] == combo_level]
        if subset.empty:
            continue
        lines.append(f"━━━ {combo_level} ━━━")
        for _, row in subset.iterrows():
            lines.append(format_signal_text(row))
        lines.append("")
    lines.append(f"⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC")
    return "\n".join(lines)


def build_email_html(signals: pd.DataFrame) -> str:
    rows_html = []
    for _, row in signals.iterrows():
        combo = row.get("combo_level", "")
        bg = "#fff3cd" if combo == "C4" else "#d1ecf1"
        price = row.get("entry_price")
        price_str = f"${price:,.2f}" if pd.notna(price) else "N/A"
        ts = row.get("trend_score")
        ts_str = f"{ts:+.0f}" if pd.notna(ts) else "N/A"
        conv = row.get("conviction_pct")
        conv_str = f"{conv:.0f}%" if pd.notna(conv) else "N/A"

        rows_html.append(
            f'<tr style="background:{bg}">'
            f'<td style="padding:6px;font-weight:bold">{combo}</td>'
            f'<td style="padding:6px">{row.get("symbol","")}</td>'
            f'<td style="padding:6px">{row.get("name","")}</td>'
            f'<td style="padding:6px">{row.get("timeframe","")}</td>'
            f'<td style="padding:6px;text-align:right">{price_str}</td>'
            f'<td style="padding:6px;text-align:right">{ts_str}</td>'
            f'<td style="padding:6px;text-align:right">{conv_str}</td>'
            f'<td style="padding:6px;font-size:0.85em">{row.get("kpis_triggered","")}</td>'
            f"</tr>"
        )

    table = (
        '<table style="border-collapse:collapse;width:100%;font-family:sans-serif;font-size:14px">'
        "<thead>"
        '<tr style="background:#343a40;color:white">'
        '<th style="padding:8px">Combo</th>'
        '<th style="padding:8px">Symbol</th>'
        '<th style="padding:8px">Name</th>'
        '<th style="padding:8px">TF</th>'
        '<th style="padding:8px">Price</th>'
        '<th style="padding:8px">TrendScore</th>'
        '<th style="padding:8px">Conviction</th>'
        '<th style="padding:8px">KPIs</th>'
        "</tr></thead><tbody>"
        + "\n".join(rows_html)
        + "</tbody></table>"
    )

    return (
        '<div style="font-family:sans-serif;max-width:900px;margin:auto">'
        f"<h2>New Combo Signals — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC</h2>"
        f"<p>{len(signals)} new signal{'s' if len(signals) != 1 else ''} detected.</p>"
        f"{table}"
        '<p style="margin-top:16px;color:#888;font-size:12px">'
        "Generated by Trading Dashboard Alert System</p>"
        "</div>"
    )


# ---------------------------------------------------------------------------
# Senders
# ---------------------------------------------------------------------------

def send_telegram(message: str, bot_token: str, chat_id: str) -> bool:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    try:
        req = urllib.request.Request(url, data=payload, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
            return result.get("ok", False)
    except Exception as e:
        print(f"  Telegram send failed: {e}")
        return False


def send_email(
    subject: str,
    html_body: str,
    smtp_host: str,
    smtp_port: int,
    username: str,
    password: str,
    from_addr: str,
    to_addrs: List[str],
) -> bool:
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = from_addr
        msg["To"] = ", ".join(to_addrs)
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(username, password)
            server.sendmail(from_addr, to_addrs, msg.as_string())
        return True
    except Exception as e:
        print(f"  Email send failed: {e}")
        return False


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

def send_notifications(
    signal_log_path: Path | None = None,
    config_path: Path | None = None,
) -> int:
    """Check for new signals and dispatch notifications. Returns count of signals notified."""
    config = load_config(config_path)
    log_path = signal_log_path or DEFAULT_SIGNAL_LOG
    notified_path = NOTIFIED_KEYS_PATH

    tg_cfg = config.get("telegram", {})
    em_cfg = config.get("email", {})
    min_combo = config.get("min_combo_level", "C3")

    tg_enabled = tg_cfg.get("enabled", False)
    em_enabled = em_cfg.get("enabled", False)

    if not tg_enabled and not em_enabled:
        print("  Alerts: No notification channels enabled (configure alerts_config.json)")
        return 0

    signals = get_new_signals(log_path, notified_path, min_combo)
    if signals.empty:
        print("  Alerts: No new signals to notify")
        return 0

    print(f"  Alerts: {len(signals)} new signal(s) to notify")
    sent = False

    if tg_enabled:
        token = tg_cfg.get("bot_token", "")
        chat = tg_cfg.get("chat_id", "")
        if token and chat:
            msg = build_telegram_message(signals)
            if send_telegram(msg, token, chat):
                print(f"  Alerts: Telegram message sent to {chat}")
                sent = True
        else:
            print("  Alerts: Telegram enabled but bot_token/chat_id missing")

    if em_enabled:
        subject = f"Trading Alert: {len(signals)} New Combo Signal{'s' if len(signals) != 1 else ''}"
        html = build_email_html(signals)
        ok = send_email(
            subject=subject,
            html_body=html,
            smtp_host=em_cfg.get("smtp_host", "smtp.gmail.com"),
            smtp_port=em_cfg.get("smtp_port", 587),
            username=em_cfg.get("username", ""),
            password=em_cfg.get("password", ""),
            from_addr=em_cfg.get("from_addr", ""),
            to_addrs=em_cfg.get("to_addrs", []),
        )
        if ok:
            print(f"  Alerts: Email sent to {', '.join(em_cfg.get('to_addrs', []))}")
            sent = True

    if sent:
        mark_as_notified(signals["signal_key"].tolist(), notified_path)

    return len(signals)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    print("Alert Notifier — Checking for new combo signals ...")
    count = send_notifications()
    if count:
        print(f"Done: {count} signal(s) notified.")
    else:
        print("Done: nothing to send.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
