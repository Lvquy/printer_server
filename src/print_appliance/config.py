from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    host: str = "127.0.0.1"
    port: int = 8081
    secure_cookie: bool = False
    worker_interval_seconds: float = 2.0
    max_request_bytes: int = 12 * 1024 * 1024

    @property
    def database_path(self) -> Path:
        return self.data_dir / "appliance.sqlite3"

    @property
    def spool_dir(self) -> Path:
        return self.data_dir / "spool"

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            data_dir=Path(os.environ.get("PRINT_APPLIANCE_DATA_DIR", "/var/lib/print-appliance")),
            host=os.environ.get("PRINT_APPLIANCE_HOST", "127.0.0.1"),
            port=int(os.environ.get("PRINT_APPLIANCE_PORT", "8081")),
            secure_cookie=os.environ.get("PRINT_APPLIANCE_SECURE_COOKIE", "0").lower()
            in {"1", "true", "yes"},
        )
