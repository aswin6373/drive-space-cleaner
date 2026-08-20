from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dependency is optional at runtime
    load_dotenv = None


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DOWNLOAD_DIR = PROJECT_ROOT / "downloads"
DEFAULT_REPORT_DIR = PROJECT_ROOT / "reports"
DEFAULT_LOG_DIR = PROJECT_ROOT / "logs"
DEFAULT_CREDENTIALS_PATH = PROJECT_ROOT / "credentials.json"
DEFAULT_TOKEN_PATH = PROJECT_ROOT / "token.json"


@dataclass(frozen=True)
class AppConfig:
    credentials_path: Path
    token_path: Path
    download_dir: Path
    report_dir: Path
    log_dir: Path


def _path_from_env(name: str, default: Path) -> Path:
    value = os.getenv(name)
    return Path(value).expanduser() if value else default


def load_config(
    *,
    credentials_path: Optional[str] = None,
    token_path: Optional[str] = None,
    download_dir: Optional[str] = None,
    report_dir: Optional[str] = None,
) -> AppConfig:
    if load_dotenv:
        load_dotenv(PROJECT_ROOT / ".env")

    config = AppConfig(
        credentials_path=Path(credentials_path).expanduser()
        if credentials_path
        else _path_from_env("DRIVE_CLEANER_CREDENTIALS", DEFAULT_CREDENTIALS_PATH),
        token_path=Path(token_path).expanduser()
        if token_path
        else _path_from_env("DRIVE_CLEANER_TOKEN", DEFAULT_TOKEN_PATH),
        download_dir=Path(download_dir).expanduser()
        if download_dir
        else _path_from_env("DRIVE_CLEANER_DOWNLOAD_DIR", DEFAULT_DOWNLOAD_DIR),
        report_dir=Path(report_dir).expanduser()
        if report_dir
        else _path_from_env("DRIVE_CLEANER_REPORT_DIR", DEFAULT_REPORT_DIR),
        log_dir=_path_from_env("DRIVE_CLEANER_LOG_DIR", DEFAULT_LOG_DIR),
    )
    return config


def ensure_runtime_dirs(config: AppConfig) -> None:
    config.download_dir.mkdir(parents=True, exist_ok=True)
    config.report_dir.mkdir(parents=True, exist_ok=True)
    config.log_dir.mkdir(parents=True, exist_ok=True)
