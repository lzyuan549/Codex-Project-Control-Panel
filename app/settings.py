from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    workspace_root: Path
    admin_password: str
    session_secret: str
    gateway_base_url: str
    gateway_api_key: str
    codex_model: str
    codex_bin: str

    @property
    def gateway_configured(self) -> bool:
        return bool(self.gateway_base_url and self.gateway_api_key and self.codex_model)


def load_settings() -> Settings:
    return Settings(
        data_dir=Path(os.getenv("DATA_DIR", "/data")).resolve(),
        workspace_root=Path(os.getenv("WORKSPACE_ROOT", "/www/wwwroot")).resolve(),
        admin_password=os.getenv("ADMIN_PASSWORD", "admin"),
        session_secret=os.getenv("SESSION_SECRET", secrets.token_urlsafe(32)),
        gateway_base_url=os.getenv("CODEX_GATEWAY_BASE_URL", "").rstrip("/"),
        gateway_api_key=os.getenv("CODEX_GATEWAY_API_KEY", ""),
        codex_model=os.getenv("CODEX_MODEL", "gpt-5.5"),
        codex_bin=os.getenv("CODEX_BIN", "codex"),
    )


def write_codex_config(settings: Settings) -> Path:
    codex_home = Path(os.getenv("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
    codex_home.mkdir(parents=True, exist_ok=True)
    config_path = codex_home / "config.toml"

    if settings.gateway_base_url:
        config_path.write_text(
            "\n".join(
                [
                    f'model = "{settings.codex_model}"',
                    'model_provider = "gateway"',
                    "",
                    "[model_providers.gateway]",
                    'name = "Gateway"',
                    f'base_url = "{settings.gateway_base_url}"',
                    'env_key = "CODEX_GATEWAY_API_KEY"',
                    'wire_api = "responses"',
                    "",
                ]
            ),
            encoding="utf-8",
        )
    return config_path
