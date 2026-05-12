## ADDED Requirements

### Requirement: 统一的跨平台 ProxyManager 接口
代码 SHALL 通过 `platform_proxy` 包对外暴露唯一一个 `ProxyManager` 类，且其 `start()` / `stop()` / `status()` 三个方法在 macOS 与 Windows 上具有一致的输入/输出契约。调用方（`server.py`、UI）不得在自身代码中含有任何 `if platform == "darwin"` 之类的平台分支。

#### Scenario: 工厂按平台分派实现
- **WHEN** 任意模块执行 `from platform_proxy import ProxyManager; m = ProxyManager()`
- **THEN** 在 macOS 上 `m` SHALL 是 `MacProxyManager` 的实例；在 Windows 上 SHALL 是 `WindowsProxyManager` 的实例；二者 SHALL 都继承自 `AbstractProxyManager`

#### Scenario: status() 输出契约
- **WHEN** 在任一平台调用 `m.status()`
- **THEN** 返回字典 SHALL 至少包含键 `running`、`mode`、`listen_host`、`listen_port`、`mitmdump_found`、`message`，键名与类型在两个平台保持一致

### Requirement: macOS 透明代理基于 mitmproxy wireguard 模式
在 macOS 上，`ProxyManager.start()` SHALL 启动 mitmproxy 的 `wireguard` 模式以获得对应用层透明的抓包能力，并使用 `--allow-hosts` 仅放行美团相关域名（`meituan.com`、`sankuai.com`、`dianping.com`）。

#### Scenario: 仅放行美团域名
- **WHEN** macOS 启动透明代理
- **THEN** mitmproxy 启动参数 SHALL 包含 `--allow-hosts` 且其值匹配上述三个域名的正则，不放行其它域名

#### Scenario: 停止时清理
- **WHEN** 调用 `m.stop()` 后再调用 `m.status()`
- **THEN** `running` SHALL 为 `False`，且监听端口上 SHALL 不再有残留的 mitmdump 进程

### Requirement: Windows 系统代理基于注册表 + WinINET 通知
在 Windows 上，`WindowsProxyManager` SHALL 通过修改 `HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings` 的 `ProxyEnable` / `ProxyServer` / `ProxyOverride` 三个值开启系统代理，并调用 `InternetSetOptionW(INTERNET_OPTION_SETTINGS_CHANGED)` 与 `INTERNET_OPTION_REFRESH` 通知 WinINET 立即生效；停止时 SHALL 恢复至开启代理前的注册表快照。

#### Scenario: 开启时写入注册表
- **WHEN** Windows 上调用 `m.start()` 且 mitmproxy 成功监听
- **THEN** `HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings\ProxyEnable` SHALL 为 `1`，`ProxyServer` SHALL 为 `127.0.0.1:<listen_port>`

#### Scenario: 停止时还原快照
- **WHEN** Windows 上调用 `m.stop()`
- **THEN** 上述注册表键值 SHALL 恢复到 `start()` 之前由 manager 缓存下来的原始值；若之前 `ProxyEnable` 为 `0`，停止后 SHALL 仍为 `0`

#### Scenario: 不使用 netsh winhttp
- **WHEN** 代码搜索 `WindowsProxyManager` 的实现
- **THEN** 实现 SHALL NOT 调用 `netsh winhttp set proxy` 命令

### Requirement: Windows 端优先 wireguard 模式，失败可降级
在 Windows 上，`start()` SHALL 优先尝试 mitmproxy `wireguard` 模式；当 wireguard 启动失败（如缺少 WinTUN 驱动或非管理员权限）时，SHALL 降级到 regular HTTP 代理 + 注册表方案，并在返回结果的 `message` 中明确标注"已降级"以及风控风险升高的提示。

#### Scenario: wireguard 启动成功
- **WHEN** Windows 已具备 WinTUN 驱动且 `start()` 成功
- **THEN** `status().mode` SHALL 为 `"wireguard"`

#### Scenario: 降级到 regular 模式
- **WHEN** wireguard 启动抛出可识别的驱动/权限错误
- **THEN** `start()` SHALL 自动重试 regular 模式，最终 `status().mode` SHALL 为 `"regular"`，且 `start()` 返回的 `message` 字段 SHALL 包含"已降级"字样

### Requirement: mitmdump 可执行文件解析
两个平台的实现 SHALL 按以下优先级解析 mitmdump 可执行文件路径：(1) `config.capture.mitmdump_path`；(2) `shutil.which("mitmdump")`；(3) 平台特定常见安装位置候选列表。任一找到即返回。

#### Scenario: 配置文件优先
- **WHEN** `config.capture.mitmdump_path` 指向一个真实存在的可执行文件
- **THEN** `ProxyManager` SHALL 使用该路径，不再查询 `PATH` 或候选位置

#### Scenario: PATH 兜底
- **WHEN** 配置未提供路径但 `mitmdump` 在系统 PATH 上
- **THEN** `ProxyManager` SHALL 使用 `shutil.which("mitmdump")` 的结果

### Requirement: 进程内 mitmproxy 启动
`ProxyManager` SHALL 通过 `mitmproxy.tools.dump.DumpMaster` 的 Python API 在应用自身进程内的独立线程中运行 mitmproxy，而 NOT 使用 `subprocess.Popen([mitmdump, ...])` 派生独立子进程，以避免生成名为 `mitmdump` 的可被外部观测的子进程。

#### Scenario: 不存在 mitmdump 子进程
- **WHEN** `m.start()` 成功后在主机上枚举进程
- **THEN** 进程列表 SHALL NOT 出现命令行包含 `mitmdump` 的独立子进程

#### Scenario: 异常自动重启
- **WHEN** 进程内 mitmproxy 线程因网络异常崩溃
- **THEN** `ProxyManager` SHALL 捕获异常、记录到 `db.add_log`，并在 5 秒内尝试重启至多 3 次；3 次失败后 `status().running` SHALL 为 `False`
