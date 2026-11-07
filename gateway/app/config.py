import os
from dataclasses import dataclass, field



def _env(k: str, d: str = "") -> str:
    return os.environ.get(k, d)



@dataclass
class Settings:
    chain_url: str = field(default_factory=lambda: _env("CHAIN_RPC_URL", "http://localhost:26657"))
    chain_rest_url: str = field(default_factory=lambda: _env("CHAIN_REST_URL", "http://localhost:1317"))
    # Internal services the gateway routes to.
    indexer_url: str = field(default_factory=lambda: _env("INDEXER_URL", "http://localhost:8086"))
    payments_url: str = field(default_factory=lambda: _env("PAYMENTS_URL", "http://localhost:8085"))
    guardian_url: str = field(default_factory=lambda: _env("GUARDIAN_URL", "http://localhost:8000"))
    # H3 quorum: comma separated Guardian URLs (falls back to GUARDIAN_URL), how many valid signed scores
    # are required for quorum_met, and an optional allowlist of guardian pubkeys (33-byte compressed hex).
    guardian_urls: list = field(default_factory=lambda: [u.strip() for u in
                                (_env("GUARDIAN_URLS") or _env("GUARDIAN_URL", "http://localhost:8000")).split(",")
                                if u.strip()])
    guardian_quorum: int = field(default_factory=lambda: int(_env("GUARDIAN_QUORUM", "1")))
    guardian_pubkeys: list = field(default_factory=lambda: [p.strip() for p in _env("GUARDIAN_PUBKEYS").split(",")
                                                            if p.strip()])
    database_path: str = field(default_factory=lambda: _env("DATABASE_URL", "sqlite:///./aurix.db").removeprefix("sqlite:///"))
    # Dev-only faucet seed fallback (legacy name). FAUCET_MNEMONIC, FAUCET_KEY_FILE or FAUCET_SEED take priority
    # (app/keys.py); seeds are refused when AURIX_ENV=production. Defaults match the dev genesis.
    faucet_seed: str = field(default_factory=lambda: _env("AURIX_FAUCET_SEED", "aurix-dev-faucet"))

    # Comma separated origins allowed by CORS ("*" for dev; "" disables the middleware).
    cors_origins: list = field(default_factory=lambda: [o.strip() for o in _env("CORS_ORIGINS", "*").split(",")
                                                        if o.strip()])

