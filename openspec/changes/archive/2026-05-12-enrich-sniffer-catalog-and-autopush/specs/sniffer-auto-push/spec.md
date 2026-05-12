## ADDED Requirements

### Requirement: 抓一条立刻自动上传后台商品库
wx-sniffer 的 `/api/internal/capture-products` 端点（mitmproxy addon 抓到商品后回传的入口）SHALL 在把商品写入本地 SQLite 后，**立刻**自动把这批商品 POST 到后台商品库端点（`config.sync.base_url + config.sync.sync_path`，默认 `/api/products/sniffer/sync-to-catalog`，带 `config.sync.host_header` 作为 `Host` 请求头）。用户 SHALL NOT 需要手动点任何「同步」按钮。

#### Scenario: 抓到商品自动入库 + 自动上传
- **WHEN** addon 抓到一个商品并 POST 到 `/api/internal/capture-products`
- **THEN** wx-sniffer SHALL 先写本地 SQLite，再自动 POST 到后台商品库，日志面板 SHALL 先后出现「抓包导入完成: created=X」和「已自动上传后台商品库：新建 X / 更新 Y（共 N 个规格）」

#### Scenario: 上传失败不影响本地
- **WHEN** 自动上传后台时网络失败 / 后台返回非 2xx
- **THEN** wx-sniffer SHALL 在日志面板写一条 WARN（含错误与目标 URL），商品 SHALL 已经存在本地 SQLite，不丢数据，不报错给 addon

#### Scenario: 转发完整字段
- **WHEN** addon 回传的 item 已含 `category_ids/category_path/detail_images/attributes/monthly_sales/brand_name/spu_id/weight/...` 等完整字段
- **THEN** 自动上传时 SHALL 把这些字段原样转发给后台（不丢字段）

### Requirement: 保留无按钮的批量重推与回填端点
`POST /api/products/sync`（带可选 `?q=` 过滤）和 `POST /api/products/backfill-upc` SHALL 仍可调用（无 UI 按钮，供需要时手动重推 / 回填）。`/api/products/sync` SHALL 复用与 auto-push 相同的「POST 到后台」逻辑（`_post_items_to_backend`），并 SHALL 在 raw_json 里没分类时按 spu_id 去持久分类缓存（`category_cache.json`）反查兜底。

#### Scenario: 手动批量重推
- **WHEN** 调用 `POST /api/products/sync`（无 `q`）
- **THEN** SHALL 把本地库所有商品（raw_json 没分类的按 spu_id 查持久缓存补上）POST 到后台商品库，返回 created/updated/variants_total

#### Scenario: 带搜索词只重推过滤结果
- **WHEN** 调用 `POST /api/products/sync?q=乐事`
- **THEN** SHALL 只推商品名/门店名/UPC/sku 含「乐事」的商品

### Requirement: 后台地址配置
后台地址 SHALL 由 `config.json` 的 `sync` 段配置（`base_url` / `host_header` / `sync_path` / `timeout_seconds`），默认 `base_url=http://192.168.1.27`、`host_header=boss.fuliops.cn`、`sync_path=/api/products/sniffer/sync-to-catalog`。auto-push 与手动重推 SHALL 都用这一份配置。

#### Scenario: 改后台地址
- **WHEN** 用户把 `config.json` 的 `sync.base_url` 改成内网域名、`host_header` 清空
- **THEN** auto-push 与 `/api/products/sync` SHALL 都改用新地址，不带 `Host` 头
