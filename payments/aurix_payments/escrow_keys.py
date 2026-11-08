"""Keys of the evidence escrow: AURIX's Ed25519 signing key and the registry of accepted sources/arbiters.

I/O at the edge: environment and key files are read here, once, at service start (CODING_STANDARDS §4).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Mapping

from aurix_common.keys import KeyConfigError, is_production
from aurix_verify import crypto

log = logging.getLogger("aurix.escrow")

DEMO_CARRIER = "aurix_demo_delivery"


def _dev_seed(label: str) -> bytes:
    return hashlib.sha256(label.encode()).digest()


def load_signer(env: Mapping[str, str] | None = None) -> crypto.Signer:
    """ESCROW_KEY_FILE (hex 32-byte Ed25519 seed) > ESCROW_SEED / built-in dev seed (refused in production)."""
    env = os.environ if env is None else env
    key_id = env.get("ESCROW_KEY_ID", "aurix-escrow-dev-1")
    path = env.get("ESCROW_KEY_FILE", "").strip()
    if path:
        try:
            with open(path) as f:
                return crypto.Signer.from_seed(key_id, bytes.fromhex(f.read().strip()))
        except (OSError, ValueError) as e:
            raise KeyConfigError(f"ESCROW_KEY_FILE {path}: {e}") from None
    if is_production(env):
        raise KeyConfigError("ESCROW_KEY_FILE is required when AURIX_ENV=production")
    log.warning("escrow signing key from a dev seed — never use on a public network")
    return crypto.Signer.from_seed(key_id, _dev_seed(env.get("ESCROW_SEED", "aurix-dev-escrow")))


def dev_key_entry(signer: crypto.Signer, role: str, owner: str, source_type: str = "real") -> dict:
    return {"key_id": signer.key_id, "alg": crypto.ALG, "public_key": signer.public_key, "role": role,
            "owner": owner, "source_type": source_type, "valid_from": "2026-01-01T00:00:00Z",
            "valid_until": None, "revoked_at": None, "revocation_reason": None}


DEV_CARRIER = crypto.Signer.from_seed("aurix-demo-delivery-dev-1", _dev_seed("aurix-dev-delivery"))
DEV_ARBITER = crypto.Signer.from_seed("aurix-arbiter-dev-1", _dev_seed("aurix-dev-arbiter"))


def load_external_keys(env: Mapping[str, str] | None = None) -> list[dict]:
    """Keys of the sources and arbiters AURIX accepts (ESCROW_TRUSTED_KEYS_FILE, a public-keys.json).
    Without it, outside production: the dev demo carrier and dev arbiter."""
    env = os.environ if env is None else env
    path = env.get("ESCROW_TRUSTED_KEYS_FILE", "").strip()
    if path:
        with open(path) as f:
            return json.load(f)["keys"]
    if is_production(env):
        return []
    return [dev_key_entry(DEV_CARRIER, f"source:{DEMO_CARRIER}", "AURIX demo carrier (simulated)", "simulated"),
            dev_key_entry(DEV_ARBITER, "arbiter", "AURIX demo arbiter")]
