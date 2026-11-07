"""Test helpers shared by the service test suites: a real aurixd node, a throwaway PostgreSQL,
and signed Guardian attestations. Not used at runtime."""
from __future__ import annotations

import contextlib
import glob
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@contextlib.contextmanager
def running_chain(tmp: Path):
    """Builds and runs a real single-validator aurixd node (200 ms blocks); yields (rpc_url, rest_url)."""
    binary, home = tmp / "aurixd", tmp / "home"
    subprocess.run(["go", "build", "-o", str(binary), "./cmd/aurixd"], cwd=ROOT / "chain", check=True)
    env = os.environ | {"AURIXD": str(binary), "AURIX_HOME": str(home), "RESET": "1"}
    subprocess.run([str(ROOT / "chain/scripts/init-local.sh")], env=env, check=True, stdout=subprocess.DEVNULL)
    cfg = home / "config/config.toml"
    cfg.write_text(cfg.read_text().replace('timeout_commit = "1s"', 'timeout_commit = "200ms"'))
    rpc, api, grpc, p2p = (free_port() for _ in range(4))
    proc = subprocess.Popen([str(binary), "start", "--home", str(home),
                             "--rpc.laddr", f"tcp://127.0.0.1:{rpc}", "--api.enable", "--api.address",
                             f"tcp://127.0.0.1:{api}", "--grpc.address", f"127.0.0.1:{grpc}",
                             "--p2p.laddr", f"tcp://127.0.0.1:{p2p}", "--rpc.pprof_laddr", ""],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    urls = (f"http://127.0.0.1:{rpc}", f"http://127.0.0.1:{api}")
    try:
        for _ in range(150):
            try:
                if int(httpx.get(urls[0] + "/status").json()["result"]["sync_info"]["latest_block_height"]) >= 2:
                    httpx.get(urls[1] + "/aurix/smartmoney/v1/params").raise_for_status()
                    break
            except (httpx.HTTPError, KeyError, ValueError):
                pass
            time.sleep(0.2)
        else:
            raise RuntimeError("aurixd did not start")
        yield urls
    finally:
        proc.terminate()


def _pg_bin(name: str) -> str:
    found = shutil.which(name) or next(iter(sorted(glob.glob(f"/usr/lib/postgresql/*/bin/{name}"), reverse=True)), None)
    if not found:
        raise RuntimeError(f"{name} not found: install PostgreSQL or set TEST_DATABASE_URL")
    return found


@contextlib.contextmanager
def running_postgres():
    """Yields a PostgreSQL URL. Uses TEST_DATABASE_URL when set (e.g. a CI service container); otherwise
    starts a throwaway cluster with initdb/pg_ctl on a free port (as user `postgres` when run as root)."""
    if os.environ.get("TEST_DATABASE_URL"):
        yield os.environ["TEST_DATABASE_URL"]
        return
    as_root = os.geteuid() == 0
    prefix = ["runuser", "-u", "postgres", "--"] if as_root else []
    data = Path(tempfile.mkdtemp(prefix="aurix-pg-"))
    if as_root:
        shutil.chown(data, "postgres", "postgres")
    port = free_port()
    subprocess.run(prefix + [_pg_bin("initdb"), "-D", str(data), "-U", "aurix", "--auth=trust", "-E", "UTF8"],
                   check=True, stdout=subprocess.DEVNULL)
    subprocess.run(prefix + [_pg_bin("pg_ctl"), "-D", str(data), "-o", f"-p {port} -k {data} -c listen_addresses=127.0.0.1",
                             "-l", str(data / "log"), "-w", "start"], check=True, stdout=subprocess.DEVNULL)
    try:
        subprocess.run(prefix + [_pg_bin("createdb"), "-h", "127.0.0.1", "-p", str(port), "-U", "aurix", "aurix"],
                       check=True)
        yield f"postgresql://aurix@127.0.0.1:{port}/aurix"
    finally:
        subprocess.run(prefix + [_pg_bin("pg_ctl"), "-D", str(data), "-m", "immediate", "stop"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        shutil.rmtree(data, ignore_errors=True)


def sign_attestation(seed: str, body: dict, score: float, level: str, **override) -> dict:
    """A Guardian attestation (contract H3) signed with the dev key sha256(seed)."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

    from .crypto import Key
    from .guardians import N, address_from_pubkey

    k = Key(seed)
    payload = {"chain_id": "aurix-test", "from": body.get("sender"), "to": body.get("recipient"),
               "amount": str(body.get("amount")), "denom": body.get("currency"), "score": score,
               "decision": level, "model_version": "test", "issued_at": int(time.time())} | override
    payload = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    r, s = decode_dss_signature(k._sk.sign(payload.encode(), ec.ECDSA(hashes.SHA256())))
    s = min(s, N - s)
    return {"guardian": address_from_pubkey(k.pubkey), "pubkey": k.pubkey.hex(), "payload": payload,
            "signature": (r.to_bytes(32, "big") + s.to_bytes(32, "big")).hex()}


def guardian_transport(level="high", score=0.82, fail=False, seed="test-guardian"):
    """A mock Neural Guardian answering signed scores (or 503 when fail=True)."""
    calls = []

    def handler(req: httpx.Request):
        calls.append(req)
        if fail:
            return httpx.Response(503)
        body = json.loads(req.content)
        return httpx.Response(200, json={"risk_score": score, "level": level, "advisory_only": True,
                                         "model_version": "test", "reasons": [
                                             {"feature": "new_recipient", "impact": 0.3, "text": "First payment"}],
                                         "attestation": sign_attestation(seed, body, score, level)})
    t = httpx.MockTransport(handler)
    t.calls = calls
    return t


