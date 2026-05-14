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


def _load_cert(pem_path: Path):
    from cryptography import x509  # mitmproxy depends on cryptography, so this is available
    from cryptography.hazmat.backends import default_backend

    data = pem_path.read_bytes()
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    try:
        return x509.load_pem_x509_certificate(data, default_backend())
    except ValueError:
        return x509.load_der_x509_certificate(data, default_backend())


def _read_cert_not_after(pem_path: Path) -> datetime.datetime | None:
    if not pem_path.exists():
        return None
    try:
        cert = _load_cert(pem_path)
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
        from cryptography.hazmat.primitives import hashes

        cert = _load_cert(pem_path)
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


def _windows_cert_thumbprint(pem_path: Path) -> str | None:  # pragma: no cover - windows only
    try:
        from cryptography.hazmat.primitives import hashes

        cert = _load_cert(pem_path)
        return cert.fingerprint(hashes.SHA1()).hex().upper()
    except Exception as exc:
        add_log("WARN", f"计算 mitmproxy 证书指纹失败: {exc}")
        return None


def _iter_windows_root_thumbprints():  # pragma: no cover - windows only
    import ssl
    from cryptography import x509
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import hashes

    for cert_bytes, encoding, _trust in ssl.enum_certificates("ROOT"):
        if encoding != "x509_asn":
            continue
        try:
            cert = x509.load_der_x509_certificate(cert_bytes, default_backend())
            yield cert.fingerprint(hashes.SHA1()).hex().upper()
        except Exception:
            continue


def _is_trusted_windows(pem_path: Path) -> bool:  # pragma: no cover - windows only
    """查当前用户可见的 ROOT 证书存储里有没有当前 mitmproxy CA。"""
    thumbprint = _windows_cert_thumbprint(pem_path)
    if not thumbprint:
        return False
    try:
        return any(existing == thumbprint for existing in _iter_windows_root_thumbprints())
    except Exception:
        return False


def _install_windows_via_crypt32(pem_path: Path) -> bool:  # pragma: no cover - windows only
    try:
        import ctypes
        from cryptography.hazmat.primitives import serialization

        cert = _load_cert(pem_path)
        der_bytes = cert.public_bytes(serialization.Encoding.DER)
    except Exception as exc:
        add_log("WARN", f"准备 Windows 证书导入数据失败: {exc}")
        return False

    X509_ASN_ENCODING = 0x00000001
    PKCS_7_ASN_ENCODING = 0x00010000
    CERT_STORE_ADD_USE_EXISTING = 2

    try:
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        crypt32.CertOpenSystemStoreW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        crypt32.CertOpenSystemStoreW.restype = ctypes.c_void_p
        crypt32.CertAddEncodedCertificateToStore.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
        ]
        crypt32.CertAddEncodedCertificateToStore.restype = ctypes.c_bool
        crypt32.CertCloseStore.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        crypt32.CertCloseStore.restype = ctypes.c_bool

        store = crypt32.CertOpenSystemStoreW(None, "ROOT")
        if not store:
            add_log("WARN", f"打开 Windows ROOT 证书存储失败: {ctypes.WinError(ctypes.get_last_error())}")
            return False

        try:
            encoded = ctypes.create_string_buffer(der_bytes)
            ok = crypt32.CertAddEncodedCertificateToStore(
                store,
                X509_ASN_ENCODING | PKCS_7_ASN_ENCODING,
                ctypes.cast(encoded, ctypes.c_void_p),
                len(der_bytes),
                CERT_STORE_ADD_USE_EXISTING,
                None,
            )
            if not ok:
                add_log("WARN", f"写入 Windows ROOT 证书存储失败: {ctypes.WinError(ctypes.get_last_error())}")
                return False
            return True
        finally:
            crypt32.CertCloseStore(store, 0)
    except Exception as exc:
        add_log("WARN", f"Windows crypt32 安装 mitmproxy 证书失败: {exc}")
        return False


def _install_windows(pem_path: Path) -> bool:  # pragma: no cover - windows only
    """静默导入当前用户 ROOT 证书存储，不弹 PowerShell/certutil 窗口。"""
    if not pem_path.exists():
        return False
    if _install_windows_via_crypt32(pem_path):
        return _is_trusted_windows(pem_path)
    return False


def is_cert_trusted() -> bool:
    pem = _mitm_cert_path()
    if sys.platform == "darwin":
        return _is_trusted_macos(pem)
    if sys.platform.startswith("win"):
        return _is_trusted_windows(pem)
    return pem.exists()


def install_cert_if_needed() -> dict:
    pem = _mitm_cert_path()
    result = {"trusted": False, "installed": False, "rotated": False}
    if pem.exists() and sys.platform == "darwin":
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
        if not _is_trusted_windows(pem):
            result["installed"] = _install_windows(pem)
        result["trusted"] = _is_trusted_windows(pem)
    else:
        result["trusted"] = pem.exists()
    return result
