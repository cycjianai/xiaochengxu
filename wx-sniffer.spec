# -*- mode: python ; coding: utf-8 -*-

import os
import sys
from pathlib import Path

project_dir = Path(SPECPATH)
block_cipher = None

# Product name is overridable to reduce static signature overlap with the
# public "wx-sniffer" / PyInstaller defaults.
PRODUCT_NAME = os.environ.get("WX_SNIFFER_PRODUCT_NAME", "MTCenter")
BUNDLE_ID = os.environ.get("WX_SNIFFER_BUNDLE_ID", "com.yuandian.mtcenter")

datas = [
    (str(project_dir / "templates"), "templates"),
    (str(project_dir / "static"), "static"),
]

hiddenimports = [
    "webview",
    "requests",
    "mitmproxy",
    "mitmproxy.tools.dump",
    "mitmproxy.addons.proxyserver",
    "mitmproxy.addons.tlsconfig",
    "mitmproxy.addons.wireguard",
    "mitmproxy.options",
    "capture",
    "capture.meituan_addon",
    "capture.http_client",
    "platform_proxy",
    "platform_proxy.base",
    "platform_proxy.macos",
    "platform_proxy.windows",
    "platform_paths",
    "anti_detection",
    "cert_installer",
]

if sys.platform.startswith("win"):
    hiddenimports.extend([
        "win32crypt",
        "win32api",
        "winreg",
    ])

try:
    import curl_cffi  # noqa: F401
    hiddenimports.append("curl_cffi")
    hiddenimports.append("curl_cffi.requests")
except Exception:
    pass


a = Analysis(
    ["app.py"],
    pathex=[str(project_dir)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

if sys.platform == "darwin":
    # macOS: produce single-file EXE + .app bundle. Default `_internal` layout
    # is not used because BUNDLE handles resources via the bundle structure.
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.zipfiles,
        a.datas,
        [],
        name=PRODUCT_NAME,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )
    app = BUNDLE(
        exe,
        name=f"{PRODUCT_NAME}.app",
        icon=None,
        bundle_identifier=BUNDLE_ID,
        info_plist={
            "CFBundleName": PRODUCT_NAME,
            "CFBundleDisplayName": PRODUCT_NAME,
            "CFBundleShortVersionString": "0.2.0",
            "CFBundleVersion": "0.2.0",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "12.0",
        },
    )
else:
    # Windows / Linux: produce one-dir layout so resources live alongside the
    # exe. The contents directory is renamed from `_internal` to `runtime` to
    # blunt the PyInstaller default-signature surface.
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name=PRODUCT_NAME,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.zipfiles,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name=PRODUCT_NAME,
        contents_directory="runtime",
    )
