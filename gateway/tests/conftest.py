import os
import socket
import subprocess
import time
from pathlib import Path

import httpx
import pytest

os.environ["AURIX_NO_AUTOAPP"] = "1"
ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture(scope="session")
def chain_urls(tmp_path_factory):
    """Builds and runs a real single-validator Cosmos SDK aurixd node (200 ms blocks)."""
    tmp = tmp_path_factory.mktemp("chain")
    binary, home = tmp / "aurixd", tmp / "home"
    subprocess.run(["go", "build", "-o", str(binary), "./cmd/aurixd"], cwd=ROOT / "chain", check=True)
    env = os.environ | {"AURIXD": str(binary), "AURIX_HOME": str(home), "RESET": "1"}
    subprocess.run([str(ROOT / "chain/scripts/init-local.sh")], env=env, check=True, stdout=subprocess.DEVNULL)
    cfg = home / "config/config.toml"
    cfg.write_text(cfg.read_text().replace('timeout_commit = "1s"', 'timeout_commit = "200ms"'))
    rpc, api, grpc, p2p = (_free_port() for _ in range(4))
    proc = subprocess.Popen([str(binary), "start", "--home", str(home),
                             "--rpc.laddr", f"tcp://127.0.0.1:{rpc}", "--api.enable", "--api.address", f"tcp://127.0.0.1:{api}",
                             "--grpc.address", f"127.0.0.1:{grpc}", "--p2p.laddr", f"tcp://127.0.0.1:{p2p}",
                             "--rpc.pprof_laddr", ""],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    urls = (f"http://127.0.0.1:{rpc}", f"http://127.0.0.1:{api}")
    for _ in range(150):
        try:
            if int(httpx.get(urls[0] + "/status").json()["result"]["sync_info"]["latest_block_height"]) >= 2:
                httpx.get(urls[1] + "/aurix/smartmoney/v1/params").raise_for_status()
                break
        except (httpx.HTTPError, KeyError, ValueError):
            pass
        time.sleep(0.2)
    else:
        proc.terminate()
        raise RuntimeError("aurixd did not start")
    yield urls
    proc.terminate()


def guardian_transport(level="high", score=0.82, fail=False):
    calls = []

    def handler(req: httpx.Request):
        calls.append(req)
        if fail:
            return httpx.Response(503)
        return httpx.Response(200, json={"risk_score": score, "level": level, "advisory_only": True,
                                         "model_version": "test", "reasons": [
                                             {"feature": "new_recipient", "impact": 0.3, "text": "First payment"}]})
    t = httpx.MockTransport(handler)
    t.calls = calls
    return t
