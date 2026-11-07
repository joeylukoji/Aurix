import os
from dataclasses import dataclass, field


def _env(k: str, d: str = "") -> str:
    return os.environ.get(k, d)


@dataclass
class Settings:
    chain_rpc_url: str = field(default_factory=lambda: _env("CHAIN_RPC_URL", "http://localhost:26657"))
    chain_rest_url: str = field(default_factory=lambda: _env("CHAIN_REST_URL", "http://localhost:1317"))
    # PostgreSQL connection string, e.g. postgresql://aurix:aurix@localhost:5432/aurix
    database_url: str = field(default_factory=lambda: _env("INDEXER_DATABASE_URL", _env("DATABASE_URL", "")))
    # Seconds between sync rounds (0 disables the background sync; tests call sync_once()).
    poll_interval: float = field(default_factory=lambda: float(_env("INDEXER_POLL_INTERVAL", "1")))
    # First height to index on an empty database (1 = full history).
    start_height: int = field(default_factory=lambda: int(_env("INDEXER_START_HEIGHT", "1")))
    cors_origins: list = field(default_factory=lambda: [o.strip() for o in _env("CORS_ORIGINS", "*").split(",")
                                                        if o.strip()])
