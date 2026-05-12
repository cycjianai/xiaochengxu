from __future__ import annotations

from pathlib import Path

from db import add_log
from platform_proxy.base import AbstractProxyManager

# Constants — keep here so the module can be imported on Windows without surprises.
INTERNET_OPTION_REFRESH = 37
INTERNET_OPTION_SETTINGS_CHANGED = 39

_REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"


class WindowsProxyManager(AbstractProxyManager):
    """Windows implementation.

    Preferred mode is mitmproxy `wireguard` (requires WinTUN driver, available
    bundled with mitmproxy). On failure (driver missing / not elevated),
    falls back to `regular` HTTP proxy and switches the WinINET system proxy
    via `HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings`,
    notifying via `InternetSetOptionW(INTERNET_OPTION_SETTINGS_CHANGED)`.

    Intentionally does NOT use `netsh winhttp set proxy` — that only affects
    WinHTTP (not the WinINET path used by WeChat / Meituan apps).
    """

    def __init__(self) -> None:
        super().__init__()
        self._snapshot: dict[str, tuple[int, object]] | None = None

    def _default_mitmdump_candidates(self) -> list[Path]:
        from pathlib import Path as _P

        base = super()._default_mitmdump_candidates()
        base.extend(
            [
                _P("C:/Program Files/mitmproxy/bin/mitmdump.exe"),
                _P("C:/Program Files (x86)/mitmproxy/bin/mitmdump.exe"),
            ]
        )
        return base

    def _wireguard_supported(self) -> bool:
        try:
            import importlib

            importlib.import_module("mitmproxy.addons.wireguard")
            return True
        except Exception:
            return False

    # ------- system proxy via WinINET registry -------

    def _open_settings_key(self, write: bool):
        import winreg  # stdlib on Windows

        access = winreg.KEY_READ | (winreg.KEY_SET_VALUE if write else 0)
        return winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_KEY, 0, access)

    def _snapshot_settings(self) -> dict[str, tuple[int, object]]:
        import winreg

        snap: dict[str, tuple[int, object]] = {}
        with self._open_settings_key(write=False) as key:
            for name in ("ProxyEnable", "ProxyServer", "ProxyOverride"):
                try:
                    value, vtype = winreg.QueryValueEx(key, name)
                    snap[name] = (vtype, value)
                except FileNotFoundError:
                    snap[name] = (-1, None)  # marker: value did not exist
        return snap

    def _write_settings(self, values: dict[str, tuple[int, object]]) -> None:
        import winreg

        with self._open_settings_key(write=True) as key:
            for name, (vtype, value) in values.items():
                if vtype == -1 or value is None:
                    try:
                        winreg.DeleteValue(key, name)
                    except FileNotFoundError:
                        pass
                    continue
                winreg.SetValueEx(key, name, 0, vtype, value)

    def _notify_wininet(self) -> None:
        try:
            import ctypes

            wininet = ctypes.windll.Wininet  # type: ignore[attr-defined]
            wininet.InternetSetOptionW(0, INTERNET_OPTION_SETTINGS_CHANGED, 0, 0)
            wininet.InternetSetOptionW(0, INTERNET_OPTION_REFRESH, 0, 0)
        except Exception as exc:  # pragma: no cover - non-Windows
            add_log("WARN", f"InternetSetOptionW 通知失败: {exc}")

    def _set_system_proxy(self, host: str, port: int) -> None:
        import winreg

        if self._snapshot is None:
            self._snapshot = self._snapshot_settings()
        new_values: dict[str, tuple[int, object]] = {
            "ProxyEnable": (winreg.REG_DWORD, 1),
            "ProxyServer": (winreg.REG_SZ, f"{host}:{int(port)}"),
            "ProxyOverride": (winreg.REG_SZ, "<local>"),
        }
        self._write_settings(new_values)
        self._notify_wininet()

    def _unset_system_proxy(self) -> None:
        if self._snapshot is None:
            return
        try:
            self._write_settings(self._snapshot)
            self._notify_wininet()
        finally:
            self._snapshot = None
