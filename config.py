from __future__ import annotations

import json
from pathlib import Path

from platform_paths import (
    app_data_dir,
    config_path,
    db_path,
    ensure_dirs,
    log_file_path,
    migrate_legacy_data,
    saved_cred_path,
)


APP_NAME = "wx-sniffer"
APP_TITLE = "商品抓取工具"
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATE_DIR = BASE_DIR / "templates"


def _data_dir() -> Path:
    return app_data_dir()


def _config_file() -> Path:
    return config_path()


def _db_file() -> Path:
    return db_path()


def _log_file() -> Path:
    return log_file_path()


def _saved_cred_file() -> Path:
    return saved_cred_path()


DATA_DIR = _data_dir()
CONFIG_FILE = _config_file()
DB_FILE = _db_file()
LOG_FILE = _log_file()
SAVED_CRED_FILE = _saved_cred_file()


DEFAULT_CONFIG = {
    "local_auth": {
        "username": "admin",
        "password": "admin123",
    },
    "sync": {
        # 后台地址：默认走局域网 IP（运营机与后台同网段）。后台 nginx 默认 vhost 对
        # /api/ 返回 404，必须带正确的 Host 头才能路由到 backend → 见 host_header。
        # 如果以后配了 DNS / 内网域名，把 base_url 改成那个、host_header 留空即可。
        "base_url": "http://192.168.1.27",
        "host_header": "boss.fuliops.cn",
        # 「同步主系统」推到后台商品库的端点（写入 product_masters / product_variants，
        # 来源平台标记 wechat_meituan → 商品库页显示「小程序美团」）。
        "sync_path": "/api/products/sniffer/sync-to-catalog",
        "timeout_seconds": 20,
    },
    "capture": {
        "listen_host": "127.0.0.1",
        "listen_port": 8899,
        "import_path": "/api/internal/capture-products",
        "capture_token": "replace-with-random-token",
        "mitmdump_path": "",
        # `regular` = 普通 HTTP 代理 + 系统代理切换（macOS networksetup / Windows
        # 注册表 Internet Settings）。mitmproxy 11 的 `local` 模式（mitmproxy_rs
        # 内核级按进程重定向）实测会搞挂微信小程序的 TLS（小程序不信任 mitmproxy
        # CA），所以默认用 regular —— Mac / Windows 一致。
        "mode": "regular",
        # （local 模式才用到；保留以备将来）按进程名子串匹配；微信小程序在 WeChatAppEx 进程里
        "local_targets": ["WeChat", "WeChatAppEx", "WeApp"],
        "target_hosts": [
            "shangoue.meituan.com",
            "waimaieapp.meituan.com",
            "*.meituan.com",
            "*.meituan.net",
            "*.sankuai.com",
            "*.dianping.com",
        ],
    },
    "app": {
        "host": "127.0.0.1",
        "port": 5188,
        "window_title": APP_TITLE,
        "window_width": 1400,
        "window_height": 920,
    },
}


def ensure_data_dir() -> None:
    ensure_dirs()


def load_config() -> dict:
    ensure_data_dir()
    migrate_legacy_data()
    cfg_file = _config_file()
    if not cfg_file.exists():
        save_config(DEFAULT_CONFIG)
        return json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        data = json.loads(cfg_file.read_text(encoding="utf-8"))
    except Exception:
        save_config(DEFAULT_CONFIG)
        return json.loads(json.dumps(DEFAULT_CONFIG))

    merged = json.loads(json.dumps(DEFAULT_CONFIG))
    for section, values in data.items():
        if isinstance(values, dict) and isinstance(merged.get(section), dict):
            merged[section].update(values)
        else:
            merged[section] = values

    # One-time migration: 旧 config 指向 /api/products/sniffer/sync（只写隔离表）→
    # 改成 /api/products/sniffer/sync-to-catalog（写商品库）。base_url 仍是旧占位符
    # http://127.0.0.1:8000（指自己 loopback，根本连不到后台）→ 改成后台默认地址。
    # host_header 缺失 → 补上默认。用户已自定义的 base_url / host_header 不动。
    sync_cfg = merged.setdefault("sync", {})
    changed = False
    if sync_cfg.get("sync_path") in (None, "", "/api/products/sniffer/sync"):
        sync_cfg["sync_path"] = DEFAULT_CONFIG["sync"]["sync_path"]
        changed = True
    if sync_cfg.get("base_url") in (None, "", "http://127.0.0.1:8000", "http://api.fuliops.cn"):
        sync_cfg["base_url"] = DEFAULT_CONFIG["sync"]["base_url"]
        changed = True
    if "host_header" not in sync_cfg:
        sync_cfg["host_header"] = DEFAULT_CONFIG["sync"]["host_header"]
        changed = True
    # 旧 config 里 capture.mode 可能是实验期的 "local"（会搞挂微信小程序）→ 强制改回 regular
    cap_cfg = merged.setdefault("capture", {})
    if (cap_cfg.get("mode") or "").lower() in ("local", "local_only", "wireguard", "wireguard_only"):
        cap_cfg["mode"] = "regular"
        changed = True
    if changed:
        try:
            save_config(merged)
        except Exception:
            pass
    return merged


def save_config(data: dict) -> None:
    ensure_data_dir()
    _config_file().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
