"""Key loading (contract H2): ATTESTOR_MNEMONIC > ATTESTOR_KEY_FILE > ATTESTOR_SEED (dev only).

Minimal BIP39 (seed only, PBKDF2-HMAC-SHA512) + BIP32 secp256k1 derivation with hashlib, so the address
matches `aurixd keys add <name> --recover` (coin type 118).
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import stat
import unicodedata

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from .crypto import _N, Key

log = logging.getLogger("attestor")

COSMOS_PATH = "m/44'/118'/0'/0/0"
DEV_WARNING = "dev seed key — never use on a public network"
_HARD = 0x80000000


class KeyConfigError(SystemExit):
    pass


def mnemonic_to_seed(mnemonic: str, passphrase: str = "") -> bytes:
    m = unicodedata.normalize("NFKD", " ".join(mnemonic.split()))
    salt = unicodedata.normalize("NFKD", "mnemonic" + passphrase)
    return hashlib.pbkdf2_hmac("sha512", m.encode(), salt.encode(), 2048)


def _pub(k: int) -> bytes:
    return ec.derive_private_key(k, ec.SECP256K1()).public_key().public_bytes(Encoding.X962,
                                                                               PublicFormat.CompressedPoint)


def derive_private_key(seed: bytes, path: str = COSMOS_PATH) -> bytes:
    i = hmac.new(b"Bitcoin seed", seed, hashlib.sha512).digest()
    k, chain = int.from_bytes(i[:32], "big"), i[32:]
    parts = path.split("/")
    if parts[0] != "m":
        raise ValueError(f"bad path {path}")
    for p in parts[1:]:
        idx = int(p.rstrip("'")) + (_HARD if p.endswith("'") else 0)
        data = (b"\x00" + k.to_bytes(32, "big")) if idx & _HARD else _pub(k)
        i = hmac.new(chain, data + idx.to_bytes(4, "big"), hashlib.sha512).digest()
        il = int.from_bytes(i[:32], "big")
        if il >= _N:
            raise ValueError("invalid child key")
        k, chain = (il + k) % _N, i[32:]
        if k == 0:
            raise ValueError("invalid child key")
    return k.to_bytes(32, "big")


def key_from_mnemonic(mnemonic: str, path: str = COSMOS_PATH) -> Key:
    from .bip39 import validate
    try:
        validate(mnemonic)
    except ValueError as e:
        raise KeyConfigError(f"ATTESTOR_MNEMONIC: {e}") from None
    return Key(private_key=derive_private_key(mnemonic_to_seed(mnemonic), path))


def key_from_file(path: str) -> Key:
    try:
        st = os.stat(path)
        with open(path, encoding="utf-8") as f:
            raw = f.read().strip().removeprefix("0x")
    except OSError as e:
        raise KeyConfigError(f"cannot read key file {path}: {e}") from None
    if st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        log.warning("key file %s is readable by group/others; chmod 600 recommended", path)
    try:
        b = bytes.fromhex(raw)
        if len(b) != 32:
            raise ValueError
        return Key(private_key=b)
    except ValueError:
        raise KeyConfigError(f"key file {path} must contain a 32-byte private key in hex") from None


def load_key(mnemonic: str = "", key_file: str = "", seed: str = "", env_name: str = "") -> Key:
    """Priority: mnemonic, key file, dev seed (refused when AURIX_ENV=production)."""
    if mnemonic.strip():
        return key_from_mnemonic(mnemonic)
    if key_file:
        return key_from_file(key_file)
    if seed:
        if env_name.lower() == "production":
            raise KeyConfigError("ATTESTOR_SEED is a dev-only key and is refused with AURIX_ENV=production; "
                                 "use ATTESTOR_MNEMONIC or ATTESTOR_KEY_FILE")
        log.warning(DEV_WARNING)
        return Key(seed)
    raise KeyConfigError("one of ATTESTOR_MNEMONIC, ATTESTOR_KEY_FILE or ATTESTOR_SEED is required")
