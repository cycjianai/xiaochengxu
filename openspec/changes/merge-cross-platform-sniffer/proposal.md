## Why

历史上"插件_美团抓包同步工具_Mac版"与"插件_美团抓包同步工具_Windows版"两个目录被当作两个项目维护，但实际上 Windows 目录里只有 PyInstaller 产物（`wx-sniffer.exe + _internal/`），不存在独立源码——它们本来就是同一份 Python 项目（Flask + pywebview + mitmproxy + PyInstaller）的两个构建产物。Mac 源码中的 `proxy_manager.py` 直接以 `platform.system() != "darwin"` 报错拒绝 Windows，导致这份"通用"代码事实上只能在 Mac 上跑。与此同时，旧版 Windows 二进制在生产中已经触发美团风控/封号，急需在合并的同时强化反检测能力。

## What Changes

- 以 Mac 源码为基线，将其原地重命名为统一项目（`wx-sniffer/`），删除 Windows 目录里的旧构建产物
- **BREAKING**：`proxy_manager.py` 由 Mac 单平台实现重构为 `platform_proxy/` 包，对外暴露统一 `ProxyManager` 接口，内部按 `darwin`/`win32` 分派
  - **最终选定方案**：mitmproxy 11.x + `regular` HTTP 代理（对齐 Windows 原版 wx-sniffer.exe 的实测可工作方案）
  - macOS：`networksetup` 自动切换 macOS 系统代理；CA 证书通过 `osascript ... with administrator privileges` 弹密码框装入 admin trust（WeChat 等 Chromium-based 应用只认 admin 域）
  - Windows：`HKCU\...\Internet Settings` 写注册表 + `InternetSetOptionW` 通知 WinINET
  - 探索过 `wireguard`（要求装 WireGuard 客户端，体验重）和 mitmproxy 11 `local` 模式（macOS Network Extension 与 WeChat 小程序 TLS 链不兼容，导致闪退），实测后均舍弃
- 反爬强化（新能力）：进程伪装、可执行文件改名/去签名特征、mitmproxy 启动参数隐藏 banner、TLS 指纹（JA3）混淆、CA 证书静默安装与轮换、抓包脚本路径混淆与按需注入
- 构建脚本统一：`build_mac.sh` 与新增 `build_win.ps1` 共用同一份 `.spec`（条件分支区分 `BUNDLE` vs Windows EXE）；`requirements.txt` 增加平台条件依赖（`pywin32; sys_platform=="win32"`）
- 配置 / 日志 / 数据目录按平台规范化（macOS: `~/Library/Application Support/wx-sniffer`，Windows: `%LOCALAPPDATA%\wx-sniffer`），不再写入 PyInstaller 临时目录
- 删除目录：`插件_美团抓包同步工具_Windows版/`（仅含旧产物）；重命名：`插件_美团抓包同步工具_Mac版/` → `wx-sniffer/`

## Capabilities

### New Capabilities
- `cross-platform-build`：统一项目结构、依赖、PyInstaller spec 与构建脚本，使同一份源码可在 macOS 产出 `.app`、在 Windows 产出 `.exe`
- `platform-proxy`：跨平台的系统代理/透明代理抽象层，封装 macOS 与 Windows 各自的系统调用，向上游暴露统一的 `start/stop/status` 接口
- `anti-detection`：针对美团风控的反检测能力集合——进程名/产物特征伪装、TLS 指纹混淆、证书与抓包脚本的隐蔽化部署
- `meituan-capture`：把现有 `meituan_capture_addon.py` 的拦截 / 解析 / 回传逻辑显式化为一项能力（响应过滤、SKU 抽取、回传后端）

### Modified Capabilities
<!-- 当前项目尚未沉淀 openspec/specs/，无既有能力可修改 -->

## Impact

- 代码：`proxy_manager.py` 重构为包；`app.py / server.py / config.py / db.py` 中所有路径与平台分支统一改走新的 `platform_paths` 工具；`scripts/meituan_capture_addon.py` 增加指纹混淆相关 mitmproxy 选项
- 依赖：`requirements.txt` 增加 `pywin32`（Windows 条件）、可选 `curl_cffi`（用于 JA3 模拟，反爬场景）
- 构建：新增 `build_win.ps1` 与 `wx-sniffer.spec`（合一），删除原 `wx-sniffer-mac.spec`
- 目录：删除 `插件_美团抓包同步工具_Windows版/`；重命名 Mac 目录；旧构建目录 `build/`、`dist/` 进入 `.gitignore`
- 运行时：用户配置/数据从项目目录迁移到系统标准 AppData 路径，需要一次性迁移脚本
- 风险：Windows 端 mitmproxy `wireguard` 模式依赖 WinTUN 驱动，首次启动需引导安装；CA 证书静默安装在 Windows 需以管理员身份运行
