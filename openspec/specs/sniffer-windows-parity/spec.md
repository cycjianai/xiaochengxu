# sniffer-windows-parity Specification

## Purpose
TBD - created by archiving change auto-proxy-and-windows-parity. Update Purpose after archive.
## Requirements
### Requirement: Windows 下与 Mac 等价运行
wx-sniffer SHALL 在 Windows 上以与 macOS 相同的方式工作：mitmproxy `regular` 模式（进程内 `DumpMaster` 监听 127.0.0.1:8899），系统代理通过修改注册表 `HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings`（`ProxyEnable/ProxyServer/ProxyOverride`）+ `InternetSetOptionW` 通知（由 `WindowsProxyManager` 完成），停止时还原快照。所有平台相关代码 SHALL 用 `sys.platform` 分支隔离，导入任一平台模块 SHALL NOT 在另一平台报错（平台特定 import 放在方法体内）。

#### Scenario: Windows 上自动开代理
- **WHEN** 在 Windows 上打开 wx-sniffer
- **THEN** mitmproxy SHALL 在 127.0.0.1:8899 监听，注册表 `Internet Settings` 的 `ProxyEnable` SHALL 为 1、`ProxyServer` SHALL 为 `127.0.0.1:8899`，并 SHALL 通过 `InternetSetOptionW` 通知生效

#### Scenario: Windows 关软件还原注册表
- **WHEN** 在 Windows 上关闭 wx-sniffer
- **THEN** 注册表 `Internet Settings` 的 `ProxyEnable/ProxyServer/ProxyOverride` SHALL 还原到开代理前由 `WindowsProxyManager` 缓存下来的快照值

#### Scenario: 跨平台模块导入不报错
- **WHEN** 在 macOS 上 `import platform_proxy.windows`（或在 Windows 上 import macos 模块）
- **THEN** SHALL 成功导入，不报错（`winreg` / `ctypes.windll` / `networksetup` 等平台 API 只在方法被调用时才用到）

### Requirement: 默认 regular 模式 + 配置迁移
`DEFAULT_CONFIG["capture"]["mode"]` SHALL 为 `"regular"`（`local` 模式实测会搞挂微信小程序 TLS）。`load_config()` SHALL 把已存在 config.json 里 `capture.mode` 为 `local` / `local_only` / `wireguard` / `wireguard_only` 的值强制改成 `regular` 并保存。

#### Scenario: 新装默认 regular
- **WHEN** 全新安装、首次生成 config.json
- **THEN** `capture.mode` SHALL 为 `"regular"`

#### Scenario: 旧 config 自动迁移
- **WHEN** 本机 config.json 的 `capture.mode` 是 `"local"`（实验期遗留）
- **THEN** 加载后 SHALL 被改写为 `"regular"`，下次抓取走普通 HTTP 代理模式

### Requirement: Windows 用 certutil 装根证书
在 Windows 上，`cert_installer` SHALL 用 `certutil -user -addstore Root <pem>` 把 mitmproxy CA 装进当前用户的「受信任的根证书颁发机构」存储（无需管理员，弹一次 Windows 安全确认框），用 `certutil -user -store Root` 检查是否已受信任。SHALL NOT 依赖 `win32crypt` 来装证书。

#### Scenario: Windows 装证书
- **WHEN** 在 Windows 上首次自动开代理、mitmproxy CA 还没受信任
- **THEN** SHALL 执行 `certutil -user -addstore Root <ca.pem>`，弹一次确认框；用户点「是」后该 CA SHALL 出现在「受信任的根证书颁发机构（当前用户）」里，`is_cert_trusted()` 返回 True，之后不再弹

#### Scenario: 没装会拒绝静默失败
- **WHEN** 系统找不到 `certutil`
- **THEN** SHALL 写一条 WARN 提示用户手动安装证书，SHALL NOT 崩溃

### Requirement: pywebview Windows 后端依赖
`requirements.txt` SHALL 声明 `pythonnet>=3.0; sys_platform == "win32"`（pywebview 在 Windows 上的 EdgeChromium/WebView2 后端需要 `clr`）。目标 Windows 机器 SHALL 需要 WebView2 Runtime（Win11 / 较新 Win10 自带，否则需单独安装）—— 在文档中说明。

#### Scenario: Windows 安装依赖
- **WHEN** 在 Windows 上执行 `pip install -r requirements.txt`
- **THEN** SHALL 安装 `pythonnet`（和 `pywin32`、`curl_cffi` 等）；在 macOS 上执行同命令 SHALL 跳过这些 Windows-only 包

