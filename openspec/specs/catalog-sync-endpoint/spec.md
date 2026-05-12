# catalog-sync-endpoint Specification

## Purpose
TBD - created by archiving change wire-sniffer-to-product-catalog. Update Purpose after archive.
## Requirements
### Requirement: 后台提供「抓包商品 → 商品库」接收端点
后台 SHALL 暴露 `POST /api/products/sniffer/sync-to-catalog`，接收 wx-sniffer 的 `{"items": [...]}` 报文（每个 item 含 `source_platform / poi_name / sku_id / product_name / upc / spec / origin_price / price / stock / product_pic / raw_json`），把数据写入商品库表 `product_masters` / `product_variants` / `product_media` / `product_raw_payloads`。该端点 SHALL NOT 修改既有的 `/api/products/sniffer/sync`、`/api/products/master/*` 路由及 `_upsert_meituan_product_master` 函数的行为。

#### Scenario: 接收并入库一批商品
- **WHEN** wx-sniffer POST 一个含 N 个 item 的 `{"items": [...]}` 到 `/api/products/sniffer/sync-to-catalog`
- **THEN** 后台 SHALL 对每个有效 item（`poi_name` 与 `sku_id` 都非空）在 `product_masters` / `product_variants` 里建立或更新对应记录，并返回 `{"success": true, "created": <新建 master 数>, "updated": <更新 master 数>, "variants_total": <写入 variant 总数>, "skipped": <跳过的无效 item 数>}`

#### Scenario: 既有端点不受影响
- **WHEN** 调用旧端点 `POST /api/products/sniffer/sync`
- **THEN** 它 SHALL 仍只 upsert 到 `sniffer_products` 表，行为与新端点上线前完全一致

### Requirement: 来源平台标记为「小程序美团」
所有经由该端点写入的 `product_masters` 行 SHALL 把 `source_platform` 设为 `"wechat_meituan"`；前端商品库页 SHALL 把 `wechat_meituan` 显示为「小程序美团」，并 SHALL 在「来源平台」筛选下拉里提供 `小程序美团` 选项。

#### Scenario: 入库行带正确来源平台
- **WHEN** 一条 wx-sniffer 商品经该端点入库
- **THEN** 对应 `ProductMaster.source_platform` SHALL 等于 `"wechat_meituan"`，且不与 `meituan` / `eleme` / `qianniuhua` 的行混淆

#### Scenario: 商品库页可按小程序美团筛选
- **WHEN** 用户在商品库页「来源平台」下拉选「小程序美团」并查询
- **THEN** 列表 SHALL 只显示 `source_platform == "wechat_meituan"` 的商品，且来源平台列 SHALL 显示文字「小程序美团」

### Requirement: 字段映射与缺字段容错
端点 SHALL 按固定映射把 wx-sniffer 字段写入商品库（门店名→`ProductMaster.source_store`，商品名→`ProductMaster.title` 与 `source_spu_key`，`sku_id`→`ProductVariant.source_sku_key` 与 `ProductVariant.sku_id`，`spec`→`ProductVariant.spec`，`upc`→`ProductVariant.barcode`，`origin_price`→`ProductVariant.original_price`，`price`→`ProductVariant.discount_price`，`stock`→`ProductVariant.stock`，`product_pic[]`→`ProductMedia(master_image)`，`raw_json`→`ProductRawPayload(payload_kind="sniffer_sync")`，以及扩展字段见「接收并落库 wx-sniffer 抓到的扩展字段」需求）。报文里**任何字段缺失** SHALL 留空（`None` / 空数组），SHALL NOT 因此报错或拒收 —— 比如商家没在后台填条码时 `upc` 为空、`standard_productinfo_list` 没「品牌」字段时 `brand_name` 为空、没刷过分类页时 `category_*` 为空，都照常入库。

#### Scenario: 价格字段映射
- **WHEN** 一个 SKU 的 `origin_price=4.5`、`price=3.67`
- **THEN** 对应 variant 的 `original_price` SHALL 为 `4.5`，`discount_price` SHALL 为 `3.67`

#### Scenario: price 无效时不写折扣价
- **WHEN** 一个 SKU 的 `price` 为 `0` / `-1` / 空
- **THEN** 对应 variant 的 `discount_price` SHALL 为 `None`

#### Scenario: 缺 UPC 不报错
- **WHEN** 一个 SKU 的 `upc` 为空（如商家未在后台填条码）
- **THEN** 该 variant SHALL 正常入库，`barcode` 为 `None`，不影响同 master 下其它 variant

#### Scenario: 缺商品库专有字段照样入库
- **WHEN** 报文里完全没有 `weight` / `brand_name` / `category_ids` / `detail_images` / `attributes` / `monthly_sales` 等字段
- **THEN** master/variant SHALL 成功入库，相应列为空，端点 SHALL 返回成功

#### Scenario: 图片进 ProductMedia
- **WHEN** 一个商品的 `product_pic` 是含 3 个 URL 的数组
- **THEN** 后台 SHALL 为该 master 写入 3 条 `ProductMedia`（`media_type` 为 `master_image`，按数组顺序 `sort_order`），并把第一张设为 `ProductMaster.cover_image_url`

#### Scenario: raw_json 归档
- **WHEN** 一个 item 带 `raw_json`
- **THEN** 后台 SHALL 在 `product_raw_payloads` 写一条 `payload_kind="sniffer_sync"` 的记录关联到该 master

### Requirement: 以「门店 + sku_id」为线索的覆盖式更新（增量合并）
当同一 `(source_platform="wechat_meituan", source_store=门店名, source_spu_key=商品名归一)` 的 master 已存在时，端点 SHALL 复用该 master；对报文里出现的 `sku_id`，若 master 下已有同 `source_sku_key` 的 variant 则覆盖其字段，否则新建 variant；master 下报文未提及的既有 variant SHALL 保留（不被清除）。也就是说多次零散抓取 SHALL 能在同一 master 下累积规格，且每个规格的最新数据 SHALL 覆盖旧数据。

#### Scenario: 同商品再次同步，字段变更被覆盖
- **WHEN** 某 sku_id 已在后台、`price=3.67`；wx-sniffer 再次抓到同 sku_id、`price=3.20` 并 sync
- **THEN** 后台该 variant 的 `discount_price` SHALL 更新为 `3.20`，master 计为 `updated`

#### Scenario: 分次抓不同规格能累积
- **WHEN** 第一次 sync 报文里某商品只含 sku_id=A；之后第二次 sync 报文里同商品名只含 sku_id=B
- **THEN** 该 master 下 SHALL 同时存在 variant A 和 variant B（第二次不冲掉 A）

#### Scenario: 新商品计为 created
- **WHEN** 报文里某 `(门店, 商品名)` 在后台不存在
- **THEN** 后台 SHALL 新建 master，返回体 `created` 计数 +1

#### Scenario: 无效 item 被跳过而非报错
- **WHEN** 报文里某 item 的 `poi_name` 或 `sku_id` 为空
- **THEN** 该 item SHALL 被跳过、计入返回体 `skipped`，其余 item SHALL 正常处理

### Requirement: 入库后可走现有下游推送
经该端点入库的 `wechat_meituan` 商品 SHALL 出现在 `/api/products/master` 列表中，并 SHALL 可被现有的「推送到美团」「推送到牵牛花」流程选中，无需额外字段适配。

#### Scenario: 出现在商品库列表 API
- **WHEN** 调 `GET /api/products/master?source_platform=wechat_meituan`
- **THEN** 返回的 `items` SHALL 包含刚入库的商品，结构与其它来源平台的 master 一致（含 `variants` / `media_items`）

#### Scenario: 可被推送流程选中
- **WHEN** 把某 `wechat_meituan` master 的 id 传给 `/api/products/master/push-to-meituan`（或牵牛花推送）
- **THEN** 推送流程 SHALL 接受该 id（不因 source_platform 不同而拒绝），后续行为由既有推送逻辑决定

### Requirement: 接收并落库 wx-sniffer 抓到的扩展字段
`POST /api/products/sniffer/sync-to-catalog` 的 item SHALL 额外接收并落库以下字段（缺任何一个都不报错）：
- `spu_id` → `ProductMaster.source_product_id`（真实美团 SPU id；缺则用 `slug(product_name)`）
- `brand_name` → `ProductMaster.brand_name`（非空才覆盖；`attributes` 里带「品牌」字段时也回填）
- `category_ids`（list）→ `ProductMaster.category_ids`（JSON 串，非空才写）
- `category_path`（list）→ `ProductMaster.category_path`（JSON 串，非空才写）
- `variant_title` → `ProductVariant.variant_title`
- `weight` / `weight_unit` → `ProductVariant.weight` / `ProductVariant.weight_unit`
- `min_purchase_qty` → `ProductVariant.min_purchase_qty`
- `monthly_sales` → `ProductVariant.monthly_sales`
- `detail_images`（list）→ 为该 master 新增 `ProductMedia(media_type="detail_image")`，按 url 去重（add-if-new，不动 `master_image`）
- `attributes`（list of `{group,key,value}`）→ `ProductAttribute(attr_group, attr_key, attr_value)`，按 `(group,key)` 去重（add-if-new）

#### Scenario: 三级分类落库
- **WHEN** item 带 `category_ids=["200000502","400000907","200001347"]`、`category_path=["家庭清洁","衣物护理","洗衣液"]`
- **THEN** 对应 `ProductMaster.category_ids` / `category_path` SHALL 是这两个数组的 JSON 串，前端商品库页「美团三级分类」列 SHALL 显示「家庭清洁 / 衣物护理 / 洗衣液」

#### Scenario: 详情图与主图分别存
- **WHEN** item 带 `product_pic=[m1,m2]`、`detail_images=[d1,d2,d3]`
- **THEN** 该 master 的 `ProductMedia` SHALL 含 2 条 `master_image`（m1/m2）+ 3 条 `detail_image`（d1/d2/d3），互不覆盖

#### Scenario: 结构化属性与品牌
- **WHEN** item 带 `brand_name="百威（Budweiser）"`、`attributes=[{group:"商品参数",key:"品牌",value:"百威（Budweiser）"},{group:"资质",key:"生产企业名称",value:"…"}]`
- **THEN** `ProductMaster.brand_name` SHALL 为「百威（Budweiser）」；该 master 的 `ProductAttribute` SHALL 含这两条（group 分别为「商品参数」「资质」）

#### Scenario: 月销 / 重量 / 起购数落库
- **WHEN** item 带 `monthly_sales=100`、`weight=1500.0`、`weight_unit="ml"`、`min_purchase_qty=10`
- **THEN** 对应 variant 的 `monthly_sales` SHALL 为 100、`weight`=1500.0、`weight_unit`="ml"、`min_purchase_qty`=10

#### Scenario: 真实 SPU id
- **WHEN** item 带 `spu_id="25340245668"`
- **THEN** `ProductMaster.source_product_id` SHALL 为「25340245668」（不是 `slug(商品名)`）

#### Scenario: 再次同步只增不删 detail_image / attribute
- **WHEN** 同一商品再次同步，`detail_images` / `attributes` 与上次相同或有新增
- **THEN** 后台 SHALL 只追加没见过的（按 url / `(group,key)`），SHALL NOT 删除已有的 `detail_image` / `ProductAttribute`，SHALL NOT 误删 `master_image`

