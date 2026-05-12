## 1. 目录合并与基线快照

- [x] 1.1 已 zip 备份到 `~/Desktop/插件_美团抓包同步工具_Windows版_backup_20260512-013157.zip`（31M）
- [x] 1.2 `插件_美团抓包同步工具_Mac版/` 已重命名为 `wx-sniffer/`；重命名后 openspec status + Python 模块导入均通过
- [ ] 1.3 在新 `wx-sniffer/` 内运行一次 `python app.py`，确认重命名未破坏现有 macOS 行为，记录基线截图（需人工运行）
- [x] 1.4 `插件_美团抓包同步工具_Windows版/` 已删除（zip 备份在 Desktop 兜底）

## 2. 平台路径与配置规范化

- [x] 2.1 新建 `platform_paths.py`，导出 `app_data_dir()`、`logs_dir()`、`config_path()`、`db_path()`，按平台返回 `~/Library/Application Support/wx-sniffer` 或 `%LOCALAPPDATA%\wx-sniffer`
- [x] 2.2 修改 `config.py`：`load_config()` 从 `platform_paths.config_path()` 读，找不到则写入默认配置；首次启动调用 `migrate_legacy_data()`
- [x] 2.3 修改 `db.py`：SQLite 路径走 `platform_paths.db_path()`；旧 `~/Library/Application Support/wx-sniffer-mac/sniffer_mac.db` 与 `BASE_DIR/data.db` 两个旧位置都通过 `migrate_legacy_data()` 迁移并落 `.migrated_<name>` 标记
- [x] 2.4 `server.py` 中 `LOG_FILE` 自动走 `platform_paths.log_file_path()`（通过 config.py 重导出）；`app.py` 启动日志已统一为 `wx-sniffer`
- [ ] 2.5 macOS 上回归：删除新数据目录，启动一次确认数据/日志写入新位置；再放一份旧 `data.db` 到 `BASE_DIR` 重启确认迁移生效（需人工运行）

## 3. ProxyManager 抽象与 macOS 实现迁移

- [x] 3.1 新建包 `platform_proxy/`：`__init__.py`（工厂 `ProxyManager.__new__` 按 `sys.platform` 分派）、`base.py`（`AbstractProxyManager` 含 `start/stop/status/_mitmdump_path/_set_system_proxy/_unset_system_proxy/_wireguard_supported`）
- [x] 3.2 macOS 逻辑迁入 `platform_proxy/macos.py`（`MacProxyManager`）；`networksetup` 用于 regular 模式降级路径
- [x] 3.3 删除旧 `proxy_manager.py`；`server.py` 改为 `from platform_proxy import ProxyManager`
- [ ] 3.4 macOS 上回归一次完整抓包链路（启动代理 → 微信小程序刷美团门店 → 看后端是否收到 `items`，需人工运行）

## 4. mitmproxy 进程内化与 addon 内嵌

- [x] 4.1 `platform_proxy/base.py::_spawn_master_thread` 在独立 `asyncio` 线程内构造 `Options` + `DumpMaster` 并 `await master.run()`
- [x] 4.2 `scripts/meituan_capture_addon.py` 重构为 `capture/meituan_addon.py`，hook `response()` 迁到 `MeituanCaptureAddon` 类；旧脚本已删除
- [x] 4.3 `base._build_addons()` 构造 `MeituanCaptureAddon(import_url, capture_token)` 并通过 `master.addons.add(addon)` 注入；不再有 `-s ...`
- [x] 4.4 `base._maybe_restart()`：5s 滑动窗口最多 3 次重启，超限置 `running=False` 并 ERROR 日志
- [ ] 4.5 macOS 回归：确认进程列表里不再有 `mitmdump` 子进程，抓包链路仍工作（需人工运行）

## 5. Windows 实现

- [x] 5.1 新建 `platform_proxy/windows.py`，`WindowsProxyManager(AbstractProxyManager)`
- [x] 5.2 `_set_system_proxy()`：先快照 `ProxyEnable/ProxyServer/ProxyOverride`（记录原 vtype 与值，缺失也记），写新值，通过 `ctypes.windll.Wininet.InternetSetOptionW` 发 `SETTINGS_CHANGED(39)` + `REFRESH(37)`
- [x] 5.3 `_unset_system_proxy()`：从 `self._snapshot` 回写，缺失值用 `DeleteValue` 还原；再发通知
- [x] 5.4 wireguard 优先 + regular 降级在 `base._start_locked()` 通过 `_wireguard_supported()` 钩子统一处理；Windows `_wireguard_supported()` 检测 `mitmproxy.addons.wireguard` 可导入；降级时 `message` 含"已降级（风控风险升高）"
- [x] 5.5 `requirements.txt`：`pywin32>=306; sys_platform == "win32"` + `curl_cffi>=0.6; python_version >= "3.10"`
- [ ] 5.6 在 Windows VM 上跑 `python app.py`：依次验证 wireguard 成功路径、wireguard 失败→降级 regular 路径、`stop()` 后注册表快照还原（需 Windows 环境）
- [ ] 5.7 Windows 上跑端到端抓包链路（微信桌面端小程序刷美团门店 → 后端收到 items，需 Windows 环境）

## 6. 反检测加固

- [x] 6.1 新增 `capture/http_client.py::HttpPoster`：`curl_cffi.requests.post(..., impersonate="chrome120")` 优先，`requests` 降级时通过 `db.add_log("WARN", ...)` 一次性告警；`MeituanCaptureAddon` 改用 `HttpPoster`
- [x] 6.2 新增 `cert_installer.py`：macOS 用 `security add-trusted-cert -r trustRoot -k ~/Library/Keychains/login.keychain-db`；Windows 用 `win32crypt.CertAddEncodedCertificateToStore` 写 `CurrentUser\Root`；剩余 < 30 天时删除旧证书让 mitmproxy 启动时自动重新生成；`server.py` 在 `/api/proxy/start` 成功后调用 `install_cert_if_needed()`
- [x] 6.3 `server.py` 新增 `GET /api/health/anti-detection`，返回字段含 `cert_trusted`、`is_elevated`、`conflicting_tools`、`recommendations`，Windows 额外含 `wintun_ready`；UI 顶栏 `status-chip` 变为健康徽章，每 60s 轮询并支持点击查看详情
- [x] 6.4 `anti_detection.conflicting_tools()`：Windows 走 `tasklist`，macOS/Linux 走 `ps -Ao comm=`；匹配 Charles/Fiddler/Wireshark/Proxifier 以及外部 `mitmproxy`/`mitmdump`

## 7. 构建脚本与产物伪装

- [x] 7.1 合并为 `wx-sniffer.spec`，spec 内 `if sys.platform == "darwin":` 走 EXE+BUNDLE，其它平台走 EXE+COLLECT(`contents_directory="runtime"`)
- [x] 7.2 产物名通过 `os.environ.get("WX_SNIFFER_PRODUCT_NAME", "MTCenter")` 注入，bundle id 同步 `WX_SNIFFER_BUNDLE_ID`
- [x] 7.3 `scripts/build_mac.sh` 改用合一 spec；spec 已在 COLLECT 里设置 `contents_directory="runtime"`，无需 CLI 传参
- [x] 7.4 新增 `scripts/build_win.ps1`：venv 创建、依赖安装、pyinstaller 调用，默认产出 `dist/MTCenter/`
- [x] 7.5 删除旧 `wx-sniffer-mac.spec`；新增 `.gitignore` 含 `build/` `dist/` `.venv*/` `__pycache__/` `*.db` 等
- [ ] 7.6 在 macOS 与 Windows 上各跑一次完整构建，校验产物可双击启动且抓包链路工作（需人工运行）

## 8. 验收与归档

- [ ] 8.1 双平台跑端到端冒烟：启动 → 透明代理就绪 → 微信小程序操作 → 后端落库 → UI 看到条目
- [ ] 8.2 跑健康自检：`/api/health/anti-detection` 在两平台返回字段齐全；故意打开 Fiddler 复核 `conflicting_tools`
- [ ] 8.3 把基线截图、构建产物 SHA256 写入 `BUILD_REPORT.md`
- [ ] 8.4 运行 `openspec archive merge-cross-platform-sniffer` 归档本次变更
