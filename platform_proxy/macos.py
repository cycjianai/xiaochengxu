from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from db import add_log
from platform_proxy.base import AbstractProxyManager


class MacProxyManager(AbstractProxyManager):
    """macOS implementation.

    Preferred mode is mitmproxy `wireguard` (transparent, app sees no proxy).
    On wireguard failure we fall back to `regular` HTTP proxy + macOS system
    proxy switch via `networksetup` so existing apps route through us.
    """

    def _default_mitmdump_candidates(self) -> list[Path]:
        base = super()._default_mitmdump_candidates()
        base.extend(
            [
                Path("/opt/homebrew/bin/mitmdump"),
                Path("/usr/local/bin/mitmdump"),
            ]
        )
        return base

    def _networksetup(self) -> str | None:
        return shutil.which("networksetup")

    def _list_services(self, tool: str) -> list[str]:
        services_output = subprocess.check_output([tool, "-listallnetworkservices"], text=True)
        out: list[str] = []
        for idx, raw in enumerate(services_output.splitlines()):
            line = raw.strip()
            if not line:
                continue
            # First line is the description ("An asterisk (*) denotes...").
            if idx == 0 and line.lower().startswith("an asterisk"):
                continue
            # Disabled services are prefixed with a literal '*'.
            if line.startswith("*"):
                continue
            out.append(line)
        return out

    def _set_system_proxy(self, host: str, port: int) -> None:
        tool = self._networksetup()
        if not tool:
            raise RuntimeError("未找到 networksetup，无法自动切换 macOS 系统代理")
        for service in self._list_services(tool):
            try:
                subprocess.run([tool, "-setwebproxy", service, host, str(port)], check=True, capture_output=True, text=True)
                subprocess.run([tool, "-setsecurewebproxy", service, host, str(port)], check=True, capture_output=True, text=True)
                subprocess.run([tool, "-setwebproxystate", service, "on"], check=True, capture_output=True, text=True)
                subprocess.run([tool, "-setsecurewebproxystate", service, "on"], check=True, capture_output=True, text=True)
            except subprocess.CalledProcessError as exc:
                add_log("WARN", f"设置代理失败 service={service}: {exc.stderr.strip() or exc}")

    def _unset_system_proxy(self) -> None:
        tool = self._networksetup()
        if not tool:
            return
        try:
            services = self._list_services(tool)
        except Exception:
            return
        for service in services:
            for state_cmd in ("-setwebproxystate", "-setsecurewebproxystate"):
                try:
                    subprocess.run([tool, state_cmd, service, "off"], check=True, capture_output=True, text=True)
                except subprocess.CalledProcessError:
                    pass

    def _wireguard_supported(self) -> bool:
        return True
