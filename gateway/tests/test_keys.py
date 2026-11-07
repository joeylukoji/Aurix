"""H2: signer key loading (mnemonic > key file > dev seed) and BIP39/BIP32 derivation."""
import logging
import os

import pytest

from app import crypto
from app.keys import DEV_WARNING, KeyConfigError, load_key

MNEMONIC = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"
# cosmos19rl4cm2hmr8afy4kldpxz3fka4jguq0auqdal4 (well-known, m/44'/118'/0'/0/0) re-encoded with prefix "aur"
VECTOR = crypto.bech32_encode("aur", crypto.bech32_decode("cosmos19rl4cm2hmr8afy4kldpxz3fka4jguq0auqdal4")[1])
PRIV = "11" * 32


def test_derivation_vector():
    assert VECTOR == "aur19rl4cm2hmr8afy4kldpxz3fka4jguq0a69v8uw"
    assert crypto.Key.from_mnemonic(MNEMONIC).address == VECTOR
    assert crypto.Key.from_mnemonic("  " + MNEMONIC.replace(" ", "   ") + "\n").address == VECTOR


def test_bad_mnemonic():
    with pytest.raises(KeyConfigError):
        load_key("GATEWAY", {"GATEWAY_MNEMONIC": "abandon about"})


def _keyfile(tmp_path, content=PRIV, mode=0o600):
    p = tmp_path / "key.hex"
    p.write_text(content + "\n")
    os.chmod(p, mode)
    return str(p)


def test_key_file(tmp_path):
    k = load_key("GATEWAY", {"GATEWAY_KEY_FILE": _keyfile(tmp_path)})
    assert k.address == crypto.Key(private_key=bytes.fromhex(PRIV)).address
    assert load_key("GATEWAY", {"GATEWAY_KEY_FILE": _keyfile(tmp_path, "0x" + PRIV)}).address == k.address


def test_key_file_errors(tmp_path, caplog):
    with pytest.raises(KeyConfigError):
        load_key("GATEWAY", {"GATEWAY_KEY_FILE": str(tmp_path / "missing")})
    with pytest.raises(KeyConfigError):
        load_key("GATEWAY", {"GATEWAY_KEY_FILE": _keyfile(tmp_path, "zz")})
    with pytest.raises(KeyConfigError):
        load_key("GATEWAY", {"GATEWAY_KEY_FILE": _keyfile(tmp_path, "00" * 32)})
    with caplog.at_level(logging.WARNING):
        load_key("GATEWAY", {"GATEWAY_KEY_FILE": _keyfile(tmp_path, mode=0o644)})
    assert "chmod 600" in caplog.text


def test_priority_order(tmp_path):
    kf = _keyfile(tmp_path)
    file_addr = crypto.Key(private_key=bytes.fromhex(PRIV)).address
    seed_addr = crypto.Key("s").address
    env = {"GATEWAY_MNEMONIC": MNEMONIC, "GATEWAY_KEY_FILE": kf, "GATEWAY_SEED": "s"}
    assert load_key("GATEWAY", env, default_seed="d").address == VECTOR
    del env["GATEWAY_MNEMONIC"]
    assert load_key("GATEWAY", env, default_seed="d").address == file_addr
    del env["GATEWAY_KEY_FILE"]
    assert load_key("GATEWAY", env, default_seed="d").address == seed_addr
    assert load_key("GATEWAY", {}, default_seed="d").address == crypto.Key("d").address
    # prefixes are independent
    assert load_key("FAUCET", {"GATEWAY_MNEMONIC": MNEMONIC}, default_seed="d").address == crypto.Key("d").address


def test_seed_warns(caplog):
    with caplog.at_level(logging.WARNING):
        load_key("GATEWAY", {"GATEWAY_SEED": "s"})
    assert DEV_WARNING in caplog.text


def test_production_refuses_seed(tmp_path):
    for env in ({"GATEWAY_SEED": "s"}, {}):
        with pytest.raises(KeyConfigError, match="production"):
            load_key("GATEWAY", env | {"AURIX_ENV": "production"}, default_seed="d")
    prod = {"AURIX_ENV": "production"}
    assert load_key("GATEWAY", prod | {"GATEWAY_MNEMONIC": MNEMONIC}).address == VECTOR
    assert load_key("FAUCET", prod | {"FAUCET_KEY_FILE": _keyfile(tmp_path)}).address


def test_no_key_at_all():
    with pytest.raises(KeyConfigError):
        load_key("GATEWAY", {})


def test_app_refuses_seed_in_production(tmp_path, monkeypatch):
    from app.config import Settings
    from app.main import create_app
    monkeypatch.setenv("AURIX_ENV", "production")
    monkeypatch.setenv("GATEWAY_MNEMONIC", MNEMONIC)
    for v in ("FAUCET_MNEMONIC", "FAUCET_KEY_FILE"):
        monkeypatch.delenv(v, raising=False)
    s = Settings(chain_url="http://x", chain_rest_url="http://x", database_path=str(tmp_path / "a.db"))
    with pytest.raises(KeyConfigError, match="FAUCET"):
        create_app(s)
    monkeypatch.setenv("FAUCET_KEY_FILE", _keyfile(tmp_path))
    assert create_app(s) is not None
