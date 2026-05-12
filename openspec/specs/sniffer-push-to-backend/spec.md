# sniffer-push-to-backend Specification

## Purpose
TBD - created by archiving change wire-sniffer-to-product-catalog. Update Purpose after archive.
## Requirements
### Requirement: 「同步主系统」推送当前可见商品到后台商品库
wx-sniffer 的「同步主系统」按钮 SHALL 把当前页面上的商品打包为 `{"items": [...]}` POST 到配置的后台地址（`config.sync.base_url + config.sync.sync_path`，默认指向后台的 `/api/products/sniffer/sync-to-catalog`）。「当前页面上的商品」SHALL 定义为：若搜索框有关键字，则推送过滤后可见的商品；否则推送本地库全部商品。

#### Scenario: 无搜索词时推全部
- **WHEN** 搜索框为空，用户点「同步主系统」
- **THEN** wx-sniffer SHALL 把本地库所有商品打包推送到后台

#### Scenario: 有搜索词时只推过滤结果
- **WHEN** 搜索框输入了关键字（例如「乐事」），列表只剩匹配的若干行，用户点「同步主系统」
- **THEN** wx-sniffer SHALL 只把这些匹配的商品打包推送，不推未显示的行

#### Scenario: 报文格式与后台端点一致
- **WHEN** wx-sniffer 构建推送报文
- **THEN** 每个 item SHALL 含 `source_platform / poi_name / sku_id / product_name / upc / spec / origin_price / price / stock / product_pic(数组) / raw_json` 字段，与后台 `/api/products/sniffer/sync-to-catalog` 期望的格式一致

### Requirement: 后台地址可配置
后台地址 SHALL 通过 `config.json` 的 `sync.base_url` 与 `sync.sync_path` 配置；默认值 SHALL 为后台公网入口 + 新端点路径（`base_url = "http://api.fuliops.cn"`、`sync_path = "/api/products/sniffer/sync-to-catalog"`）。已存在的本机 `config.json` SHALL 在升级时被一次性迁移到新默认值（保留用户已自定义的 `base_url`，仅在它仍是旧占位符时才覆盖）。

#### Scenario: 默认配置指向新端点
- **WHEN** 全新安装、首次生成 `config.json`
- **THEN** `sync.sync_path` SHALL 为 `/api/products/sniffer/sync-to-catalog`

#### Scenario: 旧 config 自动迁移
- **WHEN** 本机已有 `config.json` 且其 `sync.sync_path` 仍是旧的 `/api/products/sniffer/sync`（或占位符）
- **THEN** 升级后 SHALL 被改写为 `/api/products/sniffer/sync-to-catalog`；若用户已手动改过 `base_url` 指向真实地址，则 `base_url` SHALL 被保留不动

### Requirement: 推送结果回显
推送完成后，wx-sniffer SHALL 把后台返回的 `created` / `updated`（以及 `variants_total` / `skipped` 若有）回显给用户——既弹一次提示，也写入 UI 日志面板。

#### Scenario: 成功回显
- **WHEN** 推送返回 `{"success": true, "created": 3, "updated": 5, "variants_total": 14, "skipped": 0}`
- **THEN** wx-sniffer SHALL 提示类似「已推送到后台商品库：新建 3 / 更新 5（共 14 个规格）」，并在日志面板写一条 INFO

#### Scenario: 失败回显
- **WHEN** 推送请求超时或后台返回非 2xx
- **THEN** wx-sniffer SHALL 提示失败原因与目标 URL，并在日志面板写一条 ERROR，SHALL NOT 静默吞掉错误

