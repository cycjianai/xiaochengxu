from __future__ import annotations

import asyncio
import errno
import shutil
import socket
import threading
import time
from pathlib import Path
from typing import Any

from config import BASE_DIR, load_config
from db import add_log
from platform_paths import mitmproxy_dir


class AbstractProxyManager:
    """Base class for platform-specific proxy managers.

    Subclasses MUST implement `_set_system_proxy`, `_unset_system_proxy`,
    and MAY override `_default_mitmdump_candidates` and `start()` for
    platform-specific startup flow.

    mitmproxy itself is run in-process via DumpMaster on a background asyncio
    thread; subclasses do not spawn `mitmdump` subprocesses.
    """

    MAX_RESTART = 3
    RESTART_WINDOW_SECONDS = 5.0

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._master = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._mode: str = ""
        self._running = False
        self._last_message: str = ""
        self._restart_attempts: list[float] = []

    # ------- platform hooks -------

    def _default_mitmdump_candidates(self) -> list[Path]:
        return [
            BASE_DIR / ".venv-build" / "bin" / "mitmdump",
            BASE_DIR / ".venv" / "bin" / "mitmdump",
        ]

    def _set_system_proxy(self, host: str, port: int) -> None:
        return None

    def _unset_system_proxy(self) -> None:
        return None

    def _wireguard_supported(self) -> bool:
        return True

    # ------- public API -------

    def _mitmdump_path(self) -> str | None:
        cfg = load_config()["capture"]
        configured_path = (cfg.get("mitmdump_path") or "").strip()
        if configured_path and Path(configured_path).is_file():
            return configured_path
        which_result = shutil.which("mitmdump")
        if which_result:
            return which_result
        for candidate in self._default_mitmdump_candidates():
            if candidate.is_file():
                return str(candidate)
        return None

    def status(self) -> dict:
        cfg = load_config()["capture"]
        target_hosts = cfg.get("target_hosts") or []
        running = self._running and self._thread is not None and self._thread.is_alive()
        return {
            "running": running,
            "mode": self._mode or (cfg.get("mode") or "wireguard"),
            "listen_host": cfg["listen_host"],
            "listen_port": cfg["listen_port"],
            "target_hosts": target_hosts,
            "mitmdump_found": bool(self._mitmdump_path()),
            "mitmdump_path": self._mitmdump_path() or "",
            "message": self._last_message or ("代理运行中" if running else "代理未启动"),
        }

    def start(self) -> dict:
        with self._lock:
            if self._running and self._thread and self._thread.is_alive():
                return {"success": True, "running": True, "message": "代理已在运行"}
            return self._start_locked()

    def wireguard_client_conf(self) -> str | None:
        """Return the INI-format WireGuard client configuration users can
        import into the WireGuard.app, or None if not running in wireguard
        mode yet.
        """
        master = self._master
        if master is None:
            return None
        try:
            proxyserver = master.addons.get("proxyserver")
        except Exception:
            return None
        if proxyserver is None:
            return None
        for server in getattr(proxyserver, "servers", []):
            client_conf = getattr(server, "client_conf", None)
            if callable(client_conf):
                conf = client_conf()
                if conf:
                    return conf
        return None

    def stop(self) -> dict:
        with self._lock:
            self._stop_locked()
            self._unset_system_proxy()
            self._mode = ""
            self._last_message = "代理已停止"
            add_log("INFO", "代理已停止")
            return {"success": True, "running": False, "message": "代理已停止"}

    # ------- internals -------

    def _start_locked(self) -> dict:
        cfg = load_config()
        capture = cfg["capture"]
        preferred_mode = (capture.get("mode") or "local").lower()

        # Preferred order: local (transparent per-process, no client setup) →
        # regular (HTTP proxy + system proxy switch — fallback only).
        plan = {
            "local":         ["local", "regular"],
            "auto":          ["local", "regular"],
            "local_only":    ["local"],
            "regular":       ["regular"],
            "regular_only":  ["regular"],
            "wireguard":     ["wireguard"],
        }
        modes_to_try = list(plan.get(preferred_mode, ["local", "regular"]))
        # dedupe while preserving order
        seen = set()
        ordered: list[str] = []
        for m in modes_to_try:
            if m in seen:
                continue
            seen.add(m)
            ordered.append(m)

        last_error: str = ""
        primary_mode = ordered[0] if ordered else "regular"
        for mode in ordered:
            ok, msg = self._launch_master(mode)
            if ok:
                self._mode = mode
                downgraded = mode != primary_mode
                if mode == "regular":
                    try:
                        self._set_system_proxy(capture["listen_host"], int(capture["listen_port"]))
                    except Exception as exc:  # pragma: no cover - platform dependent
                        add_log("WARN", f"设置系统代理失败: {exc}")
                self._running = True
                if downgraded:
                    self._last_message = f"已降级到 {mode} 模式（首选 {primary_mode} 失败）：{msg}"
                else:
                    self._last_message = msg
                add_log("INFO", f"代理已启动 mode={mode}")
                return {
                    "success": True,
                    "running": True,
                    "message": self._last_message,
                    "mode": mode,
                    "downgraded": downgraded,
                }
            last_error = msg
            add_log("WARN", f"模式 {mode} 启动失败: {msg}")

        self._running = False
        self._last_message = f"代理启动失败: {last_error}"
        add_log("ERROR", self._last_message)
        return {"success": False, "running": False, "message": self._last_message}

    def _launch_master(self, mode: str) -> tuple[bool, str]:
        preflight_error = self._preflight_listen(mode)
        if preflight_error:
            return False, preflight_error
        try:
            self._spawn_master_thread(mode)
        except Exception as exc:
            return False, str(exc)
        # Give the master a moment to surface startup errors.
        for _ in range(20):
            time.sleep(0.1)
            if self._thread is None or not self._thread.is_alive():
                return False, "master 线程在启动后立即退出"
            if self._master is not None:
                break
        return True, f"已启动 mode={mode}"

    def _preflight_listen(self, mode: str) -> str | None:
        if mode not in {"regular", "wireguard"}:
            return None
        cfg = load_config()["capture"]
        host = str(cfg["listen_host"])
        port = int(cfg["listen_port"])
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM, flags=socket.AI_PASSIVE)
        except OSError as exc:
            return f"解析监听地址失败 {host}:{port}: {exc}"

        last_error: OSError | None = None
        for family, socktype, proto, _canonname, sockaddr in infos:
            sock = None
            try:
                sock = socket.socket(family, socktype, proto)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(sockaddr)
                return None
            except OSError as exc:
                last_error = exc
            finally:
                if sock is not None:
                    sock.close()

        if last_error is None:
            return f"无法监听 {host}:{port}"
        if last_error.errno == errno.EADDRINUSE:
            return f"{host}:{port} 端口已被占用，请关闭占用进程或修改 capture.listen_port"
        return f"无法监听 {host}:{port}: {last_error}"

    def _build_options(self, mode: str):
        from mitmproxy.options import Options

        cfg = load_config()["capture"]
        listen_host = cfg["listen_host"]
        listen_port = int(cfg["listen_port"])
        target_hosts = cfg.get("target_hosts") or []
        confdir = str(mitmproxy_dir())

        opts = Options(confdir=confdir)

        if mode == "local":
            targets = cfg.get("local_targets") or ["WeChat", "WeChatAppEx"]
            spec = ",".join(t.strip() for t in targets if t and t.strip())
            opts.update(mode=[f"local:{spec}"] if spec else ["local"])
        elif mode == "wireguard":
            opts.update(mode=["wireguard"], listen_host=listen_host, listen_port=listen_port)
        else:  # regular
            opts.update(mode=["regular"], listen_host=listen_host, listen_port=listen_port)

        # Host filter: also constrain by domain so even if a target process
        # talks to a non-Meituan host (e.g. WeChat itself), we don't bother
        # intercepting / decrypting it. Adds defense-in-depth.
        # mitmproxy matches allow_hosts against `host:port`, so the trailing
        # `$` we used before never matched (because of the `:443` suffix).
        # Use word boundaries instead.
        allow_patterns: list[str] = []
        for host in target_hosts:
            host = (host or "").strip()
            if not host:
                continue
            if host.startswith("*."):
                allow_patterns.append(r"(^|\.)" + host[2:].replace(".", r"\.") + r"(:\d+)?$")
            else:
                allow_patterns.append(r"^" + host.replace(".", r"\.") + r"(:\d+)?$")
        if allow_patterns:
            opts.update(allow_hosts=allow_patterns)
        return opts

    def _build_addons(self) -> list:
        from capture.meituan_addon import MeituanCaptureAddon

        cfg = load_config()
        capture = cfg["capture"]
        app_cfg = cfg["app"]
        import_url = f"http://{app_cfg['host']}:{app_cfg['port']}{capture['import_path']}"
        return [MeituanCaptureAddon(import_url=import_url, capture_token=capture["capture_token"])]

    def _spawn_master_thread(self, mode: str) -> None:
        from mitmproxy.tools.dump import DumpMaster

        ready = threading.Event()
        startup_error: dict[str, str] = {}

        def _runner() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            opts = self._build_options(mode)
            addons = self._build_addons()
            try:
                # mitmproxy 11 accepts `loop=` directly, so we don't have to
                # wrap construction in a coroutine like 9.x.
                master = DumpMaster(opts, loop=loop, with_termlog=False, with_dumper=False)
                for addon in addons:
                    master.addons.add(addon)
                self._master = master
                ready.set()
                loop.run_until_complete(master.run())
            except Exception as exc:
                startup_error["msg"] = str(exc)
                ready.set()
                add_log("ERROR", f"mitmproxy 线程异常: {exc}")
                self._maybe_restart(mode)
            finally:
                try:
                    loop.close()
                except Exception:
                    pass
                self._loop = None
                self._master = None

        t = threading.Thread(target=_runner, name=f"mitmproxy-{mode}", daemon=True)
        self._thread = t
        t.start()
        ready.wait(timeout=4.0)
        if startup_error.get("msg"):
            raise RuntimeError(startup_error["msg"])

    def _maybe_restart(self, mode: str) -> None:
        now = time.time()
        self._restart_attempts = [t for t in self._restart_attempts if now - t < self.RESTART_WINDOW_SECONDS]
        if len(self._restart_attempts) >= self.MAX_RESTART:
            self._running = False
            self._last_message = f"mitmproxy 线程崩溃且 {self.RESTART_WINDOW_SECONDS}s 内重启 {self.MAX_RESTART} 次仍失败"
            add_log("ERROR", self._last_message)
            return
        self._restart_attempts.append(now)
        time.sleep(0.5)
        try:
            self._spawn_master_thread(mode)
            add_log("INFO", f"mitmproxy 自动重启成功 mode={mode}")
        except Exception as exc:
            add_log("ERROR", f"mitmproxy 自动重启失败: {exc}")

    def _stop_locked(self) -> None:
        master = self._master
        loop = self._loop
        if master is not None and loop is not None:
            try:
                loop.call_soon_threadsafe(master.shutdown)
            except Exception:
                pass
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=4.0)
        self._master = None
        self._loop = None
        self._thread = None
        self._running = False
