"""Off-chain read index / operational store. Never a source of truth for AUR balances."""
from __future__ import annotations

import json
import sqlite3
import threading

SCHEMA = """
CREATE TABLE IF NOT EXISTS paypal_orders (
  order_id TEXT PRIMARY KEY, amount_usd TEXT NOT NULL, payer TEXT, description TEXT,
  status TEXT NOT NULL, capture_id TEXT, proof_hash TEXT, anchor_tx TEXT, anchor_height INTEGER,
  risk_score REAL, risk_level TEXT, simulated INTEGER NOT NULL DEFAULT 0,
  attestations TEXT, attest_threshold INTEGER, capture_amount TEXT, capture_currency TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS webhook_events (
  event_id TEXT PRIMARY KEY, event_type TEXT, order_id TEXT, signature_valid INTEGER,
  received_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS risk_scores (
  id INTEGER PRIMARY KEY AUTOINCREMENT, context TEXT, ref TEXT, score REAL, level TEXT,
  reasons TEXT, model_version TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
"""

# Columns added after the first release: (name, type). Added in place to older databases.
MIGRATIONS = {"paypal_orders": [("attestations", "TEXT"), ("attest_threshold", "INTEGER"),
                                 ("capture_amount", "TEXT"), ("capture_currency", "TEXT")]}


class DB:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        self.conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self):
        for table, cols in MIGRATIONS.items():
            have = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            for name, typ in cols:
                if name not in have:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {typ}")

    def close(self) -> None:
        with self.lock:
            self.conn.close()

    def exec(self, sql: str, *args):
        with self.lock:
            return self.conn.execute(sql, args)

    def one(self, sql: str, *args) -> dict | None:
        with self.lock:  # fetch under the lock: the connection is shared across threads
            row = self.conn.execute(sql, args).fetchone()
        return dict(row) if row else None

    def all(self, sql: str, *args) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def record_event_once(self, event_id: str, event_type: str, order_id: str | None) -> bool:
        """Idempotency: True only the first time an event id is seen."""
        try:
            self.exec("INSERT INTO webhook_events(event_id, event_type, order_id, signature_valid) VALUES (?,?,?,1)",
                      event_id, event_type, order_id)
            return True
        except sqlite3.IntegrityError:
            return False

    def forget_event(self, event_id: str):
        """Undoes record_event_once when processing failed, so a redelivery is handled again."""
        self.exec("DELETE FROM webhook_events WHERE event_id=?", event_id)

    def save_risk(self, context: str, ref: str, risk: dict | None):
        if risk:
            self.exec("INSERT INTO risk_scores(context, ref, score, level, reasons, model_version) VALUES (?,?,?,?,?,?)",
                      context, ref, risk["risk_score"], risk["level"], json.dumps(risk["reasons"]), risk["model_version"])

    def update_order(self, order_id: str, **fields):
        cols = ", ".join(f"{k}=?" for k in fields)
        self.exec(f"UPDATE paypal_orders SET {cols}, updated_at=CURRENT_TIMESTAMP WHERE order_id=?",
                  *fields.values(), order_id)
