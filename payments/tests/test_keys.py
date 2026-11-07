import pytest

from aurix_common.keys import KeyConfigError
from aurix_payments.config import Settings
from aurix_payments.main import create_app

MNEMONIC = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"


def test_anchor_key_seed_refused_in_production(tmp_path, monkeypatch):
    """payments holds the anchor key: with AURIX_ENV=production a dev seed is refused, a mnemonic accepted."""
    monkeypatch.setenv("AURIX_ENV", "production")
    for v in ("GATEWAY_MNEMONIC", "GATEWAY_KEY_FILE"):
        monkeypatch.delenv(v, raising=False)
    s = Settings(chain_url="http://x", chain_rest_url="http://x", database_path=str(tmp_path / "p.db"),
                 attest_poll_interval=0)
    with pytest.raises(KeyConfigError, match="GATEWAY"):
        create_app(s)
    monkeypatch.setenv("GATEWAY_MNEMONIC", MNEMONIC)
    assert create_app(s).state.paypal_mode in ("disabled", "sandbox", "simulator")
