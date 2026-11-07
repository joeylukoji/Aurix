import os

import pytest

os.environ["AURIX_NO_AUTOAPP"] = "1"

from aurix_common.testing import running_chain, running_postgres  # noqa: E402


@pytest.fixture(scope="session")
def chain_urls(tmp_path_factory):
    with running_chain(tmp_path_factory.mktemp("chain")) as urls:
        yield urls


@pytest.fixture(scope="session")
def pg_url():
    with running_postgres() as url:
        yield url
