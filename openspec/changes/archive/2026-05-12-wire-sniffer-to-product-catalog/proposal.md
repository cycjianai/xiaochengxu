## Why

wx-sniffer 现在能稳定抓到微信小程序里的美团商品并落本地 SQLite，但数据停在本地——业务上真正要的是把这套商品资料喂进「原点闪购系统」后台的**商品库**（`product_masters` / `product_variants`），因为后台的商品库板块才有「推送到美团」「推送到牵牛花」的下游能力。当前后台虽然已经有一个隔离的 `sniffer_products` 落地表 + `/api/products/sniffer/sync` 端点，但那张表不在商品库页面里，也没法被推到美团/牵牛花。

需要打通的就是这一段「软件 → 商品库」的通道，让 wx-sniffer 点「同步主系统」时把当前商品全量推进商品库，来源平台标记为「小程序美团」，并按 `(门店, sku_id)` 做覆盖式更新。**后台已成型的业务代码不动**，只新增一条接收通道。

## What Changes

- **后台（`/home/yuandian/backend`）新增一个接收端点**：`POST /api/products/sniffer/sync-to-catalog`
  - 接收 wx-sniffer 现有 `{"items": [...]}` 报文（字段：`source_platform / poi_name / sku_id / product_name / upc / spec / origin_price / price / stock / product_pic[] / raw_json`）
  - 把数据 upsert 进 `product_masters` + `product_variants`（+ `product_media` + `product_raw_payloads`），复用现有 `_upsert_meituan_product_master` 同款写法，但 `source_platform = "wechat_meituan"`
  - 按 `(source_platform, source_store=门店名, source_spu_key=商品名归一)` 定位 master；按 `sku_id` 定位 variant；同批重建 master 下的 variants/media —— 即「软件最新数据 vs 后台不一致 → 覆盖」
  - 字段映射：商品名→`title`，门店名→`source_store`，`upc`→`variant.barcode`，`origin_price`→`variant.original_price`，`price`→`variant.discount_price`，`stock`→`variant.stock`，`spec`→`variant.spec`，`product_pic[]`→`product_media`（`master_image` / `sku_image`），`raw_json`→`product_raw_payloads(payload_kind="sniffer_sync")`；wx-sniffer 没采集到的字段（重量、品牌、三级分类、月销等）留空，**不报错、照样入库**
  - **不修改** `/api/products/sniffer/sync`、`/api/products/master/*`、`_upsert_meituan_product_master` 等既有路由/函数
- **前端商品库页（`frontend/src/views/ProductLibrary/ProductMasterManage.vue`）极小改动**：
  - 来源平台标签映射加一项 `wechat_meituan → "小程序美团"`
  - 来源平台筛选下拉加一个 `<el-option label="小程序美团" value="wechat_meituan" />`
  - 不动该页其它逻辑
- **wx-sniffer 侧改动**：
  - `config.json` 的 `sync.base_url` 改成后台公网地址（`http://api.fuliops.cn` 经 nginx 转 `127.0.0.1:8000`），`sync.sync_path` 改成 `/api/products/sniffer/sync-to-catalog`
  - 「同步主系统」按钮逻辑：把当前列表（含搜索过滤后的可见行；若无过滤则全量）打包推到后台。现有 `server.py::/api/products/sync` 已经构建 `{"items":[...]}` 报文，只需让它支持「带搜索关键字时只推过滤结果」并指向新端点
  - 推送结果（created / updated）回显到 UI 日志面板

## Capabilities

### New Capabilities
- `catalog-sync-endpoint`：后台新增的「接收 wx-sniffer 商品并写入商品库」HTTP 端点的契约——报文格式、字段映射、master/variant upsert 与覆盖式更新语义、来源平台标记、对未采集字段的容错
- `sniffer-push-to-backend`：wx-sniffer 侧「同步主系统」推送行为的契约——推什么（当前可见商品）、推到哪（可配置后台地址 + 新端点）、推送结果回显

### Modified Capabilities
<!-- wx-sniffer 项目尚未沉淀 openspec/specs/；后台不在本仓库 openspec 管辖内，其改动以 design.md 文字约束记录 -->

## Impact

- 后台代码：`backend/products.py`（或 `backend/routers/products.py`）新增一个路由函数 + 一个 `_upsert_sniffer_product_master` 辅助函数；`backend/main.py` 无需改（路由已挂在 `products_router`）
- 后台 DB：无新表（复用 `product_masters` / `product_variants` / `product_media` / `product_raw_payloads`），仅多了 `source_platform="wechat_meituan"` 的行
- 前端：`ProductMasterManage.vue` 两处微调（标签映射 + 筛选下拉）
- wx-sniffer：`config.py` 默认配置中 `sync.*`；`server.py::api_sync_products` 增加可选 `q` 透传；`static/app.js` 同步按钮把当前搜索词带上
- 网络：依赖 `api.fuliops.cn`（nginx → uvicorn 127.0.0.1:8000）可达；Mac 与后台需在同一网络或公网可解析该域名
- 鉴权：现有 `/sniffer/sync` 没有鉴权，新端点保持一致（内网/可信网络使用）；若后续要对外暴露需补 token，列为 design 的开放问题
- 风险：商品名作为 `source_spu_key` 的一部分——若同店同名但实际是不同 SPU，会被合并到一个 master；若 SKU 改了所属商品名，旧 master 会残留过期 variant。v1 接受，design 里记录
