"""
trades.py — SQLite-backed trade tracker.

Stores manual trades (entry/exit) with symbol, price, size, notes.
Provides CRUD operations and summary statistics.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "trades.db"
_lock = threading.Lock()


def _get_conn() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    with _lock:
        conn = _get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS trades (
                id           TEXT PRIMARY KEY,
                symbol       TEXT NOT NULL,
                timeframe    TEXT NOT NULL DEFAULT '1D',
                direction    TEXT NOT NULL DEFAULT 'long',
                entry_date   TEXT NOT NULL,
                entry_price  REAL NOT NULL,
                size         REAL NOT NULL DEFAULT 1.0,
                exit_date    TEXT,
                exit_price   REAL,
                status       TEXT NOT NULL DEFAULT 'open',
                stop_price   REAL,
                notes        TEXT DEFAULT '',
                currency     TEXT DEFAULT 'USD',
                created_at   TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);
            CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
        """)
        conn.close()


def add_trade(
    symbol: str,
    entry_price: float,
    entry_date: str,
    *,
    timeframe: str = "1D",
    direction: str = "long",
    size: float = 1.0,
    stop_price: float | None = None,
    notes: str = "",
    currency: str = "USD",
) -> dict:
    trade_id = uuid.uuid4().hex[:12]
    now = datetime.utcnow().isoformat(timespec="seconds")
    with _lock:
        conn = _get_conn()
        conn.execute(
            """INSERT INTO trades
               (id, symbol, timeframe, direction, entry_date, entry_price,
                size, status, stop_price, notes, currency, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?)""",
            (trade_id, symbol.upper(), timeframe, direction,
             entry_date, entry_price, size, stop_price, notes, currency, now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
        conn.close()
    return dict(row)


def close_trade(trade_id: str, exit_price: float, exit_date: str) -> dict | None:
    with _lock:
        conn = _get_conn()
        conn.execute(
            "UPDATE trades SET exit_price=?, exit_date=?, status='closed' WHERE id=? AND status='open'",
            (exit_price, exit_date, trade_id),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
        conn.close()
    return dict(row) if row else None


def update_trade(trade_id: str, updates: dict) -> dict | None:
    allowed = {"stop_price", "size", "notes", "timeframe"}
    fields = {k: v for k, v in updates.items() if k in allowed}
    if not fields:
        return get_trade(trade_id)
    set_clause = ", ".join(f"{k}=?" for k in fields)
    vals = list(fields.values()) + [trade_id]
    with _lock:
        conn = _get_conn()
        conn.execute(f"UPDATE trades SET {set_clause} WHERE id=?", vals)
        conn.commit()
        row = conn.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
        conn.close()
    return dict(row) if row else None


def delete_trade(trade_id: str) -> bool:
    with _lock:
        conn = _get_conn()
        cur = conn.execute("DELETE FROM trades WHERE id=?", (trade_id,))
        conn.commit()
        conn.close()
    return cur.rowcount > 0


def get_trade(trade_id: str) -> dict | None:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_trades(*, status: str | None = None, symbol: str | None = None) -> list[dict]:
    conn = _get_conn()
    q = "SELECT * FROM trades WHERE 1=1"
    params: list[Any] = []
    if status:
        q += " AND status=?"
        params.append(status)
    if symbol:
        q += " AND symbol=?"
        params.append(symbol.upper())
    q += " ORDER BY entry_date DESC"
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def trade_stats() -> dict:
    """Compute summary statistics across all closed trades."""
    closed = list_trades(status="closed")
    if not closed:
        return {"total": 0, "wins": 0, "losses": 0, "win_rate": 0,
                "avg_gain": 0, "avg_loss": 0, "expectancy": 0,
                "total_pnl": 0, "best": 0, "worst": 0}

    pnls = []
    for t in closed:
        if t["direction"] == "long":
            pnl_pct = ((t["exit_price"] - t["entry_price"]) / t["entry_price"]) * 100
        else:
            pnl_pct = ((t["entry_price"] - t["exit_price"]) / t["entry_price"]) * 100
        pnls.append(pnl_pct)

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    avg_gain = sum(wins) / len(wins) if wins else 0
    avg_loss = sum(losses) / len(losses) if losses else 0
    win_rate = len(wins) / len(pnls) * 100 if pnls else 0
    expectancy = (win_rate / 100 * avg_gain) + ((1 - win_rate / 100) * avg_loss) if pnls else 0

    return {
        "total": len(pnls),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 1),
        "avg_gain": round(avg_gain, 2),
        "avg_loss": round(avg_loss, 2),
        "expectancy": round(expectancy, 2),
        "total_pnl": round(sum(pnls), 2),
        "best": round(max(pnls), 2) if pnls else 0,
        "worst": round(min(pnls), 2) if pnls else 0,
    }


# Initialize on import
init_db()
