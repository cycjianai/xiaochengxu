# sniffer-field-enrichment Specification

## Purpose
TBD - created by archiving change enrich-sniffer-catalog-and-autopush. Update Purpose after archive.
## Requirements
### Requirement: 三级分类的两段式抓取与持久缓存
wx-sniffer addon SHALL 拦截门店分类/商品列表接口（路径含 `/poi/sputag/products`、`/poi/product/smooth/render`、`/poi/category/products` 或 `/poi/products`），从 `data.product_spu_list[].standardCategorys`（形如 `[{id,name,level}]`，level 1/2/3）解析每个 SPU 的三级分类，缓存为 `{spu_id → {category_ids, category_path}}`（按 level 升序），并 SHALL 把该缓存持久化到 `category_cache.json`（应用数据目录下），启动时加载。点进单品（路径含 `/poi/product/info` 等 `FILTER_PATHS`）时 SHALL 用该 SPU 的 id 在缓存里查到三级分类并附到回传商品上；若缓存里没有则该商品的分类为空（不报错）。

#### Scenario: 进店刷分类页缓存分类
- **WHEN** 用户在店里浏览某分类，触发 `quickbuy/v1/poi/sputag/products`，响应里 `product_spu_list` 含若干 SPU 各带 `standardCategorys`
- **THEN** addon SHALL 把这些 `{spu_id → 三级分类}` 写入内存缓存并落盘 `category_cache.json`，日志面板 SHALL 出现「[分类缓存] … → 缓存 N 个 SPU 的三级分类」

#### Scenario: 点进单品匹配到分类
- **WHEN** 用户点进一个 SPU 的详情页，且该 SPU 的 id 已在分类缓存里
- **THEN** 回传的每个 item 的 `category_ids` / `category_path` SHALL 等于缓存里的值（level 1/2/3 升序），日志 SHALL 显示「分类=一级/二级/三级」

#### Scenario: 缓存里没有该 SPU 的分类
- **WHEN** 用户点进一个 SPU 详情页，但其 id 不在分类缓存里（没刷过它所在的分类页）
- **THEN** 回传 item 的 `category_ids` / `category_path` SHALL 为空数组，日志 SHALL 提示「先进店刷一下分类」，商品照常入库与上传（不报错）

#### Scenario: 跨重启累积
- **WHEN** 用户关掉再重开 wx-sniffer
- **THEN** addon SHALL 从 `category_cache.json` 加载之前缓存过的全部 `{spu_id → 三级分类}`，无需重新刷分类页

### Requirement: 月销取真实展示量
回传商品的 `monthly_sales` SHALL 这样取值：`data.month_saled` 数字字段非 0 时用它；否则解析 `data.month_saled_content` 文案里的数字（"月售100+" → 100，"月售0" → 0）；"X人想买" / "X人收藏" 这类不算销量（视为无）；两者都没有 → 无（`None`）。本地 DB 列里月销为 0/空时，列表展示 SHALL 从 raw_json 里按同样规则现解析兜底。

#### Scenario: 数字字段为 0 但文案有量
- **WHEN** 某商品 `data.month_saled == 0` 且 `data.month_saled_content == "月售100+"`
- **THEN** 回传 / 入库 / 列表展示的 `monthly_sales` SHALL 为 `100`

#### Scenario: 真实就是 0
- **WHEN** 某商品 `data.month_saled == 0` 且 `data.month_saled_content == "月售0"`
- **THEN** `monthly_sales` SHALL 为 `0`

#### Scenario: 想买不算销量
- **WHEN** `data.month_saled_content == "12人想买"` 且数字字段为 0
- **THEN** `monthly_sales` SHALL 为无（`None`），不当成 12

### Requirement: 从单品详情接口抽出全部可得字段
回传商品 SHALL 至少包含从 `quickbuy/v2/poi/product/info` 响应里能拿到的：`spu_id`（真实美团 SPU id，源自 `sku.spu_id` 或 `data.id`）、`variant_title`（`sku.combine_spec`）、`description`（`sku.description`）、`brand_name`（`standard_productinfo_list` 里「品牌」字段）、`weight` + `weight_unit`（解析 `sku.spec_num_unit_string` 的 `total_spec`）、`min_purchase_qty`（`sku.min_order_count`）、`detail_images`（`data.pic_content.contents`，去掉尺寸 query）、`attributes`（`data.standard_productinfo_list` → `[{group,key,value}]`，资质类字段名归「资质」组其余「商品参数」组）、`product_pic`（优先 `data.opt_pictures`，其次 `data.pictures`）。

#### Scenario: 详情图带量纲 query 被清理
- **WHEN** `data.pic_content.contents` 里是 `http://p0.meituan.net/sgopen/xxx.jpg?w=750&h=874`
- **THEN** 回传 `detail_images` 里该 URL SHALL 为 `http://p0.meituan.net/sgopen/xxx.jpg`（去掉 `?w=...`）

#### Scenario: 资质类字段归到「资质」组
- **WHEN** `standard_productinfo_list` 里有 `{fieldName:"注册证编号/备案凭证编号", value:"鲁械注准20162180063"}`
- **THEN** 回传 `attributes` 里该项的 `group` SHALL 为「资质」；`{fieldName:"口味",...}` 这类的 `group` SHALL 为「商品参数」

#### Scenario: 起购数取到批量起购
- **WHEN** 某规格 `sku.min_order_count == 10`（如「10个起购更优惠」）
- **THEN** 该 variant 的 `min_purchase_qty` SHALL 为 `10`

#### Scenario: 字段缺失不报错
- **WHEN** 某商品的 `pic_content` / `standard_productinfo_list` / `spec_num_unit_string` 等字段缺失或为空
- **THEN** 对应输出字段 SHALL 为空 / `None`，商品照常入库与上传，不报错

### Requirement: 诊断模式
当环境变量 `WX_SNIFFER_DISCOVER=1` 时，addon SHALL 把含 `product/categor/detail/spu/menu/tag/list/goods` 字样的接口（method + url + 完整响应体）逐行 dump 到日志目录下的 `discover.jsonl`，并把所有美团/点评接口的 URL 也打到 UI 日志面板（DEBUG）。该开关默认关闭。

#### Scenario: 开启诊断
- **WHEN** 以 `WX_SNIFFER_DISCOVER=1` 启动 wx-sniffer 并浏览小程序
- **THEN** 候选接口的响应 SHALL 被追加写入 `logs/discover.jsonl`，日志面板 SHALL 出现 `[discover] host/path -> 状态码 (已 dump)`

#### Scenario: 默认关闭
- **WHEN** 不带该环境变量启动
- **THEN** SHALL NOT 写 `discover.jsonl`，日志面板 SHALL NOT 因非匹配接口刷屏

