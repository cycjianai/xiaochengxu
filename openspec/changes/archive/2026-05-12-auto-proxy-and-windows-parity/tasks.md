## 1. 自动开关代理（app.py）

- [x] 1.1 `_auto_start_proxy()`：后台线程跑 `proxy_manager.start()`；成功 → `install_cert_if_needed()`；失败 → `proxy_manager.stop()` 兜底清理 + ERROR 日志
- [x] 1.2 `_stop_proxy_once()`：幂等（`_stopped` 标志），`proxy_manager.stop()` + 日志
- [x] 1.3 `main()`：起 Flask 线程 → 等端口 → `atexit.register(_stop_proxy_once)` + SIGTERM/SIGINT 信号（`getattr` + try/except guard）→ 后台线程 `_auto_start_proxy()` → 开 pywebview 窗口 → `webview.start()` 返回后 `_stop_proxy_once()`；降级浏览器路径的 `finally` 里也调
- [x] 1.4 smoke：`_auto_start_proxy()` 后系统代理 Enabled:Yes、proxy_manager 状态 running:True；`_stop_proxy_once()` 后系统代理 Enabled:No

## 2. UI 精简（templates/index.html + static/app.js）

- [x] 2.1 `index.html`：删 `btn-start`/`btn-stop`；toolbar-left 加 `<span id="proxy-chip">`；sync-bar 文字改成「软件打开即自动抓取…关闭软件自动停止」
- [x] 2.2 `app.js`：删 `startProxy`/`stopProxy`；`refreshProxyStatus()` 改为更新 `#proxy-chip`（ok/warn class + 「代理：抓取中/未运行」）和 `#proxy-status-text`；`DOMContentLoaded` 里 `setInterval(refreshProxyStatus, 4000)`
- [x] 2.3 JS 语法检查（`node -c`）、HTML 里无 `btn-start`/`btn-stop`、有 `id="proxy-chip"`

## 3. Windows 一致性

- [x] 3.1 `config.py`：`DEFAULT_CONFIG["capture"]["mode"]` `local` → `regular`（注释说明）；`local_targets` 保留 `["WeChat","WeChatAppEx","WeApp"]`
- [x] 3.2 `config.py::load_config()`：迁移 `capture.mode` ∈ {local,local_only,wireguard,wireguard_only} → `regular` 并 `save_config`
- [x] 3.3 `cert_installer.py`：`_install_windows` 改 `certutil -user -addstore Root <pem>`（无管理员，弹确认框；rc==0 或含 "already"/"已存在" → 成功）；`_is_trusted_windows` 改 `certutil -user -store Root` 输出含 "mitmproxy" → True；不再用 `win32crypt`
- [x] 3.4 `requirements.txt`：加 `pythonnet>=3.0; sys_platform == "win32"`（pywebview EdgeChromium 后端）；pywin32 保留备用；注释提示 WebView2 Runtime
- [x] 3.5 逐项核查 Windows 兼容性（platform_paths / platform_proxy 工厂 / WindowsProxyManager 的 winreg+ctypes 在方法体内 / mitmproxy_rs 有 win wheel / anti_detection 的 tasklist+IsUserAnAdmin / db.py os.replace / app.py 信号 / capture addon / server / wx-sniffer.spec 的 win 分支 / build_win.ps1）—— 见 design.md 表格
- [x] 3.6 smoke：`config.load_config()` 后 `capture.mode == 'regular'`；`import platform_proxy.windows` 在 Mac 上不报错；Mac 上调 `_is_trusted_windows()` 返回 False 不崩

## 4. 收尾

- [x] 4.1 重启 wx-sniffer 验证：5188+8899 同进程、`running:True mode:regular`、系统代理 Enabled:Yes、MITM 对美团域名 issuer=mitmproxy
- [x] 4.2 `README.md`：自动开关代理说明、"代理一直开着会不会影响 VPN/微信/办公软件"解释、强杀/重启的自愈与手动关法、Windows/Mac 一致性
- [x] 4.3 `openspec archive auto-proxy-and-windows-parity`

## 5. 待真实 Windows 端到端验证（无设备，未做）

- [ ] 5.1 在 Win10/11 上 `pip install -r requirements.txt` + `python app.py`（或 `scripts\build_win.ps1` 出 exe）→ 确认：代理自动开、注册表 Internet Settings 设上、certutil 弹确认框装证书、pywebview/WebView2 窗口出来、微信小程序点商品能抓到并自动上传后台、关软件后注册表还原
