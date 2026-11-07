"""PostgreSQL read model of the chain. Never a source of truth: rebuildable from the chain at any time."""
from __future__ import annotations

import json
import threading

import psycopg
from psycopg.rows import dict_row

SCHEMA = """
CREATE TABLE IF NOT EXISTS blocks (
  height BIGINT PRIMARY KEY, time TEXT NOT NULL, hash TEXT NOT NULL,
  num_txs INTEGER NOT NULL, proposer TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS txs (
  hash TEXT PRIMARY KEY, height BIGINT NOT NULL REFERENCES blocks(height), type TEXT NOT NULL,
  sender TEXT, recipient TEXT, amount BIGINT, escrow_id BIGINT, code INTEGER NOT NULL,
  log TEXT, timestamp TEXT, row JSONB NOT NULL);
CREATE INDEX IF NOT EXISTS txs_height ON txs (height DESC);
CREATE TABLE IF NOT EXISTS tx_addresses (
  address TEXT NOT NULL, hash TEXT NOT NULL REFERENCES txs(hash), height BIGINT NOT NULL,
  PRIMARY KEY (address, hash));
CREATE INDEX IF NOT EXISTS tx_addresses_addr ON tx_addresses (address, height DESC);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


class DB:
    def __init__(self, url: str):
        if not url:
            raise ValueError("INDEXER_DATABASE_URL (PostgreSQL) is required")
        self.conn = psycopg.connect(url, autocommit=True, row_factory=dict_row)
        self.lock = threading.Lock()
        with self.lock:
            self.conn.execute(SCHEMA)

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        with self.lock:
            r = self.conn.execute("SELECT value FROM meta WHERE key=%s", (key,)).fetchone()
        return r["value"] if r else default

    def indexed_height(self) -> int:
        return int(self.get_meta("indexed_height", "0"))

    def store_block(self, block: dict, txs: list[dict]) -> None:
        """Writes one block, its txs and the address index, and advances indexed_height atomically."""
        with self.lock, self.conn.transaction():
            self.conn.execute(
                "INSERT INTO blocks(height, time, hash, num_txs, proposer) VALUES (%s,%s,%s,%s,%s)"
                " ON CONFLICT (height) DO NOTHING",
                (block["height"], block["time"], block["hash"], block["num_txs"], block["proposer"]))
            for t in txs:
                self.conn.execute(
                    "INSERT INTO txs(hash, height, type, sender, recipient, amount, escrow_id, code, log, timestamp, row)"
                    " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (hash) DO NOTHING",
                    (t["hash"], t["height"], t["type"], t.get("from"), t.get("to"), t.get("amount"),
                     t.get("escrow_id"), t["code"], t.get("log"), t.get("timestamp"), json.dumps(t)))
                for addr in {a for a in (t.get("from"), t.get("to")) if a}:
                    self.conn.execute("INSERT INTO tx_addresses(address, hash, height) VALUES (%s,%s,%s)"
                                      " ON CONFLICT DO NOTHING", (addr, t["hash"], t["height"]))
            self.conn.execute("INSERT INTO meta(key, value) VALUES ('indexed_height', %s)"
                              " ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", (str(block["height"]),))

    def _rows(self, sql: str, args=()) -> list[dict]:
        with self.lock:
            return self.conn.execute(sql, args).fetchall()

    def blocks(self, limit: int) -> list[dict]:
        return self._rows("SELECT height, time, hash, num_txs, proposer FROM blocks ORDER BY height DESC LIMIT %s",
                          (limit,))

    def block(self, height: int) -> dict | None:
        r = self._rows("SELECT height, time, hash, num_txs, proposer FROM blocks WHERE height=%s", (height,))
        return r[0] if r else None

    def txs(self, limit: int) -> list[dict]:
        return [r["row"] for r in self._rows("SELECT row FROM txs ORDER BY height DESC, hash LIMIT %s", (limit,))]

    def txs_at(self, height: int) -> list[dict]:
        return [r["row"] for r in self._rows("SELECT row FROM txs WHERE height=%s ORDER BY hash", (height,))]

    def tx(self, h: str) -> dict | None:
        r = self._rows("SELECT row FROM txs WHERE hash=%s", (h,))
        return r[0]["row"] if r else None

    def history(self, addr: str, limit: int = 200) -> list[dict]:
        return [r["row"] for r in self._rows(
            "SELECT t.row FROM tx_addresses a JOIN txs t ON t.hash=a.hash WHERE a.address=%s"
            " ORDER BY a.height DESC, a.hash LIMIT %s", (addr, limit))]

    def stats(self) -> dict:
        r = self._rows("SELECT (SELECT count(*) FROM blocks) AS blocks, (SELECT count(*) FROM txs) AS txs,"
                       " (SELECT count(*) FROM txs WHERE code<>0) AS failed_txs")[0]
        return {k: int(v) for k, v in r.items()}
