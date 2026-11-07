"""Guardian signing key (H3).

Priority: GUARDIAN_MNEMONIC (BIP39, m/44'/118'/0'/0/0) > GUARDIAN_KEY_FILE (32-byte hex private key)
> GUARDIAN_SEED (dev only: privkey = sha256(seed); refused when AURIX_ENV=production).
With nothing configured a fixed dev seed is used so the dev stack keeps working (also refused in production).
"""
from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path

import coincurve
from bip_utils import Bech32Encoder, Bip32Slip10Secp256k1, Bip39SeedGenerator
from bip_utils import Hash160

log = logging.getLogger("guardian.keys")
DEFAULT_DEV_SEED = "aurix-guardian-dev"
HRP = "aur"
DEV_WARNING = "dev seed key — never use on a public network"


class KeyConfigError(RuntimeError):
    pass


def address_from_pubkey(pub33: bytes, hrp: str = HRP) -> str:
    """bech32(hrp, ripemd160(sha256(compressed pubkey))) — Cosmos account address."""
    return Bech32Encoder.Encode(hrp, Hash160.QuickDigest(pub33))


@dataclass(frozen=True)
class GuardianKey:
    priv: coincurve.PrivateKey
    source: str

    @property
    def pubkey(self) -> bytes:
        return self.priv.public_key.format(compressed=True)

    @property
    def address(self) -> str:
        return address_from_pubkey(self.pubkey)

    def sign(self, msg: bytes) -> bytes:
        """64-byte r||s over sha256(msg), low-S (libsecp256k1 always emits normalized signatures)."""
        return self.priv.sign_recoverable(msg, hasher=lambda m: hashlib.sha256(m).digest())[:64]


def from_mnemonic(mnemonic: str) -> coincurve.PrivateKey:
    """bip_utils validates the English wordlist and the checksum; errors become KeyConfigError."""
    try:
        seed = Bip39SeedGenerator(" ".join(mnemonic.split())).Generate()
    except Exception as e:  # MnemonicChecksumError, ValueError (unknown word / length)
        raise KeyConfigError(f"GUARDIAN_MNEMONIC: invalid BIP39 mnemonic ({e})") from None
    node = Bip32Slip10Secp256k1.FromSeed(seed).DerivePath("m/44'/118'/0'/0/0")
    return coincurve.PrivateKey(node.PrivateKey().Raw().ToBytes())


def load_key(env=None) -> GuardianKey:
    env = os.environ if env is None else env
    production = env.get("AURIX_ENV", "").lower() == "production"
    if env.get("GUARDIAN_MNEMONIC"):
        return GuardianKey(from_mnemonic(env["GUARDIAN_MNEMONIC"]), "mnemonic")
    if env.get("GUARDIAN_KEY_FILE"):
        p = Path(env["GUARDIAN_KEY_FILE"])
        raw = bytes.fromhex(p.read_text().strip().removeprefix("0x"))
        if len(raw) != 32:
            raise KeyConfigError("GUARDIAN_KEY_FILE must contain a 32-byte hex private key")
        if p.stat().st_mode & 0o077:
            log.warning("GUARDIAN_KEY_FILE %s is readable by group/others; chmod 600 recommended", p)
        return GuardianKey(coincurve.PrivateKey(raw), "key_file")
    if production:
        raise KeyConfigError("AURIX_ENV=production: set GUARDIAN_MNEMONIC or GUARDIAN_KEY_FILE (dev seed refused)")
    seed = env.get("GUARDIAN_SEED") or DEFAULT_DEV_SEED
    log.warning("GUARDIAN: %s (%s)", DEV_WARNING, "GUARDIAN_SEED" if env.get("GUARDIAN_SEED") else "built-in default")
    return GuardianKey(coincurve.PrivateKey(hashlib.sha256(seed.encode()).digest()), "dev_seed")
