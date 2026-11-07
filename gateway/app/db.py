"""Gateway operational store: Neural Guardian scores recorded for included AUR txs. Never a source of
truth for balances (the chain is); PayPal orders live in the payments service."""
from __future__ import annotations

import json
import sqlite3
import threading

SCHEMA = """
CREATE TABLE IF NOT EXISTS risk_scores (
  id INTEGER PRIMARY KEY AUTOINCREMENT, context TEXT, ref TEXT, score REAL, level TEXT,
  reasons TEXT, model_version TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
"""


class DB:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        self.conn.executescript(SCHEMA)

    def one(self, sql: str, *args) -> dict | None:
        with self.lock:
            row = self.conn.execute(sql, args).fetchone()
        return dict(row) if row else None

    def save_risk(self, context: str, ref: str, risk: dict | None):
        if risk:
            with self.lock:
                self.conn.execute(
                    "INSERT INTO risk_scores(context, ref, score, level, reasons, model_version) VALUES (?,?,?,?,?,?)",
                    (context, ref, risk["risk_score"], risk["level"], json.dumps(risk["reasons"]), risk["model_version"]))
