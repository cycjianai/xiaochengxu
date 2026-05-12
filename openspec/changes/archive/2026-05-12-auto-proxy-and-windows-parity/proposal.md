## Why

- 「代理状态」显示不准（页面在代理启动前就加载了），用户看不出到底开没开；
- 「开始抓取 / 停止抓取」两个按钮其实没必要 —— 运营同事用，软件打开就该自动抓、关闭就该自动停；
- 现有这套代码主要在 Mac 上跑/验证，要确认 Windows 下也能跟 Mac 一样跑：
  - `DEFAULT_CONFIG["capture"]["mode"]` 之前被改成了 `local`（mitmproxy 11 的内核级按进程重定向），但实测 `local` 模式会搞挂微信小程序的 TLS（小程序不信任 mitmproxy CA）—— 新装的 Windows 用户会直接踩坑；
  - `cert_installer.py` 的 Windows 路径用 `win32crypt.CertAddCertificateContextToStore` 太脆、依赖 pywin32 的细节，不如 `certutil -user -addstore Root` 稳（无需管理员、弹一次确认框，等同 Mac 那一下密码框）；
  - pywebview 在 Windows 上走 EdgeChromium(WebView2) 后端需要 `pythonnet`，requirements 里没声明。

## What Changes

- **wx-sniffer 自动开关代理（`app.py`）**：
  - 启动后自动 `proxy_manager.start()`（后台线程，不阻塞窗口）；首次会触发一次系统授权（macOS Authorization Services / Windows 证书确认框）装 mitmproxy CA，之后不再弹
  - 自动开后调 `install_cert_if_needed()` 确保 CA 受信任
  - 关闭软件窗口（`webview.start()` 返回）/ `atexit` / SIGTERM·SIGINT 信号 → 都调幂等的 `_stop_proxy_once()`：关代理 + 还原系统代理设置
  - 自动开代理失败（如端口被占）→ 主动 `proxy_manager.stop()` 清掉可能残留的系统代理设置，不把用户内网断网撂那儿
- **wx-sniffer UI（`templates/index.html` + `static/app.js`）**：
  - 删掉「开始抓取 / 停止抓取」按钮
  - 顶栏加「代理：抓取中 / 未运行」状态徽章（每 4s 实时刷新，绿/橙），sync-bar 的「代理状态」文字同步刷新 —— 状态准确
  - `startProxy` / `stopProxy` JS 函数删除；`refreshProxyStatus` 改成轮询 + 更新徽章和文字
- **Windows 一致性**：
  - `config.py` `DEFAULT_CONFIG["capture"]["mode"]` 从 `local` 改回 `regular`（Mac/Windows 一致；`local` 的进程名列表保留以备将来）；`load_config()` 加迁移：旧 config 里 `capture.mode` 是 `local`/`wireguard` 等 → 强制改成 `regular`
  - `cert_installer.py` 的 `_install_windows` / `_is_trusted_windows` 改用 `certutil -user -addstore Root` / `certutil -user -store Root`（不再用 win32crypt）
  - `requirements.txt` 加 `pythonnet>=3.0; sys_platform == "win32"`（pywebview Windows 后端）；pywin32 保留备用
- **README**：自动开关代理的说明、"代理一直开着会不会影响 VPN/微信/办公软件"的解释（基本不会：VPN 在路由层不走 HTTP 代理；mitmproxy 只解密美团/点评域名，其它 TLS 透传）、Windows/Mac 一致性说明

## Capabilities

### New Capabilities
- `sniffer-auto-proxy-lifecycle`：软件打开即自动开代理、关闭即自动关代理并还原系统设置、失败/崩溃的兜底与自愈、UI 只剩状态徽章的契约
- `sniffer-windows-parity`：Windows 下与 Mac 等价运行的契约（默认 regular 模式、配置迁移、certutil 装证书、pywebview WebView2 依赖）

### Modified Capabilities
- `sniffer-ui-minimal`：UI 进一步精简（去掉抓取按钮，加代理状态徽章）

## Impact

- wx-sniffer：`app.py`（自动开关代理 + 兜底清理）、`templates/index.html` + `static/app.js`（删按钮、加状态徽章）、`config.py`（默认 mode=regular + 迁移）、`cert_installer.py`（Windows 改 certutil）、`requirements.txt`（pythonnet）、`README.md`
- 后台 / 前端：不改
- 数据：不改（config.json 的 `capture.mode` 若是 `local` 会被自动改成 `regular`）
- 行为：软件常开 = 系统 HTTP 代理常开。VPN 不受影响（不同层）；微信/办公软件不受影响（非美团域名 TLS 透传）；唯一注意点是软件被强杀/系统重启来不及清理时可能留个指向 8899 的代理设置 → 再开一次软件自愈，或手动在系统设置里关掉
- Windows 验证：本变更把已知的 Windows 障碍（默认 mode、cert 装法、pywebview 依赖）都补齐了；但**未在真实 Windows 机器上端到端跑过** —— 代码结构齐备，首次在 Windows 上跑时若 mitmproxy_rs / pywebview / WebView2 有环境问题需现场调
