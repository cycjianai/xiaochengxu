# sniffer-auto-proxy-lifecycle Specification

## Purpose
TBD - created by archiving change auto-proxy-and-windows-parity. Update Purpose after archive.
## Requirements
### Requirement: 软件打开即自动开代理
wx-sniffer 启动后 SHALL 自动开启代理（`proxy_manager.start()`），不需要用户点任何按钮。自动开启 SHALL 在后台线程进行，不阻塞主窗口弹出。开启成功后 SHALL 调用 `install_cert_if_needed()` 确保 mitmproxy CA 受信任（首次会触发一次系统授权 —— macOS Authorization Services 密码框 / Windows 证书安装确认框 —— 之后不再弹）。

#### Scenario: 启动即抓取
- **WHEN** 用户打开 wx-sniffer
- **THEN** 窗口弹出后几秒内，mitmproxy SHALL 在 127.0.0.1:8899 监听、系统 HTTP/HTTPS 代理 SHALL 已指向它（`mode=regular`），日志面板 SHALL 出现「自动开启代理：…」

#### Scenario: 首次装证书
- **WHEN** 第一次启动且 mitmproxy CA 还没受信任
- **THEN** SHALL 弹一次系统授权框装证书；用户授权后该 CA SHALL 受信任，之后启动 SHALL NOT 再弹

#### Scenario: 自动开启失败时清理
- **WHEN** 自动开启代理失败（如 8899 端口被占）
- **THEN** SHALL 调用 `proxy_manager.stop()` 清掉可能残留的系统代理设置（别让用户网断在那），并在日志面板写 ERROR

### Requirement: 关闭软件即自动关代理并还原系统设置
关闭软件 SHALL 触发 `_stop_proxy_once()`（幂等）：关 mitmproxy + 还原系统代理设置。触发路径 SHALL 包括：pywebview 窗口关闭后（`webview.start()` 返回）、`atexit`、SIGTERM/SIGINT 信号、以及降级浏览器模式下的退出。

#### Scenario: 关窗口还原代理
- **WHEN** 用户关闭 wx-sniffer 窗口
- **THEN** macOS 系统代理 SHALL 变回未启用（`Enabled: No`）/ Windows 注册表 `Internet Settings` SHALL 还原到开代理前的快照；mitmproxy SHALL 停止

#### Scenario: 强杀后下次启动自愈
- **WHEN** 软件被强制结束（SIGKILL / 断电）来不及清理，系统代理还指着 127.0.0.1:8899
- **THEN** 下次启动时自动开代理 SHALL 重新在 8899 起 mitmproxy，使该代理设置重新生效（HTTP 流量恢复）；用户也可手动在系统设置里关掉代理

### Requirement: UI 只剩状态徽章，无抓取按钮
顶栏 SHALL NOT 有「开始抓取」「停止抓取」按钮。SHALL 有一个「代理：抓取中 / 未运行」状态徽章（绿色=运行中、橙色=未运行），每 4 秒轮询 `/api/proxy/status` 刷新；sync-bar 的「代理状态」文字 SHALL 同步显示。

#### Scenario: 状态准确
- **WHEN** 代理正在运行，用户打开/刷新页面
- **THEN** 顶栏徽章 SHALL 在几秒内显示「代理：抓取中」（绿色），不会一直停在「检查中」或错误地显示「未运行」

#### Scenario: 无抓取按钮
- **WHEN** 查看顶栏
- **THEN** SHALL 看不到「开始抓取」「停止抓取」按钮（功能改成自动）

### Requirement: 常开代理不影响 VPN 与日常软件
代理常开（`mode=regular` + 系统 HTTP/HTTPS 代理指向 mitmproxy）SHALL NOT 影响 VPN 连接（VPN 在路由层，隧道本身不走 HTTP 代理），SHALL NOT 影响微信 / 浏览器 / 办公软件的正常使用（mitmproxy 只对美团/点评域名解密 —— `allow_hosts` 限制 —— 其它所有域名 TLS 透传，应用拿到的还是真服务器真证书）。

#### Scenario: VPN 仍可用
- **WHEN** 代理开着，用户连 VPN
- **THEN** VPN 隧道 SHALL 正常建立与使用（不被 HTTP 代理设置影响）

#### Scenario: 微信正常
- **WHEN** 代理开着，用户正常用微信（登录、聊天、刷朋友圈等非美团相关功能）
- **THEN** 这些功能 SHALL 正常 —— 它们的 HTTPS 走 TLS 透传，不被解密、不被中断

