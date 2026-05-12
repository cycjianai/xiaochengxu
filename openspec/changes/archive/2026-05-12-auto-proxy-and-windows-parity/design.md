## Context

`app.py` 之前是「启 Flask 线程 → 等端口 → 开 pywebview 窗口」，代理要靠 UI 上的「开始抓取」按钮手动启。`proxy_manager`（`server.py` 里的 `ProxyManager()` 实例，按平台是 `MacProxyManager` / `WindowsProxyManager`）在 `mode=regular` 下：起进程内 `DumpMaster` 监听 127.0.0.1:8899，再切系统代理（macOS `networksetup -setwebproxy`；Windows 改注册表 `HKCU\...\Internet Settings` 的 `ProxyEnable/ProxyServer/ProxyOverride` + `InternetSetOptionW` 通知）；`stop()` 还原。`cert_installer` 把 mitmproxy CA 装进系统信任（macOS `security add-trusted-cert -d` 走 osascript 提权；Windows 之前用 `win32crypt`）。pywebview 在 Windows 上用 EdgeChromium(WebView2) 后端。

## Goals / Non-Goals

**Goals:**
- 软件打开即自动抓取、关闭即自动停 + 还原系统设置；无抓取按钮；代理状态显示准确
- 失败/崩溃有兜底：自动开失败 → 清掉残留系统代理；崩溃后下次启动自愈
- Windows 下与 Mac 等价：默认 `regular` 模式、配置迁移、certutil 装证书、pywebview 依赖齐
- 明确「常开代理对 VPN/微信/办公软件无实质影响」并写进 README

**Non-Goals:**
- 不在真实 Windows 机器上端到端验证（无设备）—— 只保证代码结构与平台分支齐备
- 不改 mode 为 `local`/`wireguard`（实测 `local` 搞挂微信小程序 TLS）
- 不打包 .app/.exe（另一个变更；`build_mac.sh`/`build_win.ps1` 已就绪）
- 不做「软件被强杀后由别的进程帮忙清理系统代理」（watchdog 太重）

## Decisions

### D1：自动开代理放后台线程
`app.py::main()`：起 Flask 线程 → 等端口 → 注册清理（`atexit` + SIGTERM/SIGINT）→ 后台线程跑 `_auto_start_proxy()`（不阻塞窗口弹出；mitmproxy 启动 + 证书安装的系统授权框会自己出来）→ 开 pywebview 窗口。`_auto_start_proxy()`：`proxy_manager.start()` → 成功则 `install_cert_if_needed()`；失败则 `proxy_manager.stop()` 兜底清理 + ERROR 日志。

### D2：关闭/退出的清理 —— `_stop_proxy_once()` 幂等 + 多路触发
- `webview.start()` 返回后（用户关窗口）→ `_stop_proxy_once()`
- `atexit.register(_stop_proxy_once)` —— 正常解释器退出兜底
- `signal.signal(SIGTERM/SIGINT, lambda: (_stop_proxy_once(), sys.exit(0)))` —— 信号兜底（pywebview 跑 native loop 时 macOS 上信号可能要等 GUI 事件才处理，不可靠；保留无害）
- 非 pywebview 降级路径（webview import 失败 → `webbrowser.open` + `flask_thread.join()`）的 `finally` 里也调
- `_stopped` 标志保证只跑一次
- **强杀(SIGKILL)/系统重启来不及清理** → 接受；下次启动 `_auto_start_proxy()` 重新在 8899 起 mitmproxy，系统代理设置本来就指那儿 → 自愈。文档里告诉用户：再开一次软件就好，或手动在系统设置关代理。

### D3：UI —— 去抓取按钮，加状态徽章
- `templates/index.html`：删 `btn-start`/`btn-stop`；toolbar-left 加 `<span id="proxy-chip">代理：检查中…</span>`（点击无操作，纯显示）
- `static/app.js`：删 `startProxy`/`stopProxy`；`refreshProxyStatus()` 改为：fetch `/api/proxy/status`，更新 `#proxy-chip`（`status-chip-ok` 绿 / `status-chip-warn` 橙 + 文字「代理：抓取中/未运行」）和 `#proxy-status-text`；`DOMContentLoaded` 里 `setInterval(refreshProxyStatus, 4000)`
- `/api/proxy/start` / `/api/proxy/stop` 端点保留（被 `app.py` 的自动逻辑用 —— 实际 `app.py` 直接调 `proxy_manager.start/stop`，不走 HTTP；端点留着备用/调试）

### D4：默认 `mode=regular` + 配置迁移
- `config.py` `DEFAULT_CONFIG["capture"]["mode"]` = `"regular"`（注释说明 `local` 实测搞挂微信小程序 TLS）；`local_targets` 保留（`["WeChat","WeChatAppEx","WeApp"]`，仅 local 模式用）
- `load_config()` 迁移：`merged["capture"]["mode"]` ∈ `{local, local_only, wireguard, wireguard_only}` → 改成 `regular` 并 `save_config`
- `base.py::_start_locked` 的 `plan` dict 里 `"regular": ["regular"]`、`"local": ["local","regular"]` 等不变 —— 现在默认走 `["regular"]`

### D5：Windows 装证书改 certutil
- `cert_installer._install_windows(pem)`：`certutil -user -addstore Root <pem>` —— 当前用户的「受信任的根证书颁发机构」存储，无需管理员；首次弹一次 Windows 安全确认框（点「是」），等同 Mac 的密码框；`returncode==0` 或输出含 "already"/"已存在" → 成功
- `_is_trusted_windows()`：`certutil -user -store Root`，输出含 "mitmproxy" → True
- 不再依赖 `win32crypt`（Windows 这条路只用 `certutil` subprocess + `cryptography`(已是 mitmproxy 依赖)）；pywin32 仍在 requirements 备用（pywebview 后端可能用）

### D6：pywebview Windows 依赖
- `requirements.txt` 加 `pythonnet>=3.0; sys_platform == "win32"`（pywebview 的 EdgeChromium 后端需要 `clr`）
- WebView2 Runtime：Win11/较新 Win10 自带；否则需单独装（`build_win.ps1` 可后续带上 `MicrosoftEdgeWebview2Setup.exe`，本次只在 requirements 注释里提示）

## Windows 兼容性逐项核查（已确认 / 无障碍）

| 点 | 状态 |
|---|---|
| `platform_paths.py` 用 `%LOCALAPPDATA%\wx-sniffer\`，全程 `pathlib.Path` | OK，跨平台 |
| `platform_proxy/__init__.py` 工厂按 `sys.platform` → Windows 返回 `WindowsProxyManager` | OK |
| `WindowsProxyManager` 用 `winreg`(stdlib) 改注册表 + `ctypes.windll.Wininet.InternetSetOptionW`；`winreg`/`ctypes` 都在方法体内 import（Mac 上 import 该模块不报错） | OK |
| mitmproxy 11 `mode=regular` 进程内 `DumpMaster` + `mitmproxy_rs` 有 Windows wheel | OK（11.x 起 mitmproxy_rs 提供 win_amd64 wheel） |
| `cert_installer.py` Windows 用 `certutil`（系统自带），macOS 用 `security`（`sys.platform` 分支） | OK |
| `anti_detection.py` Windows 用 `tasklist` + `ctypes.windll.shell32.IsUserAnAdmin`，macOS 用 `ps` + `os.geteuid`（分支） | OK |
| `db.py` sqlite3 + `os.replace`（Windows 上是 `MoveFileEx`，原子） | OK |
| `app.py` `signal.SIGTERM`/`SIGINT`（Windows 上都存在；SIGTERM 处理器在 Windows 上很少触发但 `getattr` + try/except 已 guard）；`atexit`、`webbrowser` | OK |
| `capture/meituan_addon.py` `OrderedDict`/`json`/`os`/`time`/`re`、`category_cache.json` 走 `app_data_dir()` | OK |
| `server.py` Flask `app.run`、`requests.post` 带 `Host` 头、`_post_items_to_backend` | OK |
| `wx-sniffer.spec` 有 `if sys.platform.startswith("win"):` 分支（COLLECT + `contents_directory="runtime"` + hiddenimports 含 win32crypt/winreg） | OK（win32crypt 仍在 requirements 故 hiddenimport 不报错） |
| `scripts/build_win.ps1` 存在（venv + 装依赖 + pyinstaller） | OK |

## Risks / Trade-offs

- **[强杀/重启后系统代理残留 → 一时断网]** → 再开一次软件自愈；文档说明手动关法；自动开失败时主动清理。可接受。
- **[SIGTERM 处理器在 pywebview native loop 下不可靠（macOS）]** → 窗口关闭路径（`webview.start()` 返回后）已覆盖正常关闭；信号兜底无害保留。
- **[未在真实 Windows 端到端验证]** → 已逐项核查代码与平台分支，已知障碍（默认 mode / cert 装法 / pywebview 依赖）补齐；首次上 Windows 若 mitmproxy_rs WFP / WebView2 有环境问题需现场调。建议：拿一台 Win10/11，`scripts\build_win.ps1`（或直接 `python app.py`）跑一遍，确认 (a) 代理自动开、系统代理设上 (b) certutil 弹确认框装证书 (c) pywebview 窗口出来 (d) 微信小程序点商品能抓到并自动上传后台。
- **[certutil 输出编码]** → 用 `text=True, errors="ignore"`，只看 returncode + 关键子串（"already"/"已存在"/"mitmproxy"），不依赖完整文本。

## Migration Plan

1. `config.py`：默认 mode=regular + `load_config` 迁移 local→regular；smoke（`load_config()` 后 `capture.mode == 'regular'`）
2. `app.py`：`_auto_start_proxy` / `_stop_proxy_once` / atexit / 信号 / webview 返回后清理；smoke（`_auto_start_proxy()` 后系统代理 Enabled:Yes，`_stop_proxy_once()` 后 Enabled:No）
3. UI：删按钮、加 `#proxy-chip`、`refreshProxyStatus` 轮询；JS 语法检查、HTML 无 `btn-start`/`btn-stop`
4. `cert_installer.py`：Windows 改 certutil；Mac 上调 `_is_trusted_windows()` 应返回 False 不报错
5. `requirements.txt`：加 pythonnet（win only）
6. 重启 wx-sniffer 验证：5188+8899 同进程、`running:True mode:regular`、系统代理 Enabled:Yes、MITM 对美团域名生效
7. README 更新（自动开关、影响说明、Windows/Mac 一致性）
8. `openspec archive`

**回滚**：`app.py`/`config.py`/`cert_installer.py`/`templates`/`static`/`requirements.txt`/`README.md` 在文件层 revert。

## Open Questions

- 要不要 `build_win.ps1` 里带上 `MicrosoftEdgeWebview2Setup.exe`（像朋友的 Win 版那样）？本次只在 requirements 注释提示，列为打包阶段的事。
- Windows 上首次 `certutil -user -addstore Root` 的确认框文案 / 是否真的零 UAC —— 没设备验证；理论上 `-user` 目标 HKCU 不需提权。
- 软件被强杀后的「未自愈窗口期」要不要再加个 watchdog？本次不做。
