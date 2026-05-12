## ADDED Requirements

### Requirement: 统一商品 schema（4 平台共用）
后台商品库 SHALL 用一套来源无关的统一模型存所有平台的商品：`ProductMaster`（标题 `title`、品牌 `brand_name`、类目 `category_ids`(JSON 数组，按 level 升序) + `category_path`(JSON 数组，分类名)、描述 `description`、封面 `cover_image_url`、来源标识 `source_platform` / `source_store` / `source_spu_key` / `source_product_id`、补全度 `completeness_score`）+ `ProductVariant`（规格 `spec` / `variant_title`、条码 `barcode`、原价 `original_price` / 折扣价 `discount_price`、重量 `weight` + `weight_unit`、月销 `monthly_sales`、起购数 `min_purchase_qty`、`source_sku_key` / `sku_id`）+ `ProductMedia`（`media_type` ∈ {`master_image` 主图/轮播图, `sku_image` SKU图, `detail_image` 图文详情图, `guide_image` 资质/引导图, `video`}, `url`, `sort_order`）+ `ProductAttribute`（`attr_group` 分组 / `attr_key` 字段名 / `attr_value` 值）+ `ProductRawPayload`（`payload_kind` 标来源类型, `payload_json` 原始报文归档）。`_master_to_dict()` SHALL 是这套模型的统一读出口，产出来源无关的字典供所有推送函数消费。

#### Scenario: 4 个来源写同一套表
- **WHEN** 任一来源（美团 Cookie 采集 / 饿了么 Cookie 采集 / 牵牛花账号采集 / wx-sniffer 微信小程序抓包）入库一个商品
- **THEN** 它 SHALL 写入 `ProductMaster` + `ProductVariant` + `ProductMedia` + `ProductAttribute` + `ProductRawPayload` 这同一套表，`source_platform` 字段区分来源；`_master_to_dict()` 读出来的结构 SHALL 不因来源不同而结构不同

#### Scenario: source→统一 的映射有据可查
- **WHEN** 需要知道某平台原始字段映射到统一模型的哪个字段
- **THEN** SHALL 有文档（本 spec + 各 `_upsert_*_product_master` 函数）说明：美团 `wmProductSkus[].upcCode → ProductVariant.barcode`、`name → ProductMaster.title`、`categoryIdPath → category_ids` 等；饿了么 `barCode → barcode`、`raw_item_mutation` 整体归档到 raw_payload、`cateId`(平台标准类目) 现状归档在 raw_payload；牵牛花标准化字段 → 统一字段；wx-sniffer `sku.upccode → barcode`、`sku.spu_id → source_product_id`、`standardCategorys(level 1/2/3) → category_ids/category_path`、`pic_content.contents → ProductMedia(detail_image)`、`standard_productinfo_list → ProductAttribute`

### Requirement: 统一→各平台推送报文 的映射与必填要求
SHALL 有文档（本 spec + 各推送函数）说明每个平台「建档/覆盖」时需要什么、统一字段怎么映射过去、缺了怎么办：
- **美团**（`MeituanPricePushService.push_product_master`）：必填 `categoryId`（美团类目 ID —— **平台特有，跨平台时需对照表/现场选**）、`name←title`、`pictures←ProductMedia(master_image)`、`wmProductSkus←ProductVariant`（`upcCode←barcode`、价格）；可空 `attrList`(`[]`)、`brandId`(`0`)；标志 `missingRequiredInfo`。`meituan` 来源会先做美团详情补全 + 强完整性校验；非 `meituan` 来源跳过这两步。
- **饿了么**（`_build_eleme_push_payload`）：必填 `barcode`（第一个 SKU 的条码 —— **硬要求，没条码不能推**）、`cateId`（饿了么平台标准类目 —— **平台特有，跨平台时需对照表/现场选**）、`images←ProductMedia(master_image)`、`description`(HTML，由 `_render_eleme_description_html(描述, detail_images)` 生成)；可空 `itemPropValues`/`customProperties`、`specialPictures←ProductMedia(guide_image)`；店内分类由 `_build_eleme_category_allocator` 从目标门店店内分类自动分配（不是平台标准类目）。
- **牵牛花**（`QnhOverwriteUpdateTaskService`）：**是「用 master 的内容覆盖一个已存在的牵牛花 SPU」不是新建** —— 必须提供 `master → 目标牵牛花 SPU id` 的映射；覆盖内容包括主图/详情图/SKU（`_build_overwrite_payload` / `_merge_skus` / `_source_image_urls` / `_source_detail_image_urls`），从统一字段 + `ProductMedia` 读；不需要类目（目标 SPU 自带）。

#### Scenario: 推送时按平台序列化
- **WHEN** 选一个商品主档推送到某平台
- **THEN** 后台 SHALL 用 `_master_to_dict()` 拿统一结构 → 按该平台的映射序列化成平台报文 → 调该平台的推送服务；这套流程 SHALL 不因 master 的 `source_platform` 与目标平台不同而短路（即支持跨平台）

#### Scenario: 字段分类清楚
- **WHEN** 评估某个统一字段能否跨平台
- **THEN** 文档 SHALL 标明它属于：①「同义不同名」（如 barcode / upcCode / barCode —— 直接映射）；②「平台特有需对照」（如类目 ID —— 需对照表 / 现场选）；③「平台特有可空」（如各平台的结构化属性 —— 映射不上就空着）；④「平台特有的目标标识」（如牵牛花的目标 SPU —— 必须用户提供）
