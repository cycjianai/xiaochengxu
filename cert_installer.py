from __future__ import annotations

import datetime
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from db import add_log
from platform_paths import mitmproxy_dir

CERT_RENEW_THRESHOLD_DAYS = 30


def _mitm_cert_path() -> Path:
    return mitmproxy_dir() / "mitmproxy-ca-cert.pem"


def _read_cert_not_after(pem_path: Path) -> datetime.datetime | None:
    if not pem_path.exists():
        return None
    try:
        from cryptography import x509  # mitmproxy depends on cryptography, so this is available
        from cryptography.hazmat.backends import default_backend

        data = pem_path.read_bytes()
        cert = x509.load_pem_x509_certificate(data, default_backend())
        return cert.not_valid_after
    except Exception as exc:
        add_log("WARN", f"读取 mitmproxy 根证书失败: {exc}")
        return None


def _regenerate_cert() -> bool:
    """Delete the existing mitmproxy CA so mitmproxy regenerates on next start."""
    cert_dir = mitmproxy_dir()
    removed = False
    for name in (
        "mitmproxy-ca.pem",
        "mitmproxy-ca-cert.pem",
        "mitmproxy-ca-cert.cer",
        "mitmproxy-ca-cert.p12",
        "mitmproxy-dhparam.pem",
    ):
        p = cert_dir / name
        if p.exists():
            try:
                p.unlink()
                removed = True
            except Exception as exc:
                add_log("WARN", f"删除旧证书失败 {p}: {exc}")
    return removed


def _is_trusted_macos(pem_path: Path) -> bool:
    """True iff this CA appears in the admin-domain trust settings.

    macOS WeChat (and any Chromium-based app) only trusts admin-domain roots
    — user-domain trust is invisible to them. We compare the cert's SHA1
    fingerprint against the keys in the exported admin trust plist (plist
    uses the SHA1 fingerprint as the dict key, so it survives the base64
    line-wrap that breaks substring search on the issuer name).
    """
    if not pem_path.exists():
        return False
    sec = shutil.which("security")
    if not sec:
        return False
    try:
        # Compute SHA1 of the cert (matches the plist key format)
        from cryptography import x509
        from cryptography.hazmat.backends import default_backend
        from cryptography.hazmat.primitives import hashes

        cert = x509.load_pem_x509_certificate(pem_path.read_bytes(), default_backend())
        sha1_hex = cert.fingerprint(hashes.SHA1()).hex().upper()

        with tempfile.NamedTemporaryFile(suffix=".plist", delete=False) as tf:
            out_path = tf.name
        result = subprocess.run(
            [sec, "trust-settings-export", "-d", out_path],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return False
        blob = Path(out_path).read_text(errors="ignore")
        return sha1_hex in blob
    except Exception:
        return False
    finally:
        try:
            Path(out_path).unlink()
        except Exception:
            pass


def _install_macos(pem_path: Path) -> bool:
    """1:1 with the friend's wx-sniffer.exe install recipe:

        security import -k login.keychain-db -t cert -f pemseq <pem>
        security add-trusted-cert -d -r trustRoot -p ssl <pem>

    The second command needs admin auth. When invoked from a GUI session
    (i.e. the user double-clicked the .app), `osascript ... with
    administrator privileges` will pop the native macOS password dialog and
    persist the trust. When run from a non-GUI context (CI, headless ssh),
    auth fails silently — we then return False and the caller surfaces a
    one-line instruction to the user.
    """
    if not pem_path.exists():
        return False
    sec = shutil.which("security")
    if not sec:
        return False

    keychain = str(Path.home() / "Library" / "Keychains" / "login.keychain-db")
    # Step 1: import into login keychain (idempotent, no auth needed)
    try:
        subprocess.run(
            [sec, "import", str(pem_path), "-k", keychain, "-t", "cert", "-f", "pemseq"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        pass  # already imported is fine

    # Step 2: admin-domain trust (needs auth). osascript gets the user a
    # native password dialog. This is the same UX as the friend's exe
    # triggering Windows UAC on first run.
    quoted_path = str(pem_path).replace('"', '\\"')
    osa_cmd = (
        f'do shell script "{sec} add-trusted-cert -d -r trustRoot -p ssl '
        f'\\"{quoted_path}\\"" with administrator privileges'
    )
    try:
        result = subprocess.run(
            ["osascript", "-e", osa_cmd],
            capture_output=True,
            text=True,
            timeout=120,  # user has 2 minutes to enter password
        )
        if result.returncode == 0:
            return True
        add_log("WARN", f"管理员授权失败: {result.stderr.strip() or result.stdout.strip()}")
    except Exception as exc:
        add_log("WARN", f"无法弹出管理员授权对话框: {exc}")
    return False


def _is_trusted_windows() -> bool:  # pragma: no cover - windows only
    """查当前用户的「受信任的根证书颁发机构」存储里有没有 mitmproxy CA。"""
    cu = shutil.which("certutil")
    if not cu:
        return False
    try:
        r = subprocess.run(
            [cu, "-user", "-store", "Root"],
            capture_output=True, text=True, errors="ignore", timeout=15,
        )
        return r.returncode == 0 and "mitmproxy" in ((r.stdout or "") + (r.stderr or "")).lower()
    except Exception:
        return False


def _install_windows(pem_path: Path) -> bool:  # pragma: no cover - windows only
    """certutil -user -addstore Root <pem> —— 装进当前用户的「受信任的根证书颁发机构」。
    无需管理员，会弹一次 Windows 安全确认框（点「是」），等同 Mac 上那一下密码框；
    点完之后 `is_cert_trusted()` 返回 True，不会再弹。"""
    if not pem_path.exists():
        return False
    cu = shutil.which("certutil")
    if not cu:
        add_log("WARN", "未找到 certutil，无法自动安装证书；请手动把 mitmproxy 根证书装进「受信任的根证书颁发机构」")
        return False
    try:
        r = subprocess.run(
            [cu, "-user", "-addstore", "Root", str(pem_path)],
            capture_output=True, text=True, errors="ignore", timeout=120,
        )
        out = ((r.stdout or "") + (r.stderr or "")).lower()
        if r.returncode == 0 or "already" in out or "已存在" in ((r.stdout or "") + (r.stderr or "")):
            return True
        add_log("WARN", f"certutil 安装证书失败(rc={r.returncode}): {((r.stderr or r.stdout) or '').strip()[:200]}")
        return False
    except Exception as exc:
        add_log("WARN", f"Windows 安装 mitmproxy 证书失败: {exc}")
        return False


def is_cert_trusted() -> bool:
    pem = _mitm_cert_path()
    if sys.platform == "darwin":
        return _is_trusted_macos(pem)
    if sys.platform.startswith("win"):
        return _is_trusted_windows()
    return pem.exists()


def install_cert_if_needed() -> dict:
    pem = _mitm_cert_path()
    result = {"trusted": False, "installed": False, "rotated": False}
    if pem.exists():
        not_after = _read_cert_not_after(pem)
        if not_after is not None:
            days_left = (not_after - datetime.datetime.utcnow()).days
            if days_left < CERT_RENEW_THRESHOLD_DAYS:
                if _regenerate_cert():
                    add_log("INFO", f"mitmproxy 根证书剩余 {days_left} 天，已删除以触发轮换")
                    result["rotated"] = True
                    pem = _mitm_cert_path()  # path same, file will be recreated by mitmproxy
    # If pem doesn't exist yet (first run), mitmproxy generates it on first start;
    # the caller is expected to invoke this again after mitmproxy startup.
    if not pem.exists():
        return result
    if sys.platform == "darwin":
        if not _is_trusted_macos(pem):
            result["installed"] = _install_macos(pem)
        result["trusted"] = _is_trusted_macos(pem)
    elif sys.platform.startswith("win"):
        if not _is_trusted_windows():
            result["installed"] = _install_windows(pem)
        result["trusted"] = _is_trusted_windows()
    else:
        result["trusted"] = pem.exists()
    return result
