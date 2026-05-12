## ADDED Requirements

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

## MODIFIED Requirements

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
