# wx-sniffer-mac 修改说明

## 已完成的修改

### 1. 数据目录迁移到 macOS 标准位置

**修改文件**: `config.py`

- 配置、数据库、日志统一存放到 `~/Library/Application Support/wx-sniffer-mac/`
- 不再写入项目目录或 .app 包内部
- 包含文件：
  - `config.json` - 配置文件
  - `sniffer_mac.db` - SQLite 数据库
  - `app.log` - 应用日志
  - `saved_cred.json` - 保存的登录凭证

### 2. mitmdump 路径智能查找

**修改文件**: `proxy_manager.py`

实现了 `_mitmdump_path()` 方法的优先级查找逻辑：

1. **优先级 1**: 读取 `config.json` 中的 `capture.mitmdump_path`
2. **优先级 2**: 使用 `shutil.which("mitmdump")` 查找系统 PATH
3. **优先级 3**: 检查常见候选路径：
   - `<项目目录>/.venv-build/bin/mitmdump`
   - `<项目目录>/.venv/bin/mitmdump`
   - `/opt/homebrew/bin/mitmdump`
   - `/usr/local/bin/mitmdump`

### 3. 增强错误提示

**修改文件**: `proxy_manager.py`

- `start()` 方法现在会返回详细的错误信息
- 找不到 mitmdump 时会提示安装方法和配置路径
- mitmdump 启动失败时会捕获 stderr 输出并显示
- 检查抓包脚本是否存在
- 所有错误都会记录到日志并通过弹窗显示给用户

### 4. 前端错误显示

**已验证**: `static/app.js`

- `startProxy()` 和 `stopProxy()` 已经实现了错误弹窗
- 代理状态会实时更新到页面顶部
- 错误信息会同时显示在日志区域

### 5. 更新 README

**修改文件**: `README.md`

- 更新配置文件路径说明
- 添加 mitmdump 路径配置说明
- 添加查找优先级说明
- 更新所有路径引用

### 6. Python 3.9 兼容性

**已验证**: `requirements.txt`

- Flask>=2.2,<2.3 - 兼容 Python 3.9
- mitmproxy>=9.0.1,<10.0 - 兼容 Python 3.9
- 所有依赖都支持 Python 3.9

## 下一步操作

### 步骤 1: 清理并重新打包

```bash
cd /Users/cenbusi/Desktop/wx-sniffer-mac
rm -rf build dist
bash scripts/build_mac.sh
```

### 步骤 2: 创建配置文件

```bash
mkdir -p ~/Library/Application\ Support/wx-sniffer-mac
cat > ~/Library/Application\ Support/wx-sniffer-mac/config.json <<'EOF'
{
  "local_auth": {
    "username": "admin",
    "password": "admin123"
  },
  "sync": {
    "base_url": "http://127.0.0.1:8000",
    "sync_path": "/api/products/sniffer/sync",
    "timeout_seconds": 15
  },
  "capture": {
    "listen_host": "127.0.0.1",
    "listen_port": 8899,
    "import_path": "/api/internal/capture-products",
    "capture_token": "replace-with-random-token",
    "mitmdump_path": "/Users/cenbusi/Desktop/wx-sniffer-mac/.venv-build/bin/mitmdump",
    "mode": "regular",
    "upstream_domain_keywords": [
      "shangoue.meituan.com",
      "waimaieapp.meituan.com"
    ]
  },
  "app": {
    "host": "127.0.0.1",
    "port": 5188,
    "window_title": "商品抓取工具 Mac 版",
    "window_width": 1400,
    "window_height": 920
  }
}
EOF
```

### 步骤 3: 测试运行

1. 启动打包后的应用：`open dist/wx-sniffer-mac.app`
2. 登录后点击"开始抓取"
3. 观察页面顶部的代理状态
4. 如果失败，查看弹窗错误信息

### 步骤 4: 如果仍然失败，收集以下信息

1. 页面顶部的代理状态文本
2. 弹窗显示的完整错误信息
3. 日志文件最后 50 行：
   ```bash
   tail -50 ~/Library/Application\ Support/wx-sniffer-mac/app.log
   ```

## 技术细节

### 配置文件结构

新增了 `capture.mitmdump_path` 字段：

```json
{
  "capture": {
    "mitmdump_path": "/path/to/mitmdump",
    ...
  }
}
```

- 如果为空字符串，则使用自动查找逻辑
- 如果指定了路径，会优先使用该路径

### 错误处理流程

1. 用户点击"开始抓取"
2. 前端调用 `POST /api/proxy/start`
3. 后端 `ProxyManager.start()` 执行：
   - 检查操作系统
   - 查找 mitmdump 路径
   - 检查抓包脚本是否存在
   - 启动 mitmdump 进程
   - 等待 1.5 秒检查进程是否存活
   - 如果进程退出，读取 stderr 并返回错误
   - 设置 macOS 系统代理
4. 前端收到响应后：
   - 显示弹窗（成功或失败）
   - 刷新代理状态
   - 刷新日志列表

### 日志记录

所有关键操作都会记录到：
- 文件: `~/Library/Application Support/wx-sniffer-mac/app.log`
- 数据库: `logs` 表
- 页面: 日志区域实时显示
