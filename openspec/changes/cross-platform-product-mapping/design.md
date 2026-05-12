## Context

后台（`192.168.1.27:/home/yuandian/backend`，FastAPI + SQLAlchemy + PostgreSQL）的商品库**已经是统一模型**：

- **统一存储**：`ProductMaster`（id, source_platform, source_store, source_spu_key, title, category_ids(JSON), category_path(JSON), description, brand_name, source_tag_id/name, source_product_id, cover_image_url, source_payload_summary, completeness_score, ...）+ `ProductVariant`（master_id, source_sku_key, sku_id, spec, barcode, original_price, discount_price, weight, weight_unit, monthly_sales, min_purchase_qty, variant_title, sort_order, source_payload）+ `ProductMedia`（master_id, variant_id?, media_type ∈ {master_image, sku_image, detail_image, guide_image, video}, url, sort_order）+ `ProductAttribute`（master_id, attr_group, attr_key, attr_value, sort_order）+ `ProductRawPayload`（master_id, payload_kind ∈ {meituan_list, meituan_detail, eleme_*, sniffer_sync, ...}, payload_json）。唯一键 `ProductMaster(source_platform, source_store, source_spu_key)`、`ProductVariant(master_id, source_sku_key)`。
- **4 个来源各自的 source→统一 写入**：`_upsert_meituan_product_master`（美团 list 报文 → 统一）、eleme 的 `_upsert_*`（饿了么报文 → 统一，并把 `raw_item_mutation` 等存进 raw_payload）、`_upsert_generic_group_product_master` / qnh 路径（牵牛花 → 统一）、`_upsert_sniffer_product_master`（wx-sniffer 扁平 item → 统一）。它们填的是同一套字段。
- **统一→各平台推送序列化**：
  - **美团**：`MeituanPricePushService.push_product_master(cookies, wm_poi_id, master, fixed_tag_list)`。`master` 是 `_master_to_dict()` 的产物。硬要求：`category_ids` 非空（取 `category_ids[-1]` 当 `categoryId`，缺则 `raise "缺少 categoryId"`）；还会用 `name←title`、`pictures`、`wmProductSkus`（`upcCode←barcode`、价格）、`attrList`（可 `[]`）、`brandId`（可 0）、`missingRequiredInfo`。建档前对 `meituan` 来源会先 `_ensure_meituan_master_detail_ready`（拉美团详情补全）+ `_assert_meituan_master_payload_complete`（强校验）；**非 meituan 来源跳过这两步**，直接 `_master_to_dict` 后推 —— 所以非美团→美团推送的卡点就是 `categoryId`。
  - **饿了么**：`_build_eleme_push_payload(master, target_store_id, target_seller_id, ...)`。硬要求：第一个 SKU 的 `barcode` 非空（缺则 `raise "缺少条码"`）；`cateId`（从 `raw_mutation.cateId or master.category_id` 取，缺则 `raise "缺少饿了么类目 cateId"`）；`images`（缺则从 `master.images` 派生）；`description`（HTML，由 `_render_eleme_description_html(描述, detail_image_urls)` 生成）；`itemPropValues`/`customProperties`（可空）；`specialPictures`（资质图等）。**店内分类**另有 `_build_eleme_category_allocator`：从目标饿了么门店的店内分类列表里挑一个未满（count<5000）的自动分配，可传 `preferred_category_id`。所以非饿了么→饿了么的卡点是 `cateId`（平台标准类目）和 `barcode`。
  - **牵牛花**：`QnhOverwriteUpdateTaskService.create_task(mappings=[{master_id/product_master_id, target_spu}...])` → worker 按批 `_execute_item` → `_build_overwrite_payload(master, source_raw, target_spu)` → `_merge_skus` / `_source_image_urls` / `_source_detail_image_urls` → `QnhImageBorderService.update_tenant_spu(cookies, tenant_spu=overwrite_payload)`。**是「用 master 的内容覆盖一个已存在的牵牛花 SPU」**，所以必须有 `master → 目标牵牛花 SPU` 的映射（前端已经有「为每个商品填写目标牵牛花 SPU」的入口）。不需要类目（目标 SPU 自带）。从任意来源 master 来都行（它读的是 `_master_to_dict` 那套字段 + `ProductMedia`）。
- `_master_to_dict()` 是统一出口，已经做了不少跨平台兜底（`master_images` 从 `ProductMedia(master_image)` 取，没有就用 `sku_image`；`cpv_guide_pic_list` 从 raw 取不到就用 `ProductMedia(guide_image)`；`detail_images` 从 `page_detail` 或 `ProductMedia(detail_image)`；eleme 来源会再 `_build_eleme_effective_page_detail`）。但 `category_id` 它取的是 `master.category_ids[-1]` —— 这个 ID 是**来源平台的**类目 ID，跨平台推送时是错的。
- 没找到「按 UPC/条码查某平台标准类目」的现成能力（美团/饿了么的标准库 by-UPC 查询）。

## Goals / Non-Goals

**Goals:**
- 把这套「字段匹配映射关系」显式写成文档（spec）—— 用户要的分析交付物，且后续维护有据
- 补上类目跨平台对照机制：对照表 + 解析器 + 推送时用它定类目 + 成功后回写
- 推送端点对任意来源商品稳健：必填字段从统一字段派生、宽松完整性校验、`target_category_id` 覆盖
- UI 上「推送到任意平台」跑通：类目对不上时现场选；牵牛花保留目标 SPU 选择器；成功后回写类目对照
- 不改 wx-sniffer（它的活到「推进商品库」为止）

**Non-Goals:**
- 不抓更多接口（不去美团/饿了么标准库查 by-UPC 类目 —— 没现成能力，本次只做对照表 + UI 选）
- 不做「店内分类」的跨平台对照（饿了么 allocator 自动分；美团用 cookie 的 default_push_tag）—— 只对照「平台标准商品类目」
- 不维护「每个类目的必填关键属性默认值」（跨平台推送时某些类目特有必填属性填不上，可能被平台拒）—— 列为后续逐类目沉淀
- 不在真实推送链路上端到端验证「饿了么抓的→推美团」（需有效 cookie + 真实门店）—— 代码改完要实测，本变更只保证代码与流程齐备
- 不改牵牛花推送的核心逻辑（它本来就接任意来源 + 目标 SPU）

## Decisions

### D1：「字段匹配映射」用 spec 文档化（不是代码）
新增 capability `unified-product-schema` 的 spec，把三层映射写清楚：
1. **统一 schema**（上面 Context 里那套表的字段清单）
2. **source→统一**：对每个来源，列「平台原始字段 → 统一字段」（如美团 `wmProductSkus[].upcCode → ProductVariant.barcode`、`name → ProductMaster.title`、`categoryIdPath → category_ids`；饿了么 `barCode → barcode`、`cateId → ?`（注意：饿了么的 cateId 是平台标准类目，存哪 —— 现状是塞进 raw_payload，统一字段里没有专门位置，本变更要不要加个 `ProductMaster.platform_category_id` 字段？见 D2）；wx-sniffer `sku.upccode → barcode`、`standardCategorys(level1/2/3) → category_ids/path` 等）
3. **统一→各平台推送报文**：对每个平台，列「统一字段 → 平台报文字段 + 是否必填 + 缺了怎么办」
这份文档既是交付物，也是后面写代码的依据。

### D2：类目对照 —— 官方类目总表 + DeepSeek 选类目 + 对照表沉淀 + 解析器
- **`ProductMaster` 不动**（`category_ids`/`category_path` 仍存「来源平台的」类目，作为「这个商品在它原平台是什么类目」的记录）。
- **新表 `platform_category_tree`**：缓存每个平台的官方商品标准类目树。字段：`id, platform, category_id, category_name, parent_id(可空), level(1/2/3...), is_leaf, full_path(TEXT, JSON 数组, 从根到本节点的名字), full_path_ids(TEXT, JSON 数组), fetched_at`，唯一键 `(platform, category_id)`。一次性抓取（之后偶尔刷新）—— 后台用 `cookies` 表里已有的美团/饿了么商家后台 cookie 去逆向「建商品时选类目」那个类目树接口，分页/递归拉全树写库（牵牛花不需要，覆盖更新用目标 SPU 自带类目）。
- **新表 `platform_category_mapping`**：「逐步沉淀」的对照表。字段：`id, from_platform, from_category_id, from_category_path(TEXT, JSON, 可空, 用于 path 级匹配), to_platform, to_category_id, to_category_name, decided_by(manual/llm/native), hit_count, created_at, updated_at`，唯一键 `(from_platform, from_category_id, to_platform)`。
- **DeepSeek 选类目** `llm_pick_category(target_tree, product_hint) -> {id, name, path, confidence}`：用后台已配的 `DEEPSEEK_*`（`api.deepseek.com`、`deepseek-v4-flash`），**逐层走树**：① 给 LLM 商品提示（标题、品牌、规格、源平台类目路径、几个关键属性）+ level-1 类目列表 → 选一级；② 在那个一级下给 level-2 列表 → 选二级；③ 给 level-3 叶子列表 → 选叶子。每层只给 10~50 个选项，3 次小调用。返回叶子类目 + 置信度（LLM 自评 high/medium/low，或按它给的理由判断）。置信度 low → 视为没选出来。
- **解析器** `resolve_target_category(db, master, to_platform, override_id=None, use_llm=True) -> {id, name, source}`（`source` ∈ {manual, mapping, native, llm, needs_manual}）：
  1. `override_id` 非空 → `{id: override_id, source: "manual"}`
  2. 查 `platform_category_mapping`（`from_platform=master.source_platform, from_category_id=master 的源类目 id, to_platform`）命中 → `{..., source: "mapping"}`，`hit_count += 1`；也可按 `from_category_path` 模糊匹配
  3. `to_platform == master.source_platform`（同平台推送）→ `{id: master 的源类目 id, source: "native"}`
  4. `use_llm` 且目标平台的 `platform_category_tree` 已抓 → `llm_pick_category(...)`，置信度足够 → `{..., source: "llm"}`，并 `record_category_mapping(... decided_by="llm")`（缓存，下次走 step 2 命中）
  5. 都没有（树没抓 / LLM 没把握）→ `{id: None, source: "needs_manual"}` —— 端点据此返回「需指定目标类目」给 UI
- **回写**：推送成功后，用的目标类目若来自 `manual` 或 `native`（首次）→ upsert 进 `platform_category_mapping`（`decided_by` 记 manual/native）；`llm` 那条在 step 4 已经写过了。已存在则 `hit_count += 1`。
- **替代（否决）**：① 在 `ProductMaster` 上加 `meituan_category_id` / `eleme_category_id` 列 —— 太死板，对照表更灵活。② 不要 LLM、纯让用户每次手填 —— 用户已明确要 DeepSeek，体验差太多。③ 把整棵树（几千节点）一次性塞给 LLM 让它选 —— 太大、贵、容易乱；逐层走树每层选项少、准、便宜。

### D3：推送端点稳健化
- `ProductMasterPushBody` 增加可选 `category_overrides: dict[str(master_id), str(target_category_id)]`（UI 在对照表没命中时为每个 master 填）。
- `_push_product_masters_to_cookie`（美团）：对每个 master，`item = _master_to_dict(master)`（不再因为 `source_platform != "meituan"` 就跳过补全 —— 至少跑一个**宽松版完整性派生**：title 必有、master_image 至少 1 张、SKU 至少 1 个带 barcode）；`cat = resolve_target_category(db, master, "meituan", override)` → 缺则收集到 `unresolved` 列表，全部 unresolved 的 master 不推、返回给 UI；有则把 `cat.id` 作为 `categoryId` 传给 `service.push_product_master(..., category_id=cat.id)`（改 `push_product_master` 接受外部 `category_id` 覆盖 master 自带的）。推送成功后回写对照。
- `_build_eleme_push_payload` / `_push_product_masters_to_eleme_cookie`（饿了么）：同理，`cateId` 优先用 `resolve_target_category(..., "eleme", override)`；`barcode` 缺则该 master 进 `unresolved`（条码是硬要求，没条码的不推）；其余从统一字段派生（已有兜底，补齐）。店内分类继续走 allocator。
- 牵牛花：`QnhOverwriteUpdateTaskService.create_task` 已要 `mappings=[{master_id, target_spu}]` —— 确认它对任意来源 master 都能跑（读的是 `_master_to_dict` + `ProductMedia`），不改逻辑；只在文档里写清「牵牛花推送 = 覆盖已有 SPU，必须给目标 SPU」。
- **宽松完整性校验**（跨平台通用）：`_assert_push_ready_relaxed(item, to_platform)` —— 缺 title / 缺所有图片 / 缺所有带 barcode 的 SKU / 缺目标类目（牵牛花是缺目标 SPU）→ raise 明确错误。`meituan` 来源仍额外走原来的 `_assert_meituan_master_payload_complete`（更严）。

### D4：UI（`ProductMasterManage.vue`）
- 「推送到美团 / 饿了么 / 牵牛花」按钮：去掉「只能推同来源平台」的隐含限制（如果有的话），对选中的任意行都可用。
- 点「推送到 X」→ 先调一个 `POST /api/products/master/resolve-target-categories`（新端点，入参 `{ids, to_platform}`，出参每个 master 的 `{master_id, resolved: {id,name,source} | null}`）→ 弹窗里：已解析的显示类目名（来源标 自动/对照表/原生）；未解析的，渲染一个该平台的类目选择器（类目选项怎么来 —— 美团/饿了么的类目树从哪取？现状里美团推送用 cookie 的 tag，饿了么用 store 的店内分类；**平台标准类目树**可能要从某个采集来的 raw 里凑，或者让用户直接填类目 ID/名 —— 第一版先做「填类目 ID + 名称」的简单输入，后续接类目树）。牵牛花的「目标 SPU」选择器保留（已有）。
- 确认后调 `push-to-X`（带 `category_overrides` / qnh 的 mappings）→ 成功后后端已自动回写对照表。

### D5：分阶段
- **Phase 1（文档）**：写 `unified-product-schema` spec —— 三层映射表。这是交付物，也是后面的依据。可独立先交。
- **Phase 2（类目对照机制）**：`platform_category_mapping` 表 + `resolve_target_category` + 回写。先不接 UI，用 curl 验证对照表读写 + 解析逻辑。
- **Phase 3（推送端点稳健化）**：`category_overrides` body 字段 + 推送时用解析器定类目 + 宽松完整性校验 + `push_product_master` 接外部 category_id。curl 验证「给一个 wx-sniffer/eleme 来源的 master + 一个手填 meituan 类目 → 推美团」能跑通（至少报文构建不报「缺 categoryId」；真实推送要有效 cookie）。
- **Phase 4（UI）**：`resolve-target-categories` 端点 + 推送弹窗加类目解析显示/选择器 + 按钮对任意来源可用。

## Risks / Trade-offs

- **[类目对照表初期为空]** → 跨平台推送前几次都要人工选目标类目；用一段时间命中率才上来。可接受（这是冷启动问题，没有数据源能凭空给出对照）。可选优化：从已有商品里反推（同 barcode 的商品在不同平台各是什么类目 → 自动建对照），后续做。
- **[平台标准类目树从哪来]** → 美团/饿了么的「商品标准类目」全树，现状代码里没有完整获取；第一版 UI 让用户直接填类目 ID+名称（用户从平台后台能查到）。后续可加「拉类目树」的采集。
- **[某些类目的必填关键属性填不上]** → 跨平台推送时，目标平台某些类目要求填特定属性（如食品的成分、化妆品的备案号），统一字段里没有 → 平台侧可能拒。本次不解决（`attrList`/`itemPropValues` 走空），列为后续逐类目维护默认值。
- **[未端到端实测跨平台真实推送]** → 需有效目标平台 cookie + 真实门店；代码改完后要拿真环境跑「饿了么抓的→推美团」「wx-sniffer 抓的→推饿了么」「美团抓的→推牵牛花」各验一遍。
- **[牵牛花是覆盖更新不是新建]** → 不能「凭空在牵牛花建一个新商品」，只能覆盖已有 SPU；这是牵牛花平台本身的限制（它的开放能力是 `update_tenant_spu`）。文档里讲清，避免用户期望「饿了么抓的能在牵牛花新建」。
- **[改动集中在后台核心推送代码]** → `_push_product_masters_to_cookie` / `_build_eleme_push_payload` 是生效中的核心；改前备份（`.bak`），`py_compile` 校验，重启验证，先在「同平台推送」场景回归确认没坏，再上「跨平台」。

## Migration Plan

1. **Phase 1**：`unified-product-schema` spec 文档（梳理三层映射，不动代码）—— 可独立交付给用户作为「分析结果」
2. **Phase 2**：`models.py` 加 `PlatformCategoryMapping` + 建表迁移；`routers/products.py` 加 `resolve_target_category` + `record_category_mapping`；`py_compile` + 重启；curl 验证表读写 + 解析（同平台→native、对照表命中、未命中→needs_manual、override→manual）
3. **Phase 3**：`ProductMasterPushBody` 加 `category_overrides`；`push-to-meituan`/`push-to-eleme` 用解析器定类目 + 宽松完整性校验；`MeituanPricePushService.push_product_master` 加 `category_id` 参数；`py_compile` + 重启；**先回归同平台推送**（meituan→meituan、eleme→eleme 各推一个，确认没坏）；再 curl 跨平台报文构建（不真发到平台，看报文有没有 categoryId/cateId、必填字段齐不齐）
4. **Phase 4**：`resolve-target-categories` 端点；`ProductMasterManage.vue` 推送弹窗加类目解析显示 + 输入器；按钮对任意来源可用；`npm run build`
5. **实测**：拿真实目标平台 cookie，跨平台推送各组合各验一遍；推送成功后查 `platform_category_mapping` 有没有自动回写
6. README / openspec 归档

**回滚**：`models.py` 的新表可保留（空表无害）；`routers/products.py` / `meituan_price_push_service.py` 还原 `.bak`；前端还原 `.bak`。`platform_category_mapping` 表里的数据可单独清。

## Open Questions

- 美团/饿了么的「商品标准类目树」要不要做个采集端点（这样 UI 能给类目选择器而不是让用户填 ID）？本次让用户填 ID+名，列为后续。
- 「按 UPC 在目标平台查标准类目」—— 美团/饿了么有没有这种公开/半公开接口？没调研到；有的话能大幅提升自动命中率，列为后续。
- 跨平台推送时，要不要把「源平台的类目 path（中文名）」也带给目标平台做一次「按类目名模糊匹配目标平台类目树」？依赖类目树采集，列为后续。
- 牵牛花「能不能新建」—— 确认 `QnhImageBorderService` / 牵牛花开放接口里有没有「create_tenant_spu」之类的能力？若有，可补「牵牛花新建」路径；现状只看到 `update_tenant_spu`（覆盖）。
