from __future__ import annotations

import atexit
import json
import os
import signal
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser

from config import APP_TITLE, load_config
from db import add_log, init_db
from platform_paths import app_data_dir
from server import proxy_manager, run_server


def _wait_for_port(host: str, port: int, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False


_stopped = False
_instance_lock_handle = None


def _stop_proxy_once() -> None:
    """关代理 + 还原系统代理。幂等 —— atexit / 信号 / 窗口关闭都可能调到。"""
    global _stopped
    if _stopped:
        return
    _stopped = True
    try:
        proxy_manager.stop()
        add_log("INFO", "退出：已关闭代理并还原系统代理设置")
    except Exception as exc:  # pragma: no cover - best effort
        add_log("WARN", f"退出时关代理失败: {exc}")


def _show_info_dialog(title: str, message: str) -> None:
    if sys.platform.startswith("win"):
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, message, title, 0x40)
            return
        except Exception:
            pass
    print(f"{title}: {message}")


def _acquire_instance_lock() -> bool:
    global _instance_lock_handle
    if _instance_lock_handle is not None:
        return True

    app_data_dir().mkdir(parents=True, exist_ok=True)
    lock_path = app_data_dir() / "instance.lock"
    handle = lock_path.open("a+b")
    try:
        if sys.platform.startswith("win"):
            import msvcrt

            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return False

    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()).encode("utf-8"))
    handle.flush()
    _instance_lock_handle = handle
    return True


def _release_instance_lock() -> None:
    global _instance_lock_handle
    handle = _instance_lock_handle
    if handle is None:
        return
    try:
        if sys.platform.startswith("win"):
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        handle.close()
    except Exception:
        pass
    _instance_lock_handle = None


def _probe_existing_instance(url: str) -> dict | None:
    req = urllib.request.Request(f"{url}/api/proxy/status", headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=1.0) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError):
        return None
    if not isinstance(payload, dict):
        return None
    if "running" not in payload or "message" not in payload:
        return None
    return payload


def _auto_start_proxy() -> None:
    """开软件即自动开代理；首次会弹一次 Mac 密码框装 mitmproxy 根证书（之后不再弹）。"""
    try:
        result = proxy_manager.start()
        if result.get("running"):
            add_log("INFO", f"自动开启代理：{result.get('message') or 'mode=' + str(result.get('mode'))}")
            try:
                from cert_installer import install_cert_if_needed
                install_cert_if_needed()
            except Exception as exc:
                add_log("WARN", f"证书安装/检查失败: {exc}")
        else:
            add_log("ERROR", f"自动开启代理失败: {result.get('message')} —— 已清理可能残留的系统代理设置")
            # 上次崩溃可能留了个指向 127.0.0.1:8899 的系统代理，而 mitmproxy 又起不来，
            # 那样会断网 —— 这里兜底把系统代理还原掉。
            try:
                proxy_manager.stop()
            except Exception:
                pass
    except Exception as exc:
        add_log("ERROR", f"自动开启代理异常: {exc} —— 已清理可能残留的系统代理设置")
        try:
            proxy_manager.stop()
        except Exception:
            pass


def main() -> None:
    cfg = load_config()["app"]
    url = f"http://{cfg['host']}:{cfg['port']}"
    if not _acquire_instance_lock():
        existing = _probe_existing_instance(url)
        message = "检测到 wx-sniffer 已在运行，本次不再重复启动。"
        if existing is not None:
            message += f"\n当前状态：{existing.get('message') or ('mode=' + str(existing.get('mode') or 'unknown'))}"
        _show_info_dialog(APP_TITLE, message)
        return

    atexit.register(_release_instance_lock)
    init_db()
    add_log("INFO", "wx-sniffer 启动")
    flask_thread = threading.Thread(target=run_server, daemon=True)
    flask_thread.start()

    if not _wait_for_port(cfg["host"], int(cfg["port"]), timeout=10.0):
        add_log("WARN", "Flask 端口未就绪，仍尝试继续")

    # 关软件自动关代理：atexit + 信号兜底（窗口关闭后 webview.start() 返回也会调）
    atexit.register(_stop_proxy_once)
    for _sig in (getattr(signal, "SIGTERM", None), getattr(signal, "SIGINT", None)):
        if _sig is not None:
            try:
                signal.signal(_sig, lambda *_a: (_stop_proxy_once(), sys.exit(0)))
            except (ValueError, OSError):
                pass  # 非主线程 / 不支持时忽略

    # 开软件自动开代理（在后台线程跑，不阻塞窗口弹出；证书弹窗会自己出来）
    threading.Thread(target=_auto_start_proxy, name="auto-start-proxy", daemon=True).start()

    try:
        import webview

        webview.create_window(
            cfg.get("window_title") or APP_TITLE,
            url,
            width=int(cfg.get("window_width") or 1400),
            height=int(cfg.get("window_height") or 920),
            min_size=(1200, 760),
        )
        webview.start()
        # 窗口关闭后到这里 —— 关代理
        _stop_proxy_once()
    except Exception as exc:
        add_log("WARN", f"pywebview 启动失败，已降级浏览器打开: {exc}")
        webbrowser.open(url)
        try:
            flask_thread.join()
        finally:
            _stop_proxy_once()


if __name__ == "__main__":
    main()
