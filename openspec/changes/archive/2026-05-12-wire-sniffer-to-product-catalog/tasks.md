## 1. 后台：新增「抓包商品 → 商品库」端点

- [x] 1.1 在 `backend/products.py` 加 `class SnifferCatalogSyncBody(BaseModel)`（`items: list[SnifferProductCreate]`，可复用现有的）
- [x] 1.2 加辅助函数 `_upsert_sniffer_product_master(db, item: dict, *, source_store: str) -> tuple[str, ProductMaster]`：参照 `_upsert_meituan_product_master` 的结构，但 `source_platform="wechat_meituan"`、`source_spu_key=_slugify_title(item["product_name"])`、`title=item["product_name"]`、`cover_image_url=product_pic[0]`
- [x] 1.3 在 1.2 里实现 **增量合并**（关键，区别于 `_upsert_meituan_product_master` 的整组替换）：不调 `master.variants.clear()`；遍历报文 variant，按 `source_sku_key==sku_id` 找已有 variant 则覆盖字段，否则 `db.add` 新建；`product_media` 按 url 去重 append；`product_raw_payloads` append 一条 `payload_kind="sniffer_sync"`
- [x] 1.4 在 1.2 里写字段映射：`upc→barcode`、`origin_price→original_price`、`price→discount_price`（`price` 为 0/-1/空时置 `None`）、`stock→stock`、`spec→spec`；未采集字段（`weight/weight_unit/monthly_sales/min_purchase_qty/brand_name/category_*/description/source_tag_*`）一律 `None`/`[]`，不报错
- [x] 1.5 加路由 `@router.post("/sniffer/sync-to-catalog")`：遍历 items，跳过 `poi_name`/`sku_id` 为空的（计 `skipped`），其余调 `_upsert_sniffer_product_master`；统计新建/更新的 master 数与写入的 variant 总数；`db.commit()`；返回 `{"success": True, "created": ..., "updated": ..., "variants_total": ..., "skipped": ...}`
- [x] 1.6 `curl` 本地验证：POST 一条含 2 个 SKU（一个有 upc 一个没有）的样例报文到 `http://127.0.0.1:8000/api/products/sniffer/sync-to-catalog`，确认返回 created=1 variants_total=2；再 `GET /api/products/master?source_platform=wechat_meituan` 看到该 master + 2 个 variant
- [x] 1.7 回归验证既有端点：`POST /api/products/sniffer/sync` 行为不变（仍只写 `sniffer_products`），`GET /api/products/master`（不带 source_platform）不受影响

## 2. 后台前端：商品库页加「小程序美团」

- [x] 2.1 `frontend/src/views/ProductLibrary/ProductMasterManage.vue`：来源平台标签映射对象加 `wechat_meituan: '小程序美团'`（看 `platformLabel` / `sourcePlatformLabel` 计算逻辑，第 ~507 行附近）
- [x] 2.2 同文件：来源平台筛选 `<el-select>` 加 `<el-option label="小程序美团" value="wechat_meituan" />`（第 ~42-44 行附近）
- [x] 2.3 不动该页其它逻辑；前端构建（`npm run build` 或对应流程）并验证：筛选「小程序美团」能查到 1.6 写入的样例商品，来源平台列显示中文「小程序美团」

## 3. wx-sniffer：同步按钮指向新端点

- [x] 3.1 `config.py` `DEFAULT_CONFIG["sync"]` 改：`base_url = "http://api.fuliops.cn"`、`sync_path = "/api/products/sniffer/sync-to-catalog"`、`timeout_seconds` 保留
- [x] 3.2 在 `config.py::load_config()`（或 `migrate_legacy_data` 附近）加一次性迁移：若本机 `config.json` 的 `sync.sync_path` 仍是 `/api/products/sniffer/sync` 或空 → 改成新值；若 `sync.base_url` 仍是占位符 `http://127.0.0.1:8000` → 改成新默认；用户已自定义的 `base_url` 不动
- [x] 3.3 `server.py::api_sync_products`：接受可选 query `q`，有 `q` 时 `list_products(q)`，否则 `list_products()`；其余构建报文逻辑不变
- [x] 3.4 `static/app.js::syncProducts()`：POST `/api/products/sync` 时把 `#search-input` 的值作为 `?q=` 带上（为空就不带）
- [x] 3.5 `static/app.js::syncProducts()`：成功后 `alert` 显示「已推送到后台商品库：新建 X / 更新 Y（共 N 个规格）」并 `pushClientLog('INFO', ...)`；失败显示原因 + 目标 URL 并 `pushClientLog('ERROR', ...)`

## 4. 端到端 & 回归

- [x] 4.1 wx-sniffer 抓 3-5 个商品（含一个多规格）→ 点「同步主系统」→ 看 UI 日志面板有「已推送...新建 X / 更新 Y」
- [x] 4.2 后台商品库页按「来源平台 = 小程序美团」筛选 → 看到刚推的商品，规格/价格/图片/UPC 与 wx-sniffer 一致
- [x] 4.3 改 wx-sniffer 本地某商品的售价 → 再点「同步主系统」→ 后台对应 variant 的 `discount_price` 被覆盖、master 计为 updated
- [x] 4.4 抓某商品的「部分规格」再 sync → 确认后台该 master 下之前的规格没被冲掉（验证增量合并）
- [ ] 4.5 （待用户实操：选一个「小程序美团」商品走推送美团/牵牛花，确认下游不报错）在商品库页选中一个「小程序美团」商品，走「推送到美团」/「推送到牵牛花」流程，确认下游不因 source_platform 不同而报错
- [x] 4.6 在搜索框输入关键字后点「同步主系统」→ 确认只推了过滤结果

## 5. 收尾

- [x] 5.1 更新 wx-sniffer `README.md`：补「同步主系统 = 推到后台商品库（来源平台：小程序美团）」「配置 sync.base_url / sync_path」说明
- [x] 5.2 在 `design.md` 的 Open Questions 里逐条确认/留档：`api.fuliops.cn` 在运营 Mac 上是否可达、是否要加 `X-Sync-Token`
- [ ] 5.3 `openspec archive wire-sniffer-to-product-catalog`
