## 1. Phase 1 — 文档化「字段匹配映射关系」（交付物，不动代码）

- [ ] 1.1 梳理统一 schema：从 `models.py` 把 `ProductMaster` / `ProductVariant` / `ProductMedia`（含 media_type 取值）/ `ProductAttribute` / `ProductRawPayload` 的字段全列出来，确认这就是「跨 4 平台的统一商品 schema」
- [ ] 1.2 梳理 source→统一 映射：逐个看 `_upsert_meituan_product_master`、eleme 的 `_upsert_*`、qnh 路径、`_upsert_sniffer_product_master`，列「平台原始字段 → 统一字段」对照表（重点：barcode/upcCode/barCode 这类同义、类目各平台存哪、raw_payload 里塞了什么平台特有的）
- [ ] 1.3 梳理 统一→各平台推送报文 映射：看 `MeituanPricePushService.push_product_master`（美团报文必填/可空字段）、`_build_eleme_push_payload` + `_build_eleme_category_allocator`（饿了么报文 + 店内分类分配）、`QnhOverwriteUpdateTaskService._build_overwrite_payload` + `_merge_skus` + `_source_image_urls`（牵牛花覆盖更新报文 + 必须给目标 SPU），列「统一字段 → 平台报文字段 + 是否必填 + 缺了怎么办」对照表
- [ ] 1.4 把 1.1~1.3 写成 `unified-product-schema` spec 的内容（已在 specs/ 里起草，按梳理结果校正/补全）；同时给用户一份可读的总结（哪些字段同义可直接映射、哪些平台特有需对照、哪些可空、哪些是目标标识必须用户给）

## 2. Phase 2 — 类目跨平台对照机制（后台，不接 UI）

- [ ] 2.1 `models.py` 加 `class PlatformCategoryMapping`：`id, from_platform, from_category_id, from_category_path(Text,nullable), to_platform, to_category_id, to_category_name, hit_count(default 0), created_at, updated_at`；唯一键 `(from_platform, from_category_id, to_platform)`
- [ ] 2.2 建表迁移：`main.py` 启动时 `Base.metadata.create_all` 已建（确认）；若用的是手写 DDL 列表，加一句 `CREATE TABLE IF NOT EXISTS platform_category_mapping (...)`
- [ ] 2.3 `routers/products.py`（或新 `services/category_mapping_service.py`）加 `resolve_target_category(db, master, to_platform, override_id=None) -> {id,name,source}`：override → manual；查 `PlatformCategoryMapping`（命中 hit_count+1 → mapping）；`to_platform==master.source_platform` → native（取 master 的源类目 id）；都没有 → needs_manual
- [ ] 2.4 加 `record_category_mapping(db, from_platform, from_cat_id, from_cat_path, to_platform, to_cat_id, to_cat_name)`：upsert（已存在则 hit_count+1 + 更新 to_category_name）
- [ ] 2.5 `py_compile` + 重启 `yuandian-backend`；curl/python 验证：建表存在；对照表读写；`resolve_target_category` 四条路径（native / mapping 命中 + hit_count 累加 / needs_manual / override→manual）

## 3. Phase 3 — 推送端点对任意来源稳健化（后台）

- [ ] 3.1 `routers/products.py` `ProductMasterPushBody` 加 `category_overrides: Optional[dict[str,str]] = None`（master_id → target_category_id）
- [ ] 3.2 `services/meituan_price_push_service.py` `push_product_master(...)` 加可选参数 `category_id`：传了就用它当 `categoryId`，不传才从 master 取（保持向后兼容）
- [ ] 3.3 `_push_product_masters_to_cookie`（美团推送）：对每个 master `item = _master_to_dict(master)`；`cat = resolve_target_category(db, master, "meituan", category_overrides.get(str(master.id)))` → `needs_manual` 的收集进 `unresolved`、不推；其余 `_assert_push_ready_relaxed(item, "meituan")` + `service.push_product_master(..., category_id=cat.id)`；`meituan` 来源仍额外走 `_ensure_meituan_master_detail_ready` + `_assert_meituan_master_payload_complete`；成功后 `record_category_mapping(...)`（若 source ∈ {manual, native}）；返回体带 `unresolved` 列表
- [ ] 3.4 `_build_eleme_push_payload` / `_push_product_masters_to_eleme_cookie`（饿了么推送）：`cateId` 优先用 `resolve_target_category(..., "eleme", override)`；`barcode` 缺则进 `unresolved`；其余从统一字段派生（已有兜底，补齐 images/description/itemPropValues 的 fallback）；店内分类继续走 allocator（`preferred_category_id` 可用解析出的）；`_assert_push_ready_relaxed`；成功后 `record_category_mapping`
- [ ] 3.5 加 `_assert_push_ready_relaxed(item, to_platform)`：缺 title / 缺所有图 / 缺所有带 barcode 的 SKU / 缺目标类目（牵牛花是缺目标 SPU）→ raise(明确 master_id + 缺失项)
- [ ] 3.6 牵牛花：确认 `QnhOverwriteUpdateTaskService.create_task` / `_execute_item` / `_build_overwrite_payload` 对任意来源 master 都能跑（读 `_master_to_dict` + `ProductMedia`）；不改逻辑，必要时只补「目标 SPU 缺失时明确报错」
- [ ] 3.7 `py_compile` + 重启；**先回归同平台推送**：meituan→meituan、eleme→eleme 各推 1 个商品（用现有有效 cookie），确认没坏；再 curl 跨平台**报文构建**（不真发平台）：给一个 wx-sniffer 来源 master + 手填 meituan 类目 → 看报文里有 categoryId、name/pictures/wmProductSkus 齐；给一个 meituan 来源 master + 手填 eleme cateId → 看报文里有 cateId、barcode、images、description；已备份 `.bak`

## 4. Phase 4 — UI（后台商品库页）

- [ ] 4.1 `routers/products.py` 加 `POST /api/products/master/resolve-target-categories`：入参 `{ids:[int], to_platform:str}`，出参 `{items:[{master_id, master_title, source_platform, source_category_path, resolved:{id,name,source}|null}]}`
- [ ] 4.2 `ProductMasterManage.vue`：「推送到美团/饿了么/牵牛花」按钮对**任意来源**的选中行可用（去掉「只能推同来源」的隐含限制，若有）
- [ ] 4.3 推送弹窗：点「推送到 X」先调 `resolve-target-categories` → 已解析的显示类目名 + 来源标记（自动/对照表/原生）；未解析的，渲染「目标平台类目 id + 名称」输入框（第一版先让用户填，后续接类目树）；牵牛花的「目标 SPU」选择器保留（已有）
- [ ] 4.4 确认后调 `push-to-X`（带 `category_overrides` / qnh 的 `mappings`）；成功后后端已自动回写对照表，弹窗显示「成功 N / 跳过 M（缺类目/缺条码）」
- [ ] 4.5 `npm run build`；已备份 `.bak`

## 5. 实测 & 收尾

- [ ] 5.1 跨平台真实推送各组合（需有效目标平台 cookie + 真实门店）：饿了么抓的→推美团、wx-sniffer 抓的→推饿了么、美团抓的→推牵牛花（给目标 SPU）；各验「推送成功 + 商品在目标平台正常 + `platform_category_mapping` 自动回写了对照」
- [ ] 5.2 README：「跨平台推送」整节（统一模型 + 类目对照机制 + 牵牛花是覆盖更新需给目标 SPU + 拿不到的字段说明）
- [ ] 5.3 `openspec archive cross-platform-product-mapping`
