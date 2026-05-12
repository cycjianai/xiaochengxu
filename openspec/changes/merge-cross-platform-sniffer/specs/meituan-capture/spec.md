## ADDED Requirements

### Requirement: 拦截美团商品搜索响应
抓包 addon SHALL 拦截方法为 `POST` 且 URL 路径包含 `/sc/product/retail/r/searchListPageNew` 或 `/reuse/sc/product/retail/r/searchListPageNew` 的响应，并将其作为美团商品列表数据进行解析。

#### Scenario: 命中目标接口
- **WHEN** 上游 mitmproxy 通过的响应满足上述路径且响应 JSON 顶层 `code == 0`
- **THEN** addon SHALL 进入解析分支，把 `data.productList` 当作 SPU 列表处理

#### Scenario: 非目标接口跳过
- **WHEN** 响应路径不匹配上述模式，或 `code != 0`，或响应体非合法 JSON
- **THEN** addon SHALL 立即返回，不做后续处理也不记录错误

### Requirement: SKU 级展开与字段抽取
对每一条 SPU，addon SHALL 遍历其 `wmProductSkus` 列表，按 SKU 展开为独立条目。每条 SKU 条目 SHALL 至少包含以下字段：`source_platform`、`poi_name`、`sku_id`、`product_name`、`upc`、`spec`、`origin_price`、`price`、`stock`、`product_pic`、`raw_json`，并 SHALL 丢弃缺失 `sku_id` 或 `product_name` 的无效条目。

#### Scenario: 单个 SPU 包含多 SKU
- **WHEN** 一条 SPU 含 N 个 SKU
- **THEN** addon SHALL 产出 N 条独立条目，且 `raw_json.spu_id` 字段 SHALL 都指向同一个 SPU id

#### Scenario: 缺关键字段被丢弃
- **WHEN** 某条 SKU 缺少 `id` 或对应 SPU 缺少 `name`
- **THEN** 该条目 SHALL NOT 出现在最终回传列表中

### Requirement: 门店标识与名称推断
addon SHALL 优先从请求体或 query 参数中提取 `wmPoiId`（兼容 `wm_poi_id`、`poiId`），并把它写入每条 SKU 的 `raw_json.wm_poi_id`；店名 SHALL 按 `poiName` → `wmPoiName` → `storeName` → `source_store_name` 的顺序在 SPU/SKU 字段中查找填入 `poi_name`，若全部缺失则回退为 `wmPoiId` 或字面值 `"未知门店"`。

#### Scenario: poiId 在请求体中
- **WHEN** 请求体含 `wmPoiId=123456`
- **THEN** 所有产出条目的 `raw_json.wm_poi_id` SHALL 为 `"123456"`

#### Scenario: 全部店名字段缺失
- **WHEN** SPU/SKU 任一字段都没有店名
- **THEN** 条目 `poi_name` SHALL 等于已抽取的 `wmPoiId`；若 `wmPoiId` 也为空，则 SHALL 等于 `"未知门店"`

### Requirement: 回传本地导入接口
解析得到的条目列表 SHALL 通过 HTTP `POST` 发送到本地 Flask 服务的 `capture.import_path` 配置项指定的 URL（默认 `/api/internal/capture-products`），请求体形如 `{"source": "mitmproxy", "items": [...]}`，且 SHALL 在请求头携带 `X-Capture-Token`（取自 `capture.capture_token` 配置）。

#### Scenario: 成功回传
- **WHEN** 解析得到非空 `items` 列表且 HTTP 请求 2xx 返回
- **THEN** addon SHALL 通过 mitmproxy 日志记录一行包含 `items 数量`、`首条 poi_name` 的 info 日志

#### Scenario: 回传失败容错
- **WHEN** 回传 HTTP 请求超时或非 2xx
- **THEN** addon SHALL 捕获异常并写 error 日志，且 SHALL NOT 让 mitmproxy 主流程崩溃；当次抓到的数据本次允许丢弃，不要求重试队列

### Requirement: 后端导入接口校验
本地 Flask 端点（`server.py` 中的 `/api/internal/capture-products`）SHALL 校验请求头 `X-Capture-Token` 与本地配置 `capture.capture_token` 一致；不一致时 SHALL 返回 HTTP 401。

#### Scenario: token 缺失或错误
- **WHEN** 请求头未带 `X-Capture-Token` 或值不匹配
- **THEN** 服务 SHALL 返回 401 且不进入数据落库逻辑

#### Scenario: token 正确
- **WHEN** 请求头 token 与配置一致
- **THEN** 服务 SHALL 解析 `items` 并写入本地 SQLite，向调用方返回 `{"ok": true, "imported": <count>}`
