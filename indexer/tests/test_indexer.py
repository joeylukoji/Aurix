import secrets
import time

import psycopg
import pytest
from fastapi.testclient import TestClient

from aurix_common import crypto
from aurix_common.clients import ChainClient
from aurix_indexer.config import Settings
from aurix_indexer.db import DB
from aurix_indexer.main import create_app


@pytest.fixture
def env(chain_urls, pg_url):
    with psycopg.connect(pg_url, autocommit=True) as c:  # fresh schema per test
        c.execute("DROP TABLE IF EXISTS tx_addresses, txs, blocks, meta")
    chain = ChainClient(*chain_urls)
    app = create_app(Settings(chain_rpc_url=chain_urls[0], chain_rest_url=chain_urls[1], database_url=pg_url,
                              poll_interval=0), chain=chain, db=DB(pg_url))
    return TestClient(app), chain


def send(chain: ChainClient, key: crypto.Key, to: str, amount: int) -> dict:
    acct = chain.account(key.address)
    raw = crypto.build_tx(key, [crypto.msg_send(key.address, to, amount)], chain.status()["chain_id"],
                          acct["account_number"], acct["sequence"])
    return chain.broadcast(raw)


def test_sync_indexes_blocks_txs_and_history(env):
    c, chain = env
    faucet, bob = crypto.Key("aurix-dev-faucet"), crypto.Key(secrets.token_hex(8)).address
    res = send(chain, faucet, bob, 7 * crypto.MICRO)
    assert c.app.state.sync() > 0
    tip = chain.status()["height"]
    assert c.get("/health").json()["indexed_height"] >= res["height"]
    blocks = c.get("/api/explorer/blocks?limit=5").json()
    assert blocks[0]["height"] > blocks[-1]["height"] and blocks[0]["height"] <= tip
    tx = c.get(f"/api/explorer/txs/{res['hash'].lower()}").json()  # case-insensitive lookup
    assert tx == {**tx, "hash": res["hash"], "type": "MsgSend", "to": bob, "amount": 7 * crypto.MICRO}
    hist = c.get(f"/api/explorer/addresses/{bob}/history").json()
    assert [t["hash"] for t in hist] == [res["hash"]]
    assert any(t["hash"] == res["hash"] for t in c.get("/api/explorer/txs").json())
    assert c.get("/api/explorer/stats").json()["txs"] >= 1
    # idempotent: a second sync stores only the new blocks and never duplicates rows
    c.app.state.sync()
    assert len(c.get(f"/api/explorer/addresses/{bob}/history").json()) == 1


def test_search(env):
    c, chain = env
    faucet, bob = crypto.Key("aurix-dev-faucet"), crypto.Key(secrets.token_hex(8)).address
    res = send(chain, faucet, bob, 3)
    c.app.state.sync()
    assert c.get(f"/api/explorer/search?q={res['hash']}").json()["kind"] == "tx"
    a = c.get(f"/api/explorer/search?q={bob}").json()
    assert a["kind"] == "address" and a["balance"] == 3 and a["history"][0]["hash"] == res["hash"]
    b = c.get(f"/api/explorer/search?q={res['height']}").json()
    assert b["kind"] == "block" and any(t["hash"] == res["hash"] for t in b["block"]["txs"])
    assert c.get("/api/explorer/search?q=999999999").status_code == 404
    assert c.get("/api/explorer/search?q=" + "ab" * 32).status_code == 404
    assert c.get("/api/explorer/search?q=").status_code == 400
    assert c.get("/api/explorer/addresses/cosmos1x/history").status_code == 400


def test_lagging_tx_falls_back_to_chain(env):
    c, chain = env
    res = send(chain, crypto.Key("aurix-dev-faucet"), crypto.Key("lag").address, 1)
    # not synced yet: the explorer still answers from the chain
    assert c.get(f"/api/explorer/txs/{res['hash']}").json()["hash"] == res["hash"]


def test_background_sync_follows_the_chain(chain_urls, pg_url):
    with psycopg.connect(pg_url, autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS tx_addresses, txs, blocks, meta")
    s = Settings(chain_rpc_url=chain_urls[0], chain_rest_url=chain_urls[1], database_url=pg_url, poll_interval=0.2)
    with TestClient(create_app(s)) as c:  # startup event starts the sync thread
        for _ in range(50):
            h = c.get("/health").json()
            if h["indexed_height"] > 0 and h["lag"] is not None and h["lag"] <= 5:
                break
            time.sleep(0.2)
        assert h["indexed_height"] > 0 and h["lag"] <= 5 and h["last_error"] is None


def test_chain_down_is_502(pg_url):
    s = Settings(chain_rpc_url="http://127.0.0.1:1", chain_rest_url="http://127.0.0.1:1", database_url=pg_url,
                 poll_interval=0)
    c = TestClient(create_app(s))
    assert c.get("/api/explorer/proofs/" + "ab" * 32).status_code == 502
    assert c.get("/health").json()["chain_height"] is None


