## Context

现有 Mac 版代码（`app.py + server.py + db.py + proxy_manager.py + scripts/meituan_capture_addon.py`）已经实现了 90% 的端到端流程：本地 Flask Web UI + pywebview 壳 + mitmproxy `wireguard` 模式拦截美团 `/sc/product/retail/r/searchListPageNew` 返回的 SKU 列表，解析后通过本地 `/api/internal/capture-products` 推到后端。它本质上已经是一份"可跨平台的 Python 项目"，但 `proxy_manager.py` 里写死了 `darwin` 检查和 `networksetup`，无法在 Windows 上启动；同时旧的 Windows 构建产物（`wx-sniffer.exe`）已经被美团风控盯上。

利益相关方：
- 终端运营人员：双平台使用，更关心稳定性，对管理员授权/驱动安装容忍度低
- 后端服务：消费 `/api/internal/capture-products`，对负载格式有约束
- 维护者（本人）：避免双份代码、双份构建脚本带来的同步负担

约束：
- 必须保留 Python + mitmproxy 生态（用户已确认不换框架）
- 必须可被 PyInstaller 打成单文件/单目录产物
- macOS 已工作的链路不能回归

## Goals / Non-Goals

**Goals:**
- 一份源码、两份构建产物（`.app` + `.exe`），通过同一份 `.spec` 文件构建
- `proxy_manager` 在 Windows 上能像 macOS 一样"一键启停透明代理 + 系统代理回滚"
- 反检测能力达到"产物层 + 进程层 + 流量层"三层覆盖，规避现有风控特征
- 配置 / 日志 / SQLite DB 从项目内部目录迁移到系统标准用户数据目录

**Non-Goals:**
- 不重写前端 UI、不替换 Flask、不引入 Electron / Tauri
- 不做"绕过美团登录/签名"层面的逆向，反爬只聚焦在被动检测（指纹、特征、行为）层面
- 不支持 Linux（PyInstaller 仍可在 Linux 跑，但不进入官方发布矩阵）
- 不实现自动更新（OTA）——本次范围之外

## Decisions

### D1：以"原地改造 Mac 文件夹 + 重命名"作为合并方式
- **选择**：将 `插件_美团抓包同步工具_Mac版/` 重命名为 `wx-sniffer/`，删除 `插件_美团抓包同步工具_Windows版/`
- **理由**：Windows 目录里没有源码，留它只是制造混淆；用户已明确选择此路径
- **替代**：新建 `wx-sniffer/` 同级目录把两份内容并入——多一次 IO，并且需要二次校对 git 历史/引用，收益为零

### D2：`proxy_manager` 拆为 `platform_proxy/` 包
- 结构：
  - `platform_proxy/__init__.py`：导出 `ProxyManager`，按 `sys.platform` 工厂分派
  - `platform_proxy/base.py`：`AbstractProxyManager` 抽象类，定义 `start/stop/status/_set_system_proxy/_unset_system_proxy/_mitmdump_path`
  - `platform_proxy/macos.py`：保留现有 `networksetup` 逻辑
  - `platform_proxy/windows.py`：新增 Windows 实现
- **Windows 系统代理方案**：直接改写注册表 `HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings`（`ProxyEnable=1`、`ProxyServer=127.0.0.1:port`、`ProxyOverride=<local>`）+ 调用 `InternetSetOptionW(INTERNET_OPTION_SETTINGS_CHANGED)` 让 WinINET 立即生效。**不**用 `netsh winhttp set proxy`，因为它只影响 WinHTTP 不影响 WinINET/Chromium，并且需要管理员权限
- **替代考虑**：用 `pywin32` 的 `win32inet` 包装 vs 纯 `ctypes`——选 `pywin32`，已是依赖且 API 更稳

### D3：mitmproxy 优先使用 `wireguard` 模式（双平台一致）
- **理由**：应用层完全感知不到代理，避免 WeChat / 美团 App 检查"系统是否设置了代理"这一类最基础的反爬特征。Mac 已验证可用
- **Windows 落地**：mitmproxy 9.x 的 wireguard 模式在 Windows 需要 WinTUN 驱动（`wintun.dll` 随 mitmproxy 自带）。首次启动若驱动未注册，给出明确引导
- **降级**：若 wireguard 不可用（例如缺管理员权限），降级为 regular HTTP 代理 + 改注册表，并在 UI 明确提示"风控风险升高"
- **替代**：纯 HTTP(S) 代理——简单但已被风控识别（旧 .exe 就在此模式下被封）；网卡层 WinDivert——更隐蔽但需自研驱动注入，收益不抵成本

### D4：反检测分三层
1. **产物层**：
   - PyInstaller 输出名不再叫 `wx-sniffer.exe`，改为按构建时变量生成（默认 `MTCenter.exe`，可在构建脚本里 override），并清除 PyInstaller 默认 `VS_VERSION_INFO`（避免 `pyi-` 字样）
   - `.spec` 中 `console=False`，移除 `bootloader_ignore_signals` 等明显 PyInstaller 特征字符串（不可全清，但减少高熵特征）
   - 内嵌的 `_internal/` 目录改名为 `runtime/`（PyInstaller 支持 `--contents-directory`）
2. **进程层**：
   - 启动 mitmdump 时不使用 `subprocess.Popen([mitmdump, ...])` 这种带可识别 cmdline 的方式；改成调用 mitmproxy 的 Python API（`mitmproxy.tools.dump.DumpMaster`）在**当前进程**内启动一个独立线程，避免出现一个名为 `mitmdump` 的子进程被风控的本机 Agent 捕获
   - 抓包 addon 不再以 `-s scripts/meituan_capture_addon.py` 命令行方式加载（路径暴露），改为构建时打入 PyZ，由代码 `master.addons.add(...)` 注入
3. **流量层**：
   - 抓包 addon 内回传后端的 `requests.post(...)` 改用 `curl_cffi`（impersonate=chrome120），避免 Python `requests` 的 JA3 指纹被关联（即使这里发的是本地回传，也避免它被一并作为"本机存在 Python 抓包工具"的特征）——仅当 `curl_cffi` 可用时启用
   - mitmproxy 启动时设置 `tls_version_client_min=TLS1.2`、关闭 `--showhost`、关闭日志中域名打印，减少日志侧泄露

### D5：路径与数据目录
- 新增 `platform_paths.py`：
  - macOS：`~/Library/Application Support/wx-sniffer/{config.toml, data.db, logs/}`
  - Windows：`%LOCALAPPDATA%\wx-sniffer\{config.toml, data.db, logs\}`
- `config.py` 与 `db.py` 改为从 `platform_paths` 取根目录，去掉 `BASE_DIR` 直引用
- 首次启动若发现旧的 `BASE_DIR/data.db` 存在则迁移一次（带 `.migrated` 标记），避免历史数据丢失

### D6：构建脚本与依赖
- 合一为 `wx-sniffer.spec`，内部 `if sys.platform == "darwin": BUNDLE(...)` 否则跳过 BUNDLE
- `build_mac.sh` + `build_win.ps1` 两份壳脚本，仅负责创建/激活 venv、装依赖、调用 `pyinstaller wx-sniffer.spec --contents-directory runtime`
- `requirements.txt`：
  - 通用：Flask、pywebview、mitmproxy、urllib3、requests
  - Windows 条件：`pywin32; sys_platform == "win32"`
  - 可选反爬：`curl_cffi>=0.6; python_version>="3.10"`（失败可降级回 requests）

## Risks / Trade-offs

- **WinTUN 驱动安装需要管理员权限** → 启动器检测到非管理员且 wireguard 模式失败时，自动降级到 regular 模式并在 UI 弹窗提示，让用户决定是否以管理员重启
- **`netsh winhttp` 不影响 WinINET，注册表方案不影响 WinHTTP** → 项目场景下抓的是微信小程序/美团 App，它们走系统 WinINET/Chromium，注册表方案足够；后台 WinHTTP 服务不在抓包目标内
- **进程内启动 mitmproxy 会让本进程崩溃时整体退出（不像子进程可被 supervise）** → 在 mitmproxy 线程外套一层 `try/except` + 断线重启策略；同时把 mitmproxy 日志接管到 `db.add_log`
- **`curl_cffi` 在 Windows PyInstaller 打包时可能缺 `libcurl-impersonate-chrome.dll`** → `hiddenimports` + `datas` 显式带入；若仍失败，回退 requests，告警入日志
- **进程改名 / 去签名 vs 杀软误报** → 自定义产物名容易被某些杀软（特别是 Windows Defender + SmartScreen）当作可疑文件，必要时考虑代码签名证书（本次不强制，留作后续）
- **CA 证书静默安装** → Windows 端把 mitmproxy 根证书静默写入 `Cert:\CurrentUser\Root`（用户 store，无需管理员）即可满足 WinINET 信任；不写 `LocalMachine\Root` 避免高敏操作

## Migration Plan

1. **创建合并产物**：原地把 `插件_美团抓包同步工具_Mac版/` 重命名为 `wx-sniffer/`（移动后再做代码改造，保留 Git mv 历史；如果不在 Git 仓库内则普通 `mv`）
2. **代码重构**：先抽 `platform_paths.py` 与 `platform_proxy/` 包，保证 macOS 行为零回归（跑 `python app.py` 烟测）
3. **数据迁移**：第一次启动检测旧 DB 路径并搬迁，写 `.migrated` 标记
4. **Windows 实现**：在 Windows VM/物理机上跑通 `python app.py` → mitmproxy wireguard 启动 → 系统代理切换 → 抓包回传
5. **反检测加固**：分别上线 D4 的三层加固，每层加固后回归一次微信小程序抓包
6. **构建发布**：`build_mac.sh` 跑通后再跑 `build_win.ps1`，产物分别叫 `MTCenter.app` / `MTCenter.exe`
7. **清理**：删除 `插件_美团抓包同步工具_Windows版/`、旧 `wx-sniffer-mac.spec`

**回滚**：旧 Windows 目录在删除前打 zip 备份；macOS 端旧 `proxy_manager.py` 在重构 PR 内保留 1 个 commit 历史以便 `git revert`。

## Open Questions

- 是否需要给 Windows `.exe` 申请代码签名证书？（不签会触发 SmartScreen 警告，但签了相当于把开发者身份和"抓包工具"直接绑定，反检测视角下是负向）
- 反爬 D4.2 中"进程内 mitmproxy" vs 现有子进程方案，是否会显著增加本进程内存占用？需在迁移完成后做一次基线测量
- `curl_cffi` 在新 mitmproxy 10.x 是否值得提前升级以获取更好的 TLS 指纹控制？（当前锁的是 mitmproxy 9.x）
