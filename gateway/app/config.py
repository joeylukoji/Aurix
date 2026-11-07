import os
from dataclasses import dataclass, field


def _env(k: str, d: str = "") -> str:
    return os.environ.get(k, d)


@dataclass
class Settings:
    chain_url: str = field(default_factory=lambda: _env("CHAIN_RPC_URL", "http://localhost:26657"))
    chain_rest_url: str = field(default_factory=lambda: _env("CHAIN_REST_URL", "http://localhost:1317"))
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
    paypal_client_id: str = field(default_factory=lambda: _env("PAYPAL_CLIENT_ID"))
    paypal_client_secret: str = field(default_factory=lambda: _env("PAYPAL_CLIENT_SECRET"))
    paypal_webhook_id: str = field(default_factory=lambda: _env("PAYPAL_WEBHOOK_ID"))
    paypal_base_url: str = field(default_factory=lambda: _env("PAYPAL_BASE_URL", "https://api-m.sandbox.paypal.com"))
    # Dev-only seed fallbacks (legacy names). GATEWAY_/FAUCET_ MNEMONIC, KEY_FILE or SEED take priority
    # (app/keys.py); seeds are refused when AURIX_ENV=production. Defaults match the dev genesis.
    faucet_seed: str = field(default_factory=lambda: _env("AURIX_FAUCET_SEED", "aurix-dev-faucet"))
    gateway_seed: str = field(default_factory=lambda: _env("AURIX_GATEWAY_SEED", "aurix-dev-gateway"))

    # Seconds between polls of the chain for orders waiting for k-of-n attestations (0 disables the poller).
    attest_poll_interval: float = field(default_factory=lambda: float(_env("ATTEST_POLL_INTERVAL", "2")))

    # "simulator" (local paypal-sim, tests only) or "sandbox" (real PayPal Sandbox)
    paypal_env: str = field(default_factory=lambda: _env("PAYPAL_ENV", "sandbox"))

    # Comma separated origins allowed by CORS ("*" for dev; "" disables the middleware).
    cors_origins: list = field(default_factory=lambda: [o.strip() for o in _env("CORS_ORIGINS", "*").split(",")
                                                        if o.strip()])

    @property
    def paypal_configured(self) -> bool:
        return bool(self.paypal_client_id and self.paypal_client_secret)
