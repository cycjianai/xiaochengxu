# wx-sniffer

跨平台的"微信小程序 + 美团商品"抓包同步工具。

- 拦截微信桌面端美团相关小程序里的商品详情接口
- 解析 SKU / 价格 / 库存 / 门店等字段，落本地 SQLite
- 一键推送到主系统的商品同步接口（替代人工建新品）

## 技术选型

| 层 | 用什么 | 为什么 |
|---|---|---|
| GUI 壳 | pywebview | 一份 HTML+JS，macOS/Windows 双平台直接套原生 WebKit/WebView2 |
| 后端 | Flask | 简单、零依赖第三方运行时 |
| 抓包内核 | mitmproxy 11.x（**`regular` 模式**） | 朴素 HTTP 代理；通过 `networksetup`（macOS）/ 注册表 `Internet Settings`（Windows）切换系统代理；对 WeChat/微信小程序侵入性最小、不需要 WireGuard 客户端、不需要 macOS 系统扩展 |
| 解析层 | mitmproxy addon `MeituanCaptureAddon` | 进程内 `master.addons.add(...)` 注入，不暴露脚本路径 |
| 打包 | PyInstaller 单 `.spec`，平台分支 | macOS 走 `BUNDLE` → `.app`；Windows 走 `COLLECT(contents_directory="runtime")` → 一目录化 `.exe` |

## 数据流

```
微信小程序点商品
  → 系统代理 → mitmproxy 拦截
  → MeituanCaptureAddon 解析（sku/价格/月销/详情图/属性 + 缓存里的三级分类）
  → POST /api/internal/capture-products (本地 Flask)
  → SQLite 入库
  → 立刻自动 POST 到后台商品库 /api/products/sniffer/sync-to-catalog（抓一条传一条）
  → UI 每 3s 自动刷新表格、日志面板每 2s 刷新
```

UI 极简：**没有任何抓取按钮** —— 软件打开即自动开代理开始抓取，关闭软件自动关代理并还原系统代理设置。顶栏只有「代理：抓取中/未运行」「环境健康」两个状态徽章；下面是商品表格（含「月销」列、每 3s 自动刷新）+ 日志面板（每 2s 刷新）+ 一个手动「新增商品」。**不需要手动点任何东西** —— 抓到的商品会立刻自动上传后台。

## 拦截规则（FILTER_PATHS）

字符串子串匹配（兼容美团 API 版本号变更）：

```
/poi/product/info     /poi/product/detail
/product/info         /product/detail
/product/spu/detail   /sku/detail
```

只解析 host 命中 `*.meituan.com / *.meituan.net / *.sankuai.com / *.dianping.com` 的请求（`allow_hosts` 限制）。其它域名 TLS passthrough，不解密、不影响。

## 第一次安装

> macOS 上 mitmproxy 的根证书必须装到 **admin 域**，不然 WeChat 这种 Chromium-based 应用不认。

1. 把 `wx-sniffer/` 整个目录拖到任意位置（例如 `/Applications` 同级）
2. 建 venv 装依赖（生产分发会换成打包好的 `.app`，开发期需要这一步）：
   ```bash
   /opt/homebrew/bin/python3.12 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```
3. 启动：
   ```bash
   .venv/bin/python app.py
   ```
   软件一开就**自动开代理开始抓取**。**第一次启动会弹一次系统密码框**（macOS Authorization Services），输入开机密码即可 —— 这是把 mitmproxy CA 装进 admin trust 的一次性步骤，**之后任何时候都不会再弹**。
4. 看顶栏「代理：抓取中」绿标 + 「环境健康」绿标亮起，就可以去微信里点商品了。

## 日常使用

1. 打开本软件（代理自动开）
2. 打开**微信桌面版**，进任意美团小程序（美团闪购、外卖商家版、大众点评、好旺角食材、Q必达、祥瑞配送商等）
3. **先在店里刷一下分类页**（点几个左侧分类、滚一下列表）—— 看日志面板出 `[分类缓存] ... → 缓存 N 个 SPU 的三级分类`，这步是为了拿到三级分类（分类只在列表接口里，单品详情接口没有）
4. **点进单品的详情页** —— 日志面板出 `[请求] POST https://...` → `[#N] ... items=M 分类=xxx/yyy/zzz`，然后 `已自动上传后台商品库：新建 X / 更新 Y`
5. 商品列表 3 秒内自动出现新条目；**全程不用点任何按钮** —— 抓到一条就自动上传后台商品库了
6. 用完**关掉本软件窗口** —— 代理自动关、系统代理设置自动还原

## 自动上传后台商品库

抓到的每条商品会**立刻自动**推到「原点闪购系统」后台的**商品库**（`product_masters` / `product_variants`），来源平台标记 `wechat_meituan` → 后台商品库页显示「**小程序美团**」、按「同步时间」降序排（最新抓的在最上面）。推进去之后就能走后台现有的「推送到美团」「推送到牵牛花」流程。

- **覆盖式更新**：以 `(门店名, sku_id)` 为线索。同一商品再次抓到、字段有变 → 后台对应规格被覆盖；多次零散抓不同规格 → 在同一商品下累积，不互相冲掉
- **三级分类的两段式抓取**：进店浏览商品列表（`quickbuy/v1/poi/sputag/products` 等）时缓存 `{spu_id → 三级分类}` 并**持久化到磁盘**（`category_cache.json`，跨重启/会话累积）；点进单品时把分类匹配进来。**先刷分类页再点商品**才有分类；如果点商品时分类没缓存到，日志会提示「先进店刷一下分类」。
- **从小程序接口能拿到的字段都会带上**：
  - **三级分类**（`product_spu_list[].standardCategorys` 的 level 1/2/3 → `category_ids` / `category_path`）
  - **月销**（取自 `data.month_saled` 数字字段；该字段常为 0，会回退解析 `data.month_saled_content` 文案「月售100+」→ 100。UI 表格的「月销」列就是它，比库存有代表性）
  - **详情图 / 图文详情**（`data.pic_content.contents` → 后台 `ProductMedia(detail_image)`）
  - **结构化属性 + 品牌名 + 资质文字**（`data.standard_productinfo_list` → 后台 `ProductAttribute`；含「注册证编号」「生产企业」这类资质归到「资质」组；「品牌」字段同时填 `brand_name`）
  - 重量+单位（`sku.spec_num_unit_string`）、起购数（`min_order_count`，"10个起购"那类会取到 10）、真实美团 SPU ID（`sku.spu_id` → `source_product_id`）、规格全称（`combine_spec`）、商品描述、轮播主图（`opt_pictures`）
- **小程序买家侧接口确实拿不到的**：**资质证照图片**（只给资质文字，没图片）、**部分商品的品牌名**（`standard_productinfo_list` 没「品牌」字段时拿不到，只有 `brand_id`）、**UPC**（商家没在后台填条码时接口返回空字符串，没法补）。后台「补全度」可能仍偏低（因为没有商家后台才有的某些字段），属正常。
- 上传结果（新建 X / 更新 Y / 共 N 个规格）写进 UI 日志面板；上传失败（网络）会写 WARN，数据已存本地，随下次抓取会再传
- **保留的端点（无按钮，可手动调用）**：`POST /api/products/sync`（带可选 `?q=` 批量重推本地库）、`POST /api/products/backfill-upc`（多规格商品按兄弟 SKU 回填 UPC，默认不自动跑）

后台地址在 `config.json` 的 `sync` 段配置：

```json
"sync": {
  "base_url": "http://192.168.1.27",          // 后台所在机器（运营机与后台同内网）
  "host_header": "boss.fuliops.cn",            // nginx 默认 vhost 对 /api/ 返 404，必须带这个 Host 头才路由到 backend
  "sync_path": "/api/products/sniffer/sync-to-catalog",
  "timeout_seconds": 20
}
```

> 后台 uvicorn 只绑 `127.0.0.1:8000`，对外靠 nginx。`boss.fuliops.cn` 这个 server_name 的 vhost 把 `/api/` 反代到 8000，所以请求要发到 `http://<后台IP>/api/...` 并带 `Host: boss.fuliops.cn`。如果以后配了内网 DNS，把 `base_url` 改成那个域名、`host_header` 留空即可。

## 关闭 / 代理一直开着会不会有影响

- **关闭软件窗口** → 自动关代理、还原系统代理设置（`atexit` + 窗口关闭后的清理）
- **代理一直开着会不会影响 VPN / 微信 / 办公软件？** —— 基本不会：
  - VPN 工作在路由层（比 HTTP 代理低一层），VPN 隧道本身不走 HTTP 代理，开着代理不会断 VPN
  - mitmproxy 只对**美团/点评域名**解密（`allow_hosts` 限制），其它所有域名是 **TLS 透传**（原样转发原始 TLS 字节、不解密，应用拿到的还是真服务器的真证书），所以微信/浏览器/办公软件完全正常
  - 性能开销很小（多绕一跳本地回环）
- **唯一要注意的**：如果软件被强杀 / 系统重启时来不及清理，可能留下个指向 `127.0.0.1:8899` 的系统代理设置 —— 这时 HTTP 流量会断。**修法**：再开一次本软件就自愈了（它会重新在 8899 起 mitmproxy）；或者去 系统设置 → 网络 → 当前网络 → 代理 把「Web 代理 / 安全 Web 代理」关掉。下次正常关窗口不会有这个问题。
- 启动时如果 mitmproxy 起不来（如 8899 被占），软件会**主动清掉残留的系统代理设置**，不会把你的内存断网撂在那。

## 配置文件

平台规范位置：

- macOS: `~/Library/Application Support/wx-sniffer/`
- Windows: `%LOCALAPPDATA%\wx-sniffer\`

里面：

| 文件 | 内容 |
|---|---|
| `config.json` | 端口、模式、capture_token、目标域名、本机同步后台 URL |
| `data.db` | 商品 + 日志 SQLite |
| `logs/app.log` | mitmproxy + Flask 完整日志（含 DEBUG，方便排查新 endpoint） |
| `mitmproxy/mitmproxy-ca-cert.pem` | 由 mitmproxy 自动生成的根证书 |

## 拦截不到新接口时怎么办

美团偶尔会升级 API 路径。如果你点商品但 UI 日志面板没出现 `[请求]` 行：

```bash
grep -iE "POST.*(product|goods|sku|detail|spu)" \
  ~/Library/Application\ Support/wx-sniffer/logs/app.log | tail -20
```

把里面新的子串补进 `capture/meituan_addon.py:FILTER_PATHS`，重启即可。

## 跨平台构建

构建产物默认叫 `MTCenter`（可用环境变量 `WX_SNIFFER_PRODUCT_NAME` 覆盖）。

```bash
# macOS
scripts/build_mac.sh         # → dist/MTCenter.app

# Windows
scripts\build_win.ps1        # → dist\MTCenter\
```

## 关键文件

| 路径 | 说明 |
|---|---|
| `app.py` | 桌面壳入口（Flask thread + pywebview） |
| `server.py` | Flask 路由 + `/api/internal/capture-products` |
| `platform_paths.py` | macOS/Windows 用户数据目录抽象 |
| `platform_proxy/__init__.py` | `ProxyManager` 工厂，按平台分派 |
| `platform_proxy/base.py` | 进程内 mitmproxy 启动 + 模式降级 |
| `platform_proxy/macos.py` | `networksetup` 切换 macOS 系统代理 |
| `platform_proxy/windows.py` | WinINET 注册表切换 Windows 系统代理 |
| `capture/meituan_addon.py` | **mitmproxy addon — 拦截 + 解析的核心** |
| `capture/http_client.py` | curl_cffi 优先、requests 降级的回传 client |
| `cert_installer.py` | macOS keychain / Windows CertStore 自动信任 + admin 域权限弹框 |
| `anti_detection.py` | 健康自检：证书信任、提权状态、冲突工具检测 |
| `wx-sniffer.spec` | PyInstaller 单 spec，平台分支 |

## 默认本地账号

登录模块**已移除**。打开页面直接进，单机本地不接外网鉴权。

## 反爬 / 反检测

- mitmproxy 在**应用进程内**通过 `DumpMaster` 启动，外部进程列表里**看不到 `mitmdump` 子进程**
- 回传后台的 HTTP 请求优先用 `curl_cffi`（impersonate Chrome 120 的 JA3），不可用降级 requests 时一次性 WARN
- 产物名默认 `MTCenter`，PyInstaller 资源目录从 `_internal/` 改为 `runtime/`，降低与公开 PyInstaller 默认签名的关联

如果之后被美团风控盯上，第一步是看 `~/Library/Application Support/wx-sniffer/logs/app.log` 里有没有 TLS 错误/接口异常返回；第二步把 `FILTER_PATHS` 和 `target_hosts` 按当前真实流量调整。
