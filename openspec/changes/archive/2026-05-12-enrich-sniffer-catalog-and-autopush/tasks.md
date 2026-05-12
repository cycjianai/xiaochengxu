## 1. wx-sniffer addon：两段式分类 + 详情多字段解析

- [x] 1.1 `capture/meituan_addon.py` 加 `LISTING_PATHS`（`/poi/sputag/products`、`/poi/product/smooth/render` 等）+ `_is_listing()`
- [x] 1.2 `_cache_categories_from_listing(payload)`：遍历 `product_spu_list`，`_parse_categories(spu)`（按 level 升序 → ids/path），存进 `OrderedDict` 缓存（FIFO 上限 5000）
- [x] 1.3 持久化：`_category_cache_path()`（→ `app_data_dir()/category_cache.json`）、`_load_category_cache()`（`__init__` 时加载）、`_save_category_cache()`（每次列表缓存有新增就写盘，原子 replace）
- [x] 1.4 `_parse_meituan_payload(payload, category_cache=None)`：用 `data.id` / `sku.spu_id` 在缓存里查三级分类附到每个 item
- [x] 1.5 详情多字段：`_parse_detail_images`（`pic_content.contents`，去 query）、`_parse_attributes`（`standard_productinfo_list` → `[{group,key,value}]`，资质组检测 + 抽 `brand_name`）、`_parse_videos`、`_read_weight`（`spec_num_unit_string`）；item 加 `spu_id/variant_title/description/brand_name/weight/weight_unit/min_purchase_qty/detail_images/attributes/videos/category_ids/category_path`
- [x] 1.6 月销：`_parse_sales_text`（"月售100+"→100，"X人想买"→None）、`_resolve_monthly_sales`（数字非0优先，否则文案解析）；item 的 `monthly_sales` 用它
- [x] 1.7 诊断模式 `WX_SNIFFER_DISCOVER=1`：`_discover_dump` 把候选接口连响应体写 `discover.jsonl`，非匹配接口 URL 进日志面板（默认关闭）
- [x] 1.8 `response()` hook：列表接口 → `_cache_categories_from_listing`；详情接口 → 解析 + 回传（带 category_cache）

## 2. wx-sniffer 本地 DB

- [x] 2.1 `db.py::init_db`：`products` 表加 `monthly_sales INTEGER` 列（`ALTER TABLE ... ADD COLUMN`，try/except idempotent）
- [x] 2.2 `upsert_product` / `import_captured_products`：处理 `monthly_sales`（`_coerce_monthly_sales`），3 条 SQL（带 product_id 的 UPDATE、existing 的 UPDATE、INSERT）都带上
- [x] 2.3 `_row_to_product`：列里 `monthly_sales` 为 0/空时，用 `_resolve_ms_from_raw` 从 raw_json 的 `month_saled_content` 现解析兜底

## 3. wx-sniffer server：抓一条传一条

- [x] 3.1 `server.py` 加 `_post_items_to_backend(items)`：读 `config.sync`，POST `{"items": items}` 到 `base_url+sync_path`（带 `Host: host_header`），返回后台返回体或 `{success:False,error,target_url}`
- [x] 3.2 `api_internal_capture_products`：`import_captured_products` 入库后立刻 `_post_items_to_backend(items)`，结果写 `add_log`（成功「已自动上传后台商品库：新建 X / 更新 Y」，失败 WARN）
- [x] 3.3 `api_sync_products`（批量重推，保留无按钮）：改用 `_post_items_to_backend`；传递 `monthly_sales/brand_name/category_ids/category_path/detail_images/attributes`；raw_json 没分类时按 spu_id 查 `category_cache.json` 反查（`_load_category_cache_for_sync`）
- [x] 3.4 模拟一次 `POST /api/internal/capture-products`（带 token + 完整 item）验证 auto-push 到后台、字段入库正确

## 4. wx-sniffer UI 精简

- [x] 4.1 `templates/index.html`：删「同步主系统/补齐 UPC/刷新列表/下载日志/清空日志」5 个按钮；顶栏只剩开始/停止抓取（+ 健康徽章）；表头「库存」→「月销」
- [x] 4.2 `static/app.js`：删 `syncProducts/backfillUpc/downloadLogs/clearLogs`；商品行 `p.stock` → `p.monthly_sales`（null 显示「-」）；JS 语法检查、无悬空引用
- [x] 4.3 端点保留无按钮：`/api/products/sync`、`/api/products/backfill-upc`、`/api/logs*`（日志文件仍在磁盘）

## 5. 后台 routers/products.py 字段对齐

- [x] 5.1 `SnifferProductItem` 加 `spu_id/variant_title/description/brand_name/monthly_sales/weight/weight_unit/min_purchase_qty/category_ids/category_path/detail_images/attributes`（patch2 + patch3）
- [x] 5.2 `_upsert_sniffer_product_master`：写 `master.source_product_id=真实 spu_id`、`master.brand_name`、`master.category_ids/category_path`、`variant.weight/weight_unit/min_purchase_qty/monthly_sales/variant_title`
- [x] 5.3 `ProductMedia(detail_image)` add-if-new（按 url 去重，不动 master_image）；`ProductAttribute` add-if-new（按 group+key 去重，资质类字段名归「资质」组）
- [x] 5.4 `py_compile` 校验 + 重启 `yuandian-backend` + curl 验证（含分类/详情图/属性/月销/品牌/真实 SPU id 全套入库；回归既有 `/master/*` 不受影响）；已备份 `.bak2/.bak3`

## 6. 后台前端

- [x] 6.1 `ProductMasterManage.vue`：来源平台标签映射 + 筛选下拉加 `wechat_meituan → 小程序美团`
- [x] 6.2 表格「创建时间」列 → 「同步时间」（显示 `last_synced_at`，即行排序依据）；`normalizedRows` 映射 `lastSyncedAt`
- [x] 6.3 `npm run build` 重新构建；已备份 `.bak/.bak2`

## 7. 收尾

- [x] 7.1 更新 `README.md`：数据流、UI 精简、auto-push、字段清单、月销文案解析、拿不到的字段说明、保留的无按钮端点
- [x] 7.2 `openspec archive enrich-sniffer-catalog-and-autopush`
