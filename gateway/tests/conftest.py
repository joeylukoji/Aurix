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
def chain_url(tmp_path_factory):
    """Builds and runs a real aurixd node (integration tests against the real ledger)."""
    binary = tmp_path_factory.mktemp("bin") / "aurixd"
    subprocess.run(["go", "build", "-o", str(binary), "./cmd/aurixd"], cwd=ROOT / "chain", check=True)
    port = _free_port()
    env = os.environ | {"AURIX_LISTEN": f"127.0.0.1:{port}", "AURIX_BLOCK_INTERVAL": "100ms"}
    proc = subprocess.Popen([str(binary)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}"
    for _ in range(50):
        try:
            httpx.get(url + "/status")
            break
        except httpx.HTTPError:
            time.sleep(0.1)
    yield url
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
