from __future__ import annotations

import sys


def _make() -> "AbstractProxyManager":
    if sys.platform == "darwin":
        from platform_proxy.macos import MacProxyManager

        return MacProxyManager()
    if sys.platform.startswith("win"):
        from platform_proxy.windows import WindowsProxyManager

        return WindowsProxyManager()
    from platform_proxy.base import AbstractProxyManager

    return AbstractProxyManager()


class ProxyManager:
    """Cross-platform facade. Instantiation returns the platform-specific impl."""

    def __new__(cls):  # noqa: D401
        return _make()


from platform_proxy.base import AbstractProxyManager  # noqa: E402  re-export

__all__ = ["ProxyManager", "AbstractProxyManager"]
