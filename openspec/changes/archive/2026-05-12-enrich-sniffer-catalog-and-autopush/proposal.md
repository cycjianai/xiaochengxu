## Why

`wire-sniffer-to-product-catalog` 已让 wx-sniffer 能把商品推进后台商品库，但落地后发现：
- 抓到的字段太少 —— 重量、起购数、真实 SPU ID、规格全称、商品描述、品牌、月销、三级分类、详情图、结构化属性都没抽（这些跟后台美团平台来源的商品对不齐）；
- 月销取的是 `data.month_saled` 数字字段（常为 0），真实值在 `data.month_saled_content` 文案里（"月售100+"）；
- 三级分类只在「门店分类商品列表」接口里，单品详情接口没有 —— 需要两段式抓取（进店刷分类页缓存 → 点商品时匹配）；
- 「同步主系统」要手动点才传，运营同事用着麻烦；UI 上一堆没用的按钮（同步主系统、补齐 UPC、刷新列表、下载日志、清空日志）；「库存」字段对运营没意义，「月销」才有代表性；
- 后台商品库页只显示「创建时间」但行是按「同步时间」排的，重复推送的商品看着像没排上来。

这个变更把以上全部补齐 / 优化。

## What Changes

- **wx-sniffer addon（`capture/meituan_addon.py`）**：
  - 新增拦截门店分类/商品列表接口（`/poi/sputag/products`、`/poi/product/smooth/render` 等）→ 解析 `product_spu_list[].standardCategorys`（level 1/2/3）→ 缓存 `{spu_id → 三级分类}`；**持久化到 `category_cache.json`**（跨重启/会话累积）
  - 单品详情解析新增字段：月销（`month_saled` 数字非 0 优先，否则解析 `month_saled_content` 文案「月售100+」→ 100）、详情图（`pic_content.contents`）、结构化属性+品牌+资质文字（`standard_productinfo_list`）、重量+单位（`spec_num_unit_string`）、起购数（`min_order_count`）、真实 SPU ID（`spu_id` → `source_product_id`）、规格全称（`combine_spec`）、商品描述、轮播主图（`opt_pictures`）、视频；并用缓存的三级分类匹配到当前 SPU
  - 临时诊断开关 `WX_SNIFFER_DISCOVER=1`（dump 候选接口响应到 `discover.jsonl`，已用完，保留作排查工具）
- **wx-sniffer 本地 DB（`db.py`）**：`products` 表加 `monthly_sales` 列（自动 ALTER 迁移）；`_row_to_product` 在列为 0/空时从 raw_json 现解析月销文案兜底
- **wx-sniffer server（`server.py`）**：
  - **抓一条传一条**：`/api/internal/capture-products`（addon 回传入口）入库后立刻自动 POST 到后台商品库 `/api/products/sniffer/sync-to-catalog`（带 `Host` 头），结果写日志面板；失败写 WARN、数据已存本地
  - `/api/products/sync`（批量重推，保留无按钮）改用同一个 `_post_items_to_backend` helper；传递 monthly_sales / brand_name / category_ids / category_path / detail_images / attributes；raw_json 没分类时按 spu_id 查持久缓存兜底
- **wx-sniffer UI（`templates/index.html` + `static/app.js`）**：删掉「同步主系统/补齐 UPC/刷新列表/下载日志/清空日志」5 个按钮；表格「库存」列改成「月销」列；顶栏只剩开始/停止抓取 + 健康徽章
- **后台 `routers/products.py`**：`SnifferProductItem` 加 `spu_id/variant_title/description/brand_name/monthly_sales/weight/weight_unit/min_purchase_qty/category_ids/category_path/detail_images/attributes`；`_upsert_sniffer_product_master` 写 `master.brand_name`、`master.category_ids/category_path`、`source_product_id=真实 spu_id`、`variant.weight/weight_unit/min_purchase_qty/monthly_sales/variant_title`、`ProductMedia(detail_image)`（add-if-new）、`ProductAttribute`（add-if-new，按 group+key 去重）
- **后台前端 `ProductMasterManage.vue`**：「来源平台」标签映射 + 筛选下拉加「小程序美团」（`wechat_meituan`）；表格「创建时间」列改成「同步时间」（`last_synced_at`，即行排序依据）

## Capabilities

### New Capabilities
- `sniffer-field-enrichment`：wx-sniffer 从小程序接口能抽出的全部商品字段（含两段式三级分类、月销文案解析、详情图、结构化属性等）的契约
- `sniffer-auto-push`：「抓一条传一条」自动上传后台商品库的行为契约
- `sniffer-ui-minimal`：精简后的 wx-sniffer UI 契约（只剩抓取开关、月销列、自动刷新、日志面板）

### Modified Capabilities
- `catalog-sync-endpoint`：后台接收端点的字段集与写入逻辑扩展（新增分类/详情图/属性/月销/品牌/真实 SPU ID 的接收与落库）

## Impact

- wx-sniffer：`capture/meituan_addon.py`（大改：列表接口拦截 + 持久分类缓存 + 详情多字段解析 + 月销文案解析）、`db.py`（加列 + 月销兜底）、`server.py`（auto-push + `_post_items_to_backend` + 字段透传）、`templates/index.html` + `static/app.js`（删按钮、库存→月销）
- 后台：`routers/products.py`（`SnifferProductItem` + `_upsert_sniffer_product_master` 扩展，已备份 `.bak2/.bak3`、语法检查、服务已重启）；`frontend/src/views/ProductLibrary/ProductMasterManage.vue`（标签 + 列，已 rebuild）
- 数据：wx-sniffer 本地多了 `monthly_sales` 列、`category_cache.json` 文件；后台 `product_masters/variants/media/attributes` 里 `wechat_meituan` 来源的行字段更全
- 网络：auto-push 每抓一条多发一个 POST 到 `boss.fuliops.cn`（自己的后台，非美团，无风控影响）
- 风险/已知拿不到的：UPC（商家没填时接口返回空）、资质证照图片（只有资质文字）、部分商品品牌名（无「品牌」字段时只有 brand_id）—— 这些是数据源限制，非缺陷
