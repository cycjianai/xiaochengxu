## ADDED Requirements

### Requirement: 推送端点接受任意来源的商品主档
`/api/products/master/push-to-meituan`、`/api/products/master/push-to-eleme` 以及牵牛花的覆盖更新任务 SHALL 接受**任意 `source_platform`** 的 `ProductMaster`，不因来源平台 ≠ 目标平台而拒绝。必填字段 SHALL 一律从统一字段派生（不依赖来源是哪个平台）：`name←ProductMaster.title`、`pictures/images←ProductMedia(master_image)`（没有则用 `sku_image`）、SKU 列表 `←ProductVariant`（`upcCode/barcode←ProductVariant.barcode`、原价/折扣价、重量、起购数）、`description`(HTML)←`ProductMaster.description` + `ProductMedia(detail_image)`、`attrList/itemPropValues←ProductAttribute`（映射不上就空）、资质图 `←ProductMedia(guide_image)`。

#### Scenario: 跨平台推送报文里有正确的目标平台类目
- **WHEN** 把一个 `eleme` 来源的 master 推到美团，且已通过对照表/UI 解析出美团类目 `X`
- **THEN** 发给美团的报文里 `categoryId` SHALL 是 `X`（不是该 master 身上的饿了么类目），且 `name/pictures/wmProductSkus(含 upcCode)` 等 SHALL 从统一字段派生齐全

#### Scenario: 缺目标类目时不硬推
- **WHEN** 推送的 master 里有解析不到目标平台类目的（`resolve_target_category` 返回 `needs_manual` 且 UI 没传 override）
- **THEN** 后台 SHALL NOT 给这些 master 发残缺/错误报文；SHALL 在返回体里把这些 master 列出来（连带「需指定目标类目」的提示）让 UI 处理；其余能解析的 master 正常推

#### Scenario: 缺条码的不推到饿了么
- **WHEN** 推到饿了么的某 master 的所有 SKU 都没有 `barcode`
- **THEN** 后台 SHALL 把该 master 列入「缺条码无法推送」返回 UI，不发报文

### Requirement: 推送前宽松完整性校验
跨平台推送前 SHALL 跑一个宽松的完整性校验 `_assert_push_ready_relaxed(item, to_platform)`：缺 `title` / 缺所有图片 / 缺所有带 `barcode` 的 SKU / 缺目标平台类目（牵牛花是缺目标 SPU）→ raise 明确错误（连带是哪个 master、缺什么）。`meituan` 来源推美团时 SHALL 仍额外走原来的强完整性校验 `_assert_meituan_master_payload_complete`（不放松同平台的严格度）。

#### Scenario: 残缺商品被明确拒绝
- **WHEN** 推送一个 title 为空、或一张图都没有、或一个带条码的 SKU 都没有的 master
- **THEN** 后台 SHALL 返回明确错误（指明 master_id 与缺失项），SHALL NOT 静默发个残缺报文给平台

#### Scenario: 同平台严格度不变
- **WHEN** 把一个 `meituan` 来源的 master 推到美团
- **THEN** SHALL 仍走原来的 `_ensure_meituan_master_detail_ready`（必要时拉美团详情补全）+ `_assert_meituan_master_payload_complete`（强校验）—— 同平台推送的行为与本变更前一致

### Requirement: 推送 body 支持 target_category 覆盖
`ProductMasterPushBody`（push-to-meituan / push-to-eleme 的入参）SHALL 增加可选字段 `category_overrides: dict[master_id(str), target_category_id(str)]`，让 UI 在对照表没命中时为每个 master 指定目标平台类目。

#### Scenario: 带 override 推送
- **WHEN** UI 用 `{"ids": [101,102], "target_cookie_id": 5, "category_overrides": {"101": "200001347"}}` 调 push-to-meituan
- **THEN** master 101 用美团类目 `200001347` 推送；master 102 走 `resolve_target_category`（对照表/native/needs_manual）
