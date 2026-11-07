"""Follows the chain block by block and writes the PostgreSQL read model.

CometBFT gives instant finality, so there are no re-orgs to undo: a stored block never changes.
Block metadata comes from the CometBFT RPC (/blockchain, 20 per call); txs with their events from
the REST tx search (tx.height=h), normalised exactly like the gateway did (aurix_common.clients).
"""
from __future__ import annotations

import logging

from aurix_common.clients import ChainClient

from .db import DB

log = logging.getLogger("aurix.indexer")
BATCH = 20  # CometBFT /blockchain returns at most 20 block metas per call
TX_PAGE = 100


def block_metas(chain: ChainClient, low: int, high: int) -> list[dict]:
    r = chain.rpc.get("/blockchain", params={"minHeight": low, "maxHeight": high}).json()["result"]
    metas = [{"height": int(m["header"]["height"]), "time": m["header"]["time"], "hash": m["block_id"]["hash"],
              "num_txs": int(m["num_txs"]), "proposer": m["header"]["proposer_address"]}
             for m in r["block_metas"]]
    return sorted(metas, key=lambda m: m["height"])


def block_txs(chain: ChainClient, height: int, expected: int, page_size: int = TX_PAGE) -> list[dict]:
    """All txs of a block (paginated tx search); never silently skips txs."""
    txs = chain.search_txs_all(f"tx.height={height}", page_size=page_size)
    if len(txs) != expected:
        raise RuntimeError(f"block {height}: {len(txs)} of {expected} txs returned by the tx search")
    return txs


def sync_once(chain: ChainClient, db: DB, start_height: int = 1, max_blocks: int = 2000) -> int:
    """Indexes up to max_blocks new blocks; returns how many were stored."""
    tip = chain.status()["height"]
    nxt = max(db.indexed_height() + 1, start_height)
    stored = 0
    while nxt <= tip and stored < max_blocks:
        high = min(nxt + BATCH - 1, tip)
        for meta in block_metas(chain, nxt, high):
            txs = block_txs(chain, meta["height"], meta["num_txs"]) if meta["num_txs"] else []
            db.store_block(meta, txs)
            stored += 1
        nxt = high + 1
    return stored
