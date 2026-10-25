"""Durable decision-to-fill attribution independent of rolling UI snapshots."""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path


class DecisionHistory:
    def __init__(self, path: Path):
        self.path = Path(path)
        with self.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS decisions (id TEXT PRIMARY KEY, at REAL NOT NULL, symbol TEXT NOT NULL, body TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS executions (id TEXT PRIMARY KEY, at TEXT NOT NULL, decision_id TEXT, mode TEXT NOT NULL, body TEXT NOT NULL)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS decisions_time ON decisions(at DESC)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS market_bars (symbol TEXT, at REAL, close REAL, PRIMARY KEY(symbol,at))"
            )

    def bar(self, symbol, at, close):
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO market_bars VALUES (?,?,?)", (symbol, at, close)
            )

    def bars(self, symbol):
        with self.connect() as db:
            return list(
                reversed(
                    db.execute(
                        "SELECT at,close FROM market_bars WHERE symbol=? ORDER BY at DESC LIMIT 500",
                        (symbol,),
                    ).fetchall()
                )
            )

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def record_decision(self, decision):
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO decisions VALUES (?, ?, ?, ?)",
                (
                    decision["id"],
                    decision["at"],
                    decision["symbol"],
                    json.dumps(decision, allow_nan=False),
                ),
            )

    def execution(self, trade):
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO executions VALUES (?, ?, ?, ?, ?)",
                (
                    trade["id"],
                    trade["at"],
                    trade.get("decision_id"),
                    trade["mode"],
                    json.dumps(trade, allow_nan=False),
                ),
            )

    def decisions(self, limit=50, offset=0):
        with self.connect() as db:
            total = db.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
            rows = db.execute(
                "SELECT body FROM decisions ORDER BY at DESC, id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return {"total": total, "items": [json.loads(row[0]) for row in rows]}

    def detail(self, identity):
        with self.connect() as db:
            row = db.execute(
                "SELECT body FROM decisions WHERE id=?", (identity,)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def trades(self, mode="broker", limit=50, offset=0):
        with self.connect() as db:
            total = db.execute(
                "SELECT COUNT(*) FROM executions WHERE mode=?", (mode,)
            ).fetchone()[0]
            rows = db.execute(
                "SELECT body FROM executions WHERE mode=? ORDER BY at DESC, id DESC LIMIT ? OFFSET ?",
                (mode, limit, offset),
            ).fetchall()
        return {"total": total, "items": [json.loads(row[0]) for row in rows]}
