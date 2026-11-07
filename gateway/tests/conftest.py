import os
import sys
from pathlib import Path

import pytest

os.environ["AURIX_NO_AUTOAPP"] = "1"
ROOT = Path(__file__).resolve().parents[2]
# The gateway's integration tests run the payments and indexer services in-process.
sys.path[:0] = [str(ROOT / "payments"), str(ROOT / "indexer")]


from aurix_common.testing import guardian_transport, running_chain, running_postgres, sign_attestation  # noqa: E402,F401

PG = {}


@pytest.fixture(scope="session")
def chain_urls(tmp_path_factory):
    """A real single-validator Cosmos SDK aurixd node (200 ms blocks)."""
    with running_chain(tmp_path_factory.mktemp("chain")) as urls:
        yield urls


@pytest.fixture(scope="session", autouse=True)
def pg_url():
    """PostgreSQL for the in-process indexer service (TEST_DATABASE_URL or a throwaway cluster)."""
    with running_postgres() as url:
        PG["url"] = url
        yield url
