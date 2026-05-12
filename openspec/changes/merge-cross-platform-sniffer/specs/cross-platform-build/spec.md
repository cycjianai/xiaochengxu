## ADDED Requirements

### Requirement: 单一源码树同时支持 macOS 与 Windows 构建
项目 SHALL 维护单一源码目录 `wx-sniffer/`，通过同一份 PyInstaller spec 文件在 macOS 上产出 `.app` 包，在 Windows 上产出 `.exe` 目录化分发产物。不允许出现按平台分叉的源码副本。

#### Scenario: macOS 上构建
- **WHEN** 维护者在 macOS 主机执行 `./build_mac.sh`
- **THEN** 构建脚本 SHALL 创建/激活 venv，安装 `requirements.txt`，调用 `pyinstaller wx-sniffer.spec`，最终在 `dist/` 下产出可双击运行的 `.app` 包

#### Scenario: Windows 上构建
- **WHEN** 维护者在 Windows 主机执行 `./build_win.ps1`
- **THEN** 构建脚本 SHALL 创建/激活 venv，安装 `requirements.txt` 中的通用与 Windows 条件依赖，调用 `pyinstaller wx-sniffer.spec --contents-directory runtime`，最终在 `dist/` 下产出可独立运行的目录化 `.exe` 产物

#### Scenario: 同一份 spec 文件被两平台共用
- **WHEN** 读取 `wx-sniffer.spec`
- **THEN** spec 文件 SHALL 通过 `sys.platform` 判断决定是否调用 `BUNDLE(...)` 段，且不依赖任何外部"按平台选 spec"的脚本

### Requirement: 平台条件依赖
`requirements.txt` SHALL 使用 PEP 508 环境标记声明仅 Windows 需要的依赖，使 macOS 安装时不会拉取无意义的 Windows-only 包，反之亦然。

#### Scenario: Windows 条件依赖被声明
- **WHEN** 检视 `requirements.txt`
- **THEN** 该文件 SHALL 包含一行形如 `pywin32; sys_platform == "win32"` 的条目

#### Scenario: macOS 安装时不拉 Windows 包
- **WHEN** 在 macOS 上执行 `pip install -r requirements.txt`
- **THEN** pip SHALL 跳过 `pywin32`，不报错也不安装

### Requirement: 旧 Windows 构建产物目录被移除
合并完成后，仓库 SHALL 不再保留 `插件_美团抓包同步工具_Windows版/` 目录或其中的 `wx-sniffer.exe`、`_internal/` 等旧产物。

#### Scenario: 旧目录已删除
- **WHEN** 在合并后的工作区列出 `插件` 根目录
- **THEN** 列表中 SHALL NOT 出现 `插件_美团抓包同步工具_Windows版`

#### Scenario: Mac 目录被重命名为统一名
- **WHEN** 在合并后的工作区列出 `插件` 根目录
- **THEN** 原 `插件_美团抓包同步工具_Mac版/` SHALL 已重命名为 `wx-sniffer/`，且不再保留 `wx-sniffer-mac.spec`（被 `wx-sniffer.spec` 取代）

### Requirement: 用户数据目录平台规范化
应用 SHALL 把配置、SQLite 数据库与日志写入操作系统标准用户数据目录，不再写入项目源码目录或 PyInstaller 临时解包目录。

#### Scenario: macOS 数据路径
- **WHEN** 应用在 macOS 上首次启动并写入日志
- **THEN** 日志文件 SHALL 出现在 `~/Library/Application Support/wx-sniffer/logs/` 下

#### Scenario: Windows 数据路径
- **WHEN** 应用在 Windows 上首次启动并写入日志
- **THEN** 日志文件 SHALL 出现在 `%LOCALAPPDATA%\wx-sniffer\logs\` 下

#### Scenario: 历史数据迁移
- **WHEN** 应用首次启动且检测到旧的项目内 `data.db`
- **THEN** 应用 SHALL 将其复制到新路径，并在源位置放置 `.migrated` 标记文件，后续启动不再重复迁移
