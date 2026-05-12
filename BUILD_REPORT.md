# 构建完成报告

## ✅ 已完成的操作

### 1. 配置目录创建
```
~/Library/Application Support/wx-sniffer-mac/
```
- ✅ 目录已创建
- ✅ config.json 已生成

### 2. 旧构建产物清理
```
rm -rf build dist
```
- ✅ 已清理

### 3. macOS 应用打包
```
bash scripts/build_mac.sh
```
- ✅ 打包成功
- ✅ 使用 Python 3.9
- ✅ 所有依赖已安装
- ✅ PyInstaller 构建完成

### 4. 产物验证
- ✅ `dist/wx-sniffer-mac.app` - 14MB
- ✅ `dist/wx-sniffer-mac` - 可执行文件
- ✅ mitmdump 路径存在: `/Users/cenbusi/Desktop/wx-sniffer-mac/.venv-build/bin/mitmdump`

## 📋 配置文件内容

位置: `~/Library/Application Support/wx-sniffer-mac/config.json`

```json
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
```

## 🚀 下一步：测试应用

### 方法 1: 通过 Finder 启动
```bash
open dist/wx-sniffer-mac.app
```

### 方法 2: 直接运行可执行文件
```bash
./dist/wx-sniffer-mac
```

## 🧪 测试步骤

1. **启动应用**
   - 应该会打开一个窗口或浏览器
   - 显示登录页面

2. **登录**
   - 用户名: `admin`
   - 密码: `admin123`

3. **测试代理功能**
   - 点击"开始抓取"按钮
   - 观察页面顶部的代理状态
   - 如果成功，应该显示"运行中"
   - 如果失败，会弹窗显示详细错误信息

4. **查看日志**
   - 页面底部的日志区域会显示操作记录
   - 或查看文件: `~/Library/Application Support/wx-sniffer-mac/app.log`

## 🔍 故障排查

### 如果"开始抓取"失败

1. **查看弹窗错误信息**
   - 会显示具体的失败原因

2. **查看页面顶部状态**
   - 会显示代理状态和错误提示

3. **查看日志文件**
   ```bash
   tail -50 ~/Library/Application\ Support/wx-sniffer-mac/app.log
   ```

4. **检查 mitmdump 是否可用**
   ```bash
   /Users/cenbusi/Desktop/wx-sniffer-mac/.venv-build/bin/mitmdump --version
   ```

5. **检查端口是否被占用**
   ```bash
   lsof -i :8899
   ```

## 📝 已实现的改进

### 1. 数据目录标准化
- 所有数据存放在 macOS 标准位置
- 不会污染项目目录或 .app 包

### 2. mitmdump 智能查找
- 优先使用配置文件指定的路径
- 自动查找系统 PATH
- 回退到常见安装位置

### 3. 详细错误提示
- 找不到 mitmdump 时提供安装指引
- 启动失败时显示具体原因
- 捕获并显示 stderr 输出

### 4. 日志记录
- 所有关键操作都记录到日志
- 错误信息同时显示在界面和日志中

## 🎯 预期行为

### 成功场景
- 点击"开始抓取" → 弹窗提示"mitmproxy 与 macOS 系统代理已启动"
- 页面顶部显示"代理状态：运行中"
- 日志显示"mitmproxy 已启动，监听 127.0.0.1:8899"

### 失败场景（会有明确提示）
- mitmdump 不存在 → 弹窗提示安装方法
- 端口被占用 → 弹窗提示端口冲突
- 权限不足 → 弹窗提示权限问题
- 脚本缺失 → 弹窗提示文件路径

## 📦 创建 DMG（可选）

如果需要分发 DMG 安装包：

```bash
hdiutil create -volname wx-sniffer-mac \
  -srcfolder "/Users/cenbusi/Desktop/wx-sniffer-mac/dist/wx-sniffer-mac.app" \
  -ov -format UDZO \
  "/Users/cenbusi/Desktop/wx-sniffer-mac/dist/wx-sniffer-mac.dmg"
```

## 🔧 修改配置

如果需要修改配置（如更换 mitmdump 路径）：

```bash
nano ~/Library/Application\ Support/wx-sniffer-mac/config.json
```

修改后重启应用即可生效。
