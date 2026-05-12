from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

APP_NAME = "wx-sniffer"
LEGACY_MAC_NAME = "wx-sniffer-mac"


def _windows_local_appdata() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base)
    return Path.home() / "AppData" / "Local"


def app_data_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if sys.platform.startswith("win"):
        return _windows_local_appdata() / APP_NAME
    return Path.home() / ".local" / "share" / APP_NAME


def logs_dir() -> Path:
    return app_data_dir() / "logs"


def config_path() -> Path:
    return app_data_dir() / "config.json"


def db_path() -> Path:
    return app_data_dir() / "data.db"


def saved_cred_path() -> Path:
    return app_data_dir() / "saved_cred.json"


def log_file_path() -> Path:
    return logs_dir() / "app.log"


def mitmproxy_dir() -> Path:
    return app_data_dir() / "mitmproxy"


def ensure_dirs() -> None:
    app_data_dir().mkdir(parents=True, exist_ok=True)
    logs_dir().mkdir(parents=True, exist_ok=True)
    mitmproxy_dir().mkdir(parents=True, exist_ok=True)


def _legacy_candidates_for_db() -> list[Path]:
    candidates: list[Path] = []
    if sys.platform == "darwin":
        legacy_mac_dir = Path.home() / "Library" / "Application Support" / LEGACY_MAC_NAME
        candidates.append(legacy_mac_dir / "sniffer_mac.db")
        candidates.append(legacy_mac_dir / "data.db")
    project_dir = Path(__file__).resolve().parent
    candidates.append(project_dir / "data.db")
    candidates.append(project_dir / "sniffer_mac.db")
    return candidates


def _legacy_candidates_for_config() -> list[Path]:
    out: list[Path] = []
    if sys.platform == "darwin":
        legacy_mac_dir = Path.home() / "Library" / "Application Support" / LEGACY_MAC_NAME
        out.append(legacy_mac_dir / "config.json")
    return out


def _legacy_candidates_for_saved_cred() -> list[Path]:
    out: list[Path] = []
    if sys.platform == "darwin":
        legacy_mac_dir = Path.home() / "Library" / "Application Support" / LEGACY_MAC_NAME
        out.append(legacy_mac_dir / "saved_cred.json")
    return out


def migrate_legacy_data() -> dict:
    ensure_dirs()
    target_db = db_path()
    target_cfg = config_path()
    target_cred = saved_cred_path()
    migrated: dict[str, str] = {}

    def _migrate(src: Path, dst: Path, kind: str) -> None:
        if src.exists() and not dst.exists():
            marker = src.parent / f".migrated_{dst.name}"
            if marker.exists():
                return
            shutil.copy2(str(src), str(dst))
            marker.write_text(str(dst), encoding="utf-8")
            migrated[kind] = str(src)

    for src in _legacy_candidates_for_db():
        _migrate(src, target_db, "db")
        if "db" in migrated:
            break
    for src in _legacy_candidates_for_config():
        _migrate(src, target_cfg, "config")
        if "config" in migrated:
            break
    for src in _legacy_candidates_for_saved_cred():
        _migrate(src, target_cred, "saved_cred")
        if "saved_cred" in migrated:
            break

    return migrated
