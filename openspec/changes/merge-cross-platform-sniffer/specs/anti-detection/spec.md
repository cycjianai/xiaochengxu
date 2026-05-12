## ADDED Requirements

### Requirement: 产物层伪装
PyInstaller 产物 SHALL 以可配置的产品名输出（默认 `MTCenter`），且 SHALL 通过 `--contents-directory` 把默认的 `_internal` 资源目录改名为 `runtime`，以减少与公开 PyInstaller 默认特征的关联。

#### Scenario: 产物名可配置
- **WHEN** 构建脚本以环境变量 `WX_SNIFFER_PRODUCT_NAME=Foo` 调用
- **THEN** `dist/` 下产物名 SHALL 为 `Foo.app` 或 `Foo.exe`

#### Scenario: 资源目录已改名
- **WHEN** 构建产物完成
- **THEN** Windows 产物的同级 SHALL 出现名为 `runtime/` 的资源目录，且 SHALL NOT 出现 `_internal/`

### Requirement: 进程层隐藏抓包脚本路径
mitmproxy 启动 SHALL NOT 通过命令行 `-s scripts/meituan_capture_addon.py` 加载抓包脚本；addon 实例 SHALL 在代码内直接通过 `master.addons.add(MeituanCaptureAddon())` 注入，使外部观测到的进程命令行不含抓包脚本路径。

#### Scenario: 命令行不暴露脚本
- **WHEN** 任意外部工具尝试读取 mitmproxy 启动命令行
- **THEN** 命令行 SHALL NOT 包含 `meituan_capture_addon` 或任何 `*.py` 路径字符串

#### Scenario: addon 由 Python 内部注入
- **WHEN** 检视启动 mitmproxy 的源码路径
- **THEN** 源码 SHALL 显式构造 `MeituanCaptureAddon` 实例并通过 `master.addons.add(...)` 注入

### Requirement: 流量层指纹混淆
抓包 addon 内向本地后端回传 SKU 的 HTTP 请求 SHALL 优先使用 `curl_cffi` 并以 `impersonate="chrome120"` 发送，以避免 Python `requests` 的 JA3 指纹被作为"本机存在 Python 抓包工具"的特征聚合。`curl_cffi` 不可用时方可降级到 `requests`，且 SHALL 写入告警日志。

#### Scenario: curl_cffi 可用时优先使用
- **WHEN** 运行时 `import curl_cffi` 成功
- **THEN** addon 回传 SHALL 通过 `curl_cffi.requests.post(..., impersonate="chrome120")` 发出

#### Scenario: 降级告警
- **WHEN** `curl_cffi` 导入失败
- **THEN** addon SHALL 退回 `requests` 并通过 `db.add_log("WARN", ...)` 记录降级原因，每次进程启动至多记录一次

### Requirement: CA 证书静默安装到用户证书库
首次启动时，应用 SHALL 自动把 mitmproxy 的根证书写入当前用户证书库（macOS：用户钥匙串；Windows：`Cert:\CurrentUser\Root`），不要求管理员权限，并 SHALL 在每次启动检查证书有效期，过期前 30 天自动轮换。

#### Scenario: macOS 用户钥匙串安装
- **WHEN** macOS 首次启动且 mitmproxy 根证书尚未受信任
- **THEN** 应用 SHALL 通过 `security add-trusted-cert -k login.keychain ...` 将证书加入用户钥匙串，且不调用任何需要 sudo 的命令

#### Scenario: Windows 用户证书库安装
- **WHEN** Windows 首次启动且 mitmproxy 根证书尚未受信任
- **THEN** 应用 SHALL 通过 `CertAddCertificateContextToStore`（pywin32）将证书加入 `CurrentUser\Root`，且不写入 `LocalMachine\Root`

#### Scenario: 即将过期自动轮换
- **WHEN** 启动时检测到根证书剩余有效期小于 30 天
- **THEN** 应用 SHALL 生成新根证书、替换 mitmproxy 配置目录中的证书文件并重新写入用户证书库

### Requirement: 启动后管理员/驱动状态自检
应用启动时 SHALL 自检以下条件并在 UI 中以非阻塞方式展示结果：(1) mitmproxy 根证书是否已受信任；(2) Windows 端 WinTUN 驱动是否可用；(3) 当前进程是否具备管理员/root 权限；(4) 是否检测到本机存在反 mitm 风险（如 `Charles`、`Fiddler` 等已知抓包工具进程同时运行）。

#### Scenario: 健康自检 API 返回字段齐全
- **WHEN** UI 请求 `/api/health/anti-detection`
- **THEN** 响应 JSON SHALL 包含布尔字段 `cert_trusted`、`wintun_ready`（仅 Windows）、`is_elevated`、`conflicting_tools`（字符串数组）以及一个 `recommendations` 文本字段

#### Scenario: 存在冲突工具时告警
- **WHEN** 自检发现本机正在运行 Charles/Fiddler/Wireshark 之一
- **THEN** 响应中的 `conflicting_tools` SHALL 包含对应名称，UI SHALL 以醒目颜色展示并建议关闭这些工具
