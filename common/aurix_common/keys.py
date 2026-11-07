"""Signer key loading (H2). Priority: <PREFIX>_MNEMONIC, <PREFIX>_KEY_FILE, then <PREFIX>_SEED (dev only).

- *_MNEMONIC: BIP39 12/24 words, derived at m/44'/118'/0'/0/0 (same address as `aurixd keys add --recover`).
- *_KEY_FILE: file holding a 32-byte secp256k1 private key as hex (mode 0600 recommended).
- *_SEED: legacy dev key sha256(seed). Logs a warning; refused when AURIX_ENV=production.
"""
from __future__ import annotations

import logging
import os
import stat
from typing import Mapping

from .crypto import Key

log = logging.getLogger("aurix.keys")
DEV_WARNING = "dev seed key — never use on a public network"


class KeyConfigError(RuntimeError):
    pass


def is_production(env: Mapping[str, str]) -> bool:
    return env.get("AURIX_ENV", "").strip().lower() == "production"


def load_key(prefix: str, env: Mapping[str, str] | None = None, default_seed: str = "") -> Key:
    """Loads the key for `prefix` (e.g. "GATEWAY"). `default_seed` is the dev seed used when neither
    <PREFIX>_MNEMONIC, <PREFIX>_KEY_FILE nor <PREFIX>_SEED is set (Settings passes the legacy
    AURIX_<PREFIX>_SEED / built-in dev default)."""
    env = os.environ if env is None else env
    mnemonic = env.get(f"{prefix}_MNEMONIC", "").strip()
    if mnemonic:
        try:
            return Key.from_mnemonic(mnemonic)
        except ValueError as e:
            raise KeyConfigError(f"{prefix}_MNEMONIC: {e}") from None
    key_file = env.get(f"{prefix}_KEY_FILE", "").strip()
    if key_file:
        try:
            mode = os.stat(key_file).st_mode
            with open(key_file) as f:
                raw = f.read().strip().removeprefix("0x")
            key = Key(private_key=bytes.fromhex(raw))
        except (OSError, ValueError) as e:
            raise KeyConfigError(f"{prefix}_KEY_FILE {key_file}: {e}") from None
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
            log.warning("%s_KEY_FILE %s is accessible by group/others; chmod 600 recommended", prefix, key_file)
        return key
    seed = env.get(f"{prefix}_SEED", "") or default_seed
    if not seed:
        raise KeyConfigError(f"no key configured: set {prefix}_MNEMONIC or {prefix}_KEY_FILE")
    if is_production(env):
        raise KeyConfigError(f"{prefix}: seed keys are refused when AURIX_ENV=production; "
                             f"set {prefix}_MNEMONIC or {prefix}_KEY_FILE")
    log.warning("%s key from a seed: %s", prefix, DEV_WARNING)
    return Key(seed)
