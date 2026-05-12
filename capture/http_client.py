from __future__ import annotations

from typing import Any

from db import add_log


class HttpPoster:
    """JSON poster that prefers `curl_cffi` (chrome120 impersonation) to avoid
    leaking a Python `requests` JA3 fingerprint that could be correlated with
    'a Python sniffer is running on this host'. Falls back to `requests`.
    """

    def __init__(self) -> None:
        self._impl = None
        self._warned_downgrade = False
        self._init_impl()

    def _init_impl(self) -> None:
        try:
            from curl_cffi import requests as cc_requests  # type: ignore

            self._impl = ("curl_cffi", cc_requests)
            return
        except Exception:
            pass
        try:
            import requests as py_requests

            self._impl = ("requests", py_requests)
            if not self._warned_downgrade:
                add_log("WARN", "curl_cffi 不可用，回传降级到 requests（JA3 指纹未混淆）")
                self._warned_downgrade = True
            return
        except Exception as exc:
            raise RuntimeError(f"no http client available: {exc}") from exc

    def post_json(self, url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int) -> None:
        if self._impl is None:
            self._init_impl()
        assert self._impl is not None
        kind, mod = self._impl
        if kind == "curl_cffi":
            resp = mod.post(url, json=payload, headers=headers, timeout=timeout, impersonate="chrome120")
        else:
            resp = mod.post(url, json=payload, headers=headers, timeout=timeout)
        resp.raise_for_status()
