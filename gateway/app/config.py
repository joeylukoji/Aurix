import os
from dataclasses import dataclass, field


def _env(k: str, d: str = "") -> str:
    return os.environ.get(k, d)


@dataclass
class Settings:
    chain_url: str = field(default_factory=lambda: _env("CHAIN_RPC_URL", "http://localhost:26657"))
    chain_rest_url: str = field(default_factory=lambda: _env("CHAIN_REST_URL", "http://localhost:1317"))
    guardian_url: str = field(default_factory=lambda: _env("GUARDIAN_URL", "http://localhost:8000"))
    database_path: str = field(default_factory=lambda: _env("DATABASE_URL", "sqlite:///./aurix.db").removeprefix("sqlite:///"))
    paypal_client_id: str = field(default_factory=lambda: _env("PAYPAL_CLIENT_ID"))
    paypal_client_secret: str = field(default_factory=lambda: _env("PAYPAL_CLIENT_SECRET"))
    paypal_webhook_id: str = field(default_factory=lambda: _env("PAYPAL_WEBHOOK_ID"))
    paypal_base_url: str = field(default_factory=lambda: _env("PAYPAL_BASE_URL", "https://api-m.sandbox.paypal.com"))
    # Dev-only seeds; must match the chain's genesis. Never use in production.
    faucet_seed: str = field(default_factory=lambda: _env("AURIX_FAUCET_SEED", "aurix-dev-faucet"))
    gateway_seed: str = field(default_factory=lambda: _env("AURIX_GATEWAY_SEED", "aurix-dev-gateway"))

    @property
    def paypal_mock(self) -> bool:
        return not (self.paypal_client_id and self.paypal_client_secret)
