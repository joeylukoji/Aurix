"""AURIX indexer: explorer API over a PostgreSQL read model of the chain.

Read-only and rebuildable: balances, escrows and proofs are always read live from the chain
(the source of truth); blocks, txs and per-address history come from PostgreSQL.
"""
from __future__ import annotations

import contextlib
import logging
import os
import re
import threading

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from aurix_common import crypto
from aurix_common.clients import ChainClient, ChainError

from .config import Settings
from .db import DB
from .sync import sync_once

log = logging.getLogger("aurix.indexer")


def create_app(settings: Settings | None = None, chain: ChainClient | None = None, db: DB | None = None) -> FastAPI:
    s = settings or Settings()
    chain = chain or ChainClient(s.chain_rpc_url, s.chain_rest_url)
    db = db or DB(s.database_url)
    stop = threading.Event()

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        if s.poll_interval > 0:
            threading.Thread(target=loop, name="indexer-sync", daemon=True).start()
        yield
        stop.set()
        db.close()

    app = FastAPI(title="AURIX Indexer", version="0.1.0", lifespan=lifespan)
    app.state.db, app.state.chain = db, chain
    app.state.sync = lambda: sync_once(chain, db, s.start_height)
    if s.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=s.cors_origins, allow_methods=["GET"], allow_headers=["*"])

    @app.exception_handler(ChainError)
    def _chain_err(_: Request, e: ChainError):
        code = 404 if e.status == 404 else 400 if e.status < 500 else 502
        return JSONResponse(status_code=code, content={"detail": e.detail})

    @app.exception_handler(httpx.HTTPError)
    def _chain_down(_: Request, e: httpx.HTTPError):
        return JSONResponse(status_code=502, content={"detail": f"chain node unreachable: {type(e).__name__}"})

    state = {"last_error": None}

    def loop():
        while not stop.wait(s.poll_interval):
            try:
                sync_once(chain, db, s.start_height)
                state["last_error"] = None
            except Exception as e:  # keep following the chain whatever happens
                state["last_error"] = f"{type(e).__name__}: {e}"
                log.warning("sync failed: %s", state["last_error"])

    @app.get("/health")
    def health():
        indexed = db.indexed_height()
        try:
            tip = chain.status()["height"]
        except Exception:
            tip = None
        return {"status": "ok", "indexed_height": indexed, "chain_height": tip,
                "lag": None if tip is None else max(tip - indexed, 0), "last_error": state["last_error"]}

    @app.get("/api/explorer/stats")
    def stats():
        return db.stats() | {"indexed_height": db.indexed_height()}

    @app.get("/api/explorer/blocks")
    def blocks(limit: int = 50):
        return db.blocks(max(1, min(limit, 200)))

    @app.get("/api/explorer/txs")
    def txs(limit: int = 100):
        return db.txs(max(1, min(limit, 500)))

    @app.get("/api/explorer/txs/{h}")
    def tx_by_hash(h: str):
        t = db.tx(h.upper())
        if t:
            return t
        try:  # not indexed yet (lag): ask the chain
            return chain.tx(h.upper())
        except ChainError:
            raise HTTPException(404, "not found")

    @app.get("/api/explorer/proofs/{h}")
    def proof(h: str):
        return chain.proof(h)

    @app.get("/api/explorer/addresses/{addr}/history")
    def history(addr: str, limit: int = 200):
        if not crypto.valid_address(addr):
            raise HTTPException(400, f"invalid address {addr!r}")
        return db.history(addr, max(1, min(limit, 500)))

    @app.get("/api/explorer/search")
    def search(q: str = ""):
        q = q.strip()
        if not q:
            raise HTTPException(400, "empty query")
        low = q.lower()
        try:
            if low.startswith("aur1"):
                if not crypto.valid_address(low):
                    raise HTTPException(404, "not found")
                return {"kind": "address", "address": low, "balance": chain.account(low)["balance"],
                        "history": db.history(low)}
            m = re.fullmatch(r"(?:escrow:\s*|#)(\d+)", low)
            if m:
                return {"kind": "escrow", "escrow": chain.escrow(int(m.group(1)))}
            if q.isdigit():
                b = db.block(int(q))
                if not b:
                    raise HTTPException(404, "not found")
                return {"kind": "block", "block": b | {"txs": db.txs_at(int(q))}}
            if re.fullmatch(r"[0-9a-fA-F]{64}", q):
                t = db.tx(q.upper())
                if t:
                    return {"kind": "tx", "tx": t}
                return {"kind": "proof", "proof": chain.proof(low)}
        except ChainError as e:
            if e.status >= 500 and "not found" not in e.detail.lower():
                raise
        raise HTTPException(404, "not found")

    return app


app = create_app() if os.environ.get("AURIX_NO_AUTOAPP") != "1" else None
