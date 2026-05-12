## Context

后台仓库（SSH `cenbusi@192.168.1.27:/home/yuandian/backend`，FastAPI + SQLAlchemy + SQLite，uvicorn 跑在 `127.0.0.1:8000`，nginx 把 `api.fuliops.cn` / `boss.fuliops.cn` / `crm.fuliops.cn` 反代到它）已有的相关结构：

- `models.py::SnifferProduct`（表 `sniffer_products`）：字段与 wx-sniffer 本地 SQLite **完全一致**——`source_platform / poi_name / sku_id / product_name / upc / spec / origin_price / price / stock / product_pic(JSON) / raw_json`，唯一键 `(source_platform, poi_name, sku_id)`，外加 `last_synced_at`。注释写「与现有手工货盘隔离」。
- `products.py`（顶层，挂 `prefix="/api/products"`）已有 `/sniffer/stats`、`/sniffer`（列表）、`/sniffer`（POST 单条）、`/sniffer/sync`（POST 批量 upsert 到 `sniffer_products`）、`/sniffer/{id}` CRUD。wx-sniffer 当前 `config.json` 默认 `sync_path = /api/products/sniffer/sync` 正好对应这个——**但它只写 `sniffer_products`，不进商品库**。
- `models.py::ProductMaster` / `ProductVariant` / `ProductMedia` / `ProductAttribute` / `ProductRawPayload`：这是「商品库」的真表。`ProductMaster` 唯一键 `(source_platform, source_store, source_spu_key)`；`ProductVariant` 唯一键 `(master_id, source_sku_key)`，自身有 `sku_id / spec / barcode / original_price / discount_price / stock / weight / monthly_sales ...`。
- `products.py::_upsert_meituan_product_master(db, payload, *, source_store, source_platform="meituan")`：现成的「按 SPU 定位 master → 清空重建 variants/media/attrs/raw → 重填」写法，**就是覆盖式更新**。`/api/products/master`（列表，可按 `source_platform` 过滤）+ `/api/products/master/push-to-meituan` + 牵牛花推送，都吃 `product_masters`。
- 前端 `frontend/src/views/ProductLibrary/ProductMasterManage.vue`：商品库页面，有 `sourcePlatformLabel` 标签映射、`来源平台` 筛选下拉（当前选项 `meituan / eleme / qianniuhua`）、`/api/products/master` 列表渲染。另有 `SnifferManage.vue` 是单独的「抓包商品」页（吃 `/sniffer*`），与商品库分离。

wx-sniffer 侧（本仓库）：`server.py::api_sync_products`（`POST /api/products/sync`）读本地 `list_products()`（可带 `q` 过滤，但当前 sync 没透传），构建 `{"items":[{source_platform, poi_name, sku_id, product_name, upc, spec, origin_price, price, stock, product_pic(list), raw_json}, ...]}`，POST 到 `{config.sync.base_url}{config.sync.sync_path}`，把返回的 created/updated 回显。「同步主系统」按钮调它。

## Goals / Non-Goals

**Goals:**
- wx-sniffer 点「同步主系统」→ 当前可见商品全量进后台**商品库**（`product_masters` / `product_variants`），来源平台 = 「小程序美团」
- 覆盖式更新：以 `(门店名, sku_id)` 为线索，软件最新数据与后台不一致即覆盖
- wx-sniffer 没采集到的商品库字段（重量、品牌、三级分类、月销、最小起购等）留空不报错
- 后台已成型业务代码（`/sniffer/sync`、`/master/*`、`_upsert_meituan_product_master`、商品库页面其它逻辑）**零改动**
- 这套数据进商品库后能直接走现有「推送美团 / 推送牵牛花」流程，不需要额外适配

**Non-Goals:**
- 不重写 wx-sniffer 的抓包/解析逻辑
- 不动 `sniffer_products` 表及其 CRUD（保留，作为可选的原始落地区；本次不依赖它）
- 不做后台→wx-sniffer 的反向同步
- 不实现细粒度的「字段级 diff 报告」（只做整 master 覆盖）
- 不做鉴权改造（沿用 `/sniffer/sync` 的「无 token」现状；公网暴露的安全收紧列为开放问题）
- 不把 wx-sniffer 打包成 .app（那是另一个变更）

## Decisions

### D1：后台新增独立端点 `POST /api/products/sniffer/sync-to-catalog`，不碰旧端点
- 在 `backend/products.py` 里加一个新路由 + 一个 `_upsert_sniffer_product_master(db, item, *, source_store)` 辅助函数。`_upsert_sniffer_product_master` **照抄** `_upsert_meituan_product_master` 的结构（按唯一键查 master → 没有就建 → 清空 `master.variants/media_items/attribute_items/raw_payloads` → 重填 → `db.flush()` → 重建子表），只是把入参从「美团 payload」换成「wx-sniffer 扁平 item」，`source_platform` 硬编码 `"wechat_meituan"`。
- **替代**：扩展 `/sniffer/sync` 让它顺便 promote 到商品库——否决，因为会改既有端点行为，违反「不动业务代码」。
- **替代**：在 `routers/products.py` 而不是顶层 `products.py` 加——两个文件都挂 `prefix="/api/products"`；放 `products.py`（sniffer 系列都在这）更内聚。

### D2：SPU 归并策略——`source_spu_key = slug(product_name)`
- 一个 ProductMaster 对应「同门店 + 同商品名」。`source_store = poi_name`，`source_spu_key = _slugify_title(product_name)`（复用现有 `_slugify_title`），`title = product_name`。多个规格 SKU（如收纳袋 6 个规格）`product_name` 相同 → 归到同一个 master 下的多个 variant。
- 变体定位键：`source_sku_key = sku_id`（直接用 wx-sniffer 抓到的美团 SKU id），`variant.sku_id = sku_id`。
- **为什么不用 upc 当 SPU 键**：wx-sniffer 抓到的多规格商品里 upc 经常只在主 SKU 上有（已实测），用 upc 归并会把缺 upc 的规格漏掉或错并。`_upsert_meituan_product_master` 里有个「barcode 二次匹配」的兜底，我们这版**先不抄那段**（保持简单，纯按 product_name 归并）；如果实测发现同名误并严重再加。
- **代价**：(a) 同店同名但实际不同 SPU → 会被并到一个 master（罕见，可接受）；(b) 某 SKU 改了所属 product_name → 旧 master 残留过期 variant（因为新 master 在新 product_name 下重建，旧 master 没被触碰）。v1 接受，Risks 里列出，后续可加「孤儿 variant 清理」。

### D3：字段映射表（wx-sniffer item → 商品库）

| wx-sniffer 字段 | 商品库目标 | 说明 |
|---|---|---|
| `poi_name` | `ProductMaster.source_store` | 门店名当 source_store |
| `product_name` | `ProductMaster.title` + `source_spu_key=slug(product_name)` | |
| （固定） | `ProductMaster.source_platform = "wechat_meituan"` | 前端显示「小程序美团」 |
| `product_pic[0]` | `ProductMaster.cover_image_url` | 第一张当封面 |
| `product_pic[]` | `ProductMedia(media_type="master_image", url=..., sort_order=i)` | 整组进 media |
| `raw_json` | `ProductRawPayload(payload_kind="sniffer_sync", payload_json=...)` | 原始报文归档 |
| `sku_id` | `ProductVariant.source_sku_key` + `ProductVariant.sku_id` | 变体定位键 |
| `spec` | `ProductVariant.spec` | |
| `upc` | `ProductVariant.barcode` | 可空 |
| `origin_price` | `ProductVariant.original_price` | |
| `price` | `ProductVariant.discount_price`（`None` 当 `price` 为 0/-1/空） | |
| `stock` | `ProductVariant.stock` | |
| —（未采集） | `weight / weight_unit / monthly_sales / min_purchase_qty / brand_name / category_ids / category_path / description / source_tag_*` | 一律 `None` / `[]`，不报错 |
- `ProductMaster.source_payload_summary` 写一个小摘要 `{"picture_count": n, "variant_count": m, "from": "wx-sniffer"}`。
- `ProductMaster.completeness_score` 复用 `_calc_completeness_score`（title/images/variants/category_path）——会偏低（没分类），符合实际，UI 上「详情补全」列会如实显示「分类待补」。
- 返回体：`{"success": true, "created": <新建 master 数>, "updated": <更新 master 数>, "variants_total": <写入的 variant 总数>, "skipped": <因缺 poi_name/sku_id 跳过的 item 数>}`。

### D4：wx-sniffer 侧只动配置 + 同步按钮的「当前可见」语义
- `config.py` `DEFAULT_CONFIG["sync"]` 改：`base_url = "http://api.fuliops.cn"`、`sync_path = "/api/products/sniffer/sync-to-catalog"`、保留 `timeout_seconds`。用户本机已有的 `config.json` 也要同步迁移（一次性，类似之前 mode 的迁移）。
- `server.py::api_sync_products` 增加可选 query `q`：有 `q` 时 `list_products(q)` 只推过滤结果，否则推全部——对齐用户「把当前页面上的商品全部推送」。
- `static/app.js::syncProducts()`：调 `/api/products/sync` 时把当前 `#search-input` 的值作为 `?q=` 带上；成功后 `alert` 显示 `已推送 N 条到后台商品库（新建 X / 更新 Y）`，并 `pushClientLog`。
- 报文格式不变（后台新端点就是按这个格式写的）。

### D5：覆盖式更新 = 整 master 重建
- 每次 sync，对每条 item：定位/创建 master → `master.variants.clear()` 等 → 重新插入所有 variant/media。也就是说「同一次 sync 报文里同 product_name 的多个 SKU」会被一起重建到那个 master 下。
- **注意一个陷阱**：如果用户某次只点了某商品的部分规格（比如只看了 1 个 SKU），这次 sync 报文里那个商品只有 1 个 variant → master 会被重建成只有 1 个 variant，**之前抓到的其它规格会丢**。
  - 缓解：后台 upsert 时，对「报文里出现的 product_name」做「按 sku_id 合并」而不是「整组替换」——即 master 下已有的 variant，若这次报文没带它，**保留**；这次报文带了的，覆盖。这样多次零散抓取能累积。
  - 实现：不调 `master.variants.clear()`，改成遍历报文 variant：存在同 `source_sku_key` 的就更新字段，不存在就新建；media 同理按 url 去重 append。`_upsert_meituan_product_master` 是「整组替换」因为美团 cookie 采集是一次性拉全店；wx-sniffer 是逐个点，必须「增量合并」。**这是和 D1「照抄」的唯一偏离点，design 明确写清。**
- updated vs created 的判定：master 这次是新建的 → created；否则 → updated。

### D6：来源平台标签——前端两行改动
- `ProductMasterManage.vue` 里的 `sourcePlatformLabel`/`platformLabel` 映射对象加 `wechat_meituan: '小程序美团'`。
- 来源平台筛选 `<el-select>` 加 `<el-option label="小程序美团" value="wechat_meituan" />`。
- 不动该页其它任何逻辑（推送、详情、删除、采集面板都不碰）。

## Risks / Trade-offs

- **[同店同名不同 SPU 被合并]** → v1 接受；若实测出现，加 D2 那段「barcode 二次匹配」兜底，或让 `source_spu_key` 带上 upc 后缀。
- **[逐个抓 → 整组替换会丢已抓规格]** → 用 D5 的「增量合并」写法规避；这是必须做的偏离，不能照抄 `_upsert_meituan_product_master` 的 `.clear()`。
- **[新端点无鉴权，公网可达]** → 沿用现状（`/sniffer/sync` 也没鉴权）；如果 `api.fuliops.cn` 真的公网可解析，建议至少加个固定 `X-Sync-Token` 校验——列为开放问题，不阻塞 v1（内网/可信网络先用）。
- **[wx-sniffer 推完不知道后台是否真写进商品库]** → 返回体带 created/updated/variants_total，UI 回显；用户可去商品库页按「来源平台=小程序美团」筛选核对。
- **[`api.fuliops.cn` 在用户 Mac 上解析不到 / 跨网络]** → 部署时确认；备选：配 `sync.base_url` 为局域网 IP（需 uvicorn 绑 0.0.0.0 或 nginx 监听内网网卡）或 SSH 隧道。这是部署配置，不是代码问题。
- **[`completeness_score` 偏低导致商品库页显示「补全度低」]** → 符合事实（wx-sniffer 没采集分类/重量），不是 bug；后续若接「美团商品详情补全」流程可拉高。
- **[wx-sniffer 本地与后台商品库出现「双份真相」]** → 明确：后台商品库是下游推送的真源，wx-sniffer 本地库只是抓取暂存区；不做双向同步。

## Migration Plan

1. 后台：在 `products.py` 加 `_upsert_sniffer_product_master` + `POST /api/products/sniffer/sync-to-catalog` 路由；本地用 `curl` 打一条样例报文验证 master/variant 入库正确（看 `/api/products/master?source_platform=wechat_meituan`）
2. 前端：`ProductMasterManage.vue` 加标签 + 筛选选项；`npm run build`（如果是构建型前端）或热更
3. wx-sniffer：改 `config.py` 默认 + 迁移本机 `config.json`；改 `server.py::api_sync_products` 透传 `q`；改 `static/app.js` 同步按钮带 `q` + 回显
4. 端到端：wx-sniffer 抓几个商品 → 点「同步主系统」→ 后台商品库页按「小程序美团」筛选能看到 → 试着选中推送到一个美团 Cookie / 牵牛花账号，确认下游不报错
5. 回归：抓「部分规格」再 sync 一次，确认之前的规格没被冲掉（验证 D5 增量合并）

**回滚**：新端点是纯增量，删掉路由函数即可；前端两行 revert；wx-sniffer 把 `sync_path` 改回 `/api/products/sniffer/sync` 就退回旧行为。`product_masters` 里 `source_platform="wechat_meituan"` 的行可单独 `DELETE` 清理，不影响美团/饿了么/牵牛花的行。

## Open Questions（实施后已确认 / 留档）

- ~~`api.fuliops.cn` 是否可达？~~ **已确认**：`api.fuliops.cn` 实际是另一个应用（`/home/aiwork/api-module-platform-deploy`），不是后台。后台 uvicorn 只绑 `127.0.0.1:8000`，对外靠 nginx；`server_name boss.fuliops.cn`（也包括 `crm.fuliops.cn`）的 vhost 把 `/api/` 反代到 8000。从运营机器（与后台 `192.168.1.27` 同内网）的可行路径是 **`http://192.168.1.27/api/...` 带 `Host: boss.fuliops.cn` 头**（nginx 默认 vhost 对 `/api/` 返 404，必须带正确 Host）。已在 `config.json` 加 `sync.host_header` 字段实现这个。若以后配了内网 DNS，把 `base_url` 改成域名、`host_header` 留空即可。
- ~~新端点要不要加 `X-Sync-Token`？~~ **本次不加**——后台 `/sniffer/*` 系列现状也无鉴权（且那套挂在死代码 `products.py` 上根本没生效），新端点挂在 `routers/products.py` 上、靠内网隔离。后续若要对外暴露再统一加 token，列为安全收紧待办。
- 「当前页面上的商品」——已实现为「有搜索词推过滤结果、无搜索词推全部」（`server.py::api_sync_products` 透传 `?q=`）。若以后要「勾选行才推」可再加。
- 商品库页给「小程序美团」行加「重新从 wx-sniffer 拉取」按钮——本次不做，列为后续。
