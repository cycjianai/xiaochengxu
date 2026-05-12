from __future__ import annotations

import ctypes
import os
import sys
from typing import Iterable

from cert_installer import is_cert_trusted


def is_elevated() -> bool:
    if sys.platform.startswith("win"):
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
        except Exception:
            return False
    try:
        return os.geteuid() == 0  # type: ignore[attr-defined]
    except Exception:
        return False


def wintun_ready() -> bool | None:
    if not sys.platform.startswith("win"):
        return None
    try:
        import importlib

        mod = importlib.import_module("mitmproxy.addons.wireguard")
        return mod is not None
    except Exception:
        return False


CONFLICTING_PROCESS_NAMES = {
    "charles": "Charles",
    "fiddler": "Fiddler",
    "wireshark": "Wireshark",
    "proxifier": "Proxifier",
}


def _iter_process_names() -> Iterable[str]:
    """Yield lowercase process basenames. Best-effort, no hard dep on psutil."""
    if sys.platform.startswith("win"):
        try:
            import subprocess

            out = subprocess.check_output(
                ["tasklist", "/FO", "CSV", "/NH"], text=True, timeout=5
            )
            for line in out.splitlines():
                parts = [p.strip().strip('"') for p in line.split(",")]
                if parts and parts[0]:
                    yield parts[0].lower()
        except Exception:
            return
        return
    try:
        import subprocess

        out = subprocess.check_output(["ps", "-Ao", "comm="], text=True, timeout=5)
        for line in out.splitlines():
            name = line.strip().split("/")[-1]
            if name:
                yield name.lower()
    except Exception:
        return


def conflicting_tools() -> list[str]:
    found: set[str] = set()
    for name in _iter_process_names():
        for needle, pretty in CONFLICTING_PROCESS_NAMES.items():
            if needle in name:
                found.add(pretty)
        # External mitmproxy / mitmdump that we did NOT start ourselves
        if "mitmdump" in name or name.startswith("mitmproxy"):
            found.add("mitmproxy")
    return sorted(found)


def health_snapshot() -> dict:
    cert_trusted = is_cert_trusted()
    elevated = is_elevated()
    tools = conflicting_tools()
    wintun = wintun_ready()

    recommendations: list[str] = []
    if not cert_trusted:
        recommendations.append(
            "mitmproxy 根证书未装入系统信任。点击\"开始抓取\"会自动弹一次密码框（macOS Authorization Services / Windows UAC），输入一次即可。"
        )
    if tools:
        recommendations.append(f"检测到可能冲突的本机抓包工具: {', '.join(tools)}，建议关闭后再抓包以降低风控特征聚合风险。")

    snapshot = {
        "cert_trusted": cert_trusted,
        "is_elevated": elevated,
        "conflicting_tools": tools,
        "recommendations": " ".join(recommendations) if recommendations else "环境健康，未检测到已知风险。",
    }
    if sys.platform.startswith("win"):
        snapshot["wintun_ready"] = bool(wintun)
    return snapshot
