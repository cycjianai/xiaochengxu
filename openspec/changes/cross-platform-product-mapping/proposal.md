## Why

后台商品库现在汇集了 4 个来源的商品：`meituan`（美团 Cookie 采集）、`eleme`（饿了么 Cookie 采集）、`qianniuhua`（牵牛花账号采集）、`wechat_meituan`（wx-sniffer 微信小程序抓包）。这 4 个来源**实际上写的是同一张表** —— `ProductMaster` + `ProductVariant` + `ProductMedia` + `ProductAttribute` + `ProductRawPayload` 这套统一模型，`_master_to_dict()` 产出的也是来源无关的统一结构。推送侧（`/master/push-to-meituan`、`/master/push-to-eleme`、牵牛花的 overwrite-update 任务）也是从这套统一结构读、各自序列化成平台报文。

**也就是说「任一平台抓的商品推到任意平台」在架构上已经成立** —— 推送函数本身不按 `source_platform` 拒绝。但实测会卡在几个「平台特有标识转不过去」的地方：
- **类目**：美团推送硬要 `categoryId`（美团类目 ID），饿了么推送硬要 `cateId`（饿了么类目 ID）。一个从饿了么抓的商品身上带的是饿了么类目，推美团时那个 ID 对美团没意义 → 推送失败。
- **必填字段**：美团建档要 `name/pictures/wmProductSkus(含 upcCode)`、饿了么要 `barcode/images/description(HTML)` —— 这些大多能从统一字段派生，但缺类目就过不去。
- **属性**：每个平台的结构化属性体系不同；不过 `attrList`/`itemPropValues` 都允许为空，所以属性不是硬卡点（推过去商品属性空着而已）。
- **牵牛花推送**是「覆盖更新已有 SPU」不是「新建商品」—— 需要 `master → 目标牵牛花 SPU` 的映射（前端已经有这个填写入口）。

这个变更要做的是：① 把这套「字段匹配映射关系」**显式文档化**（统一 schema + 各平台 source→统一 的映射 + 统一→各平台推送报文 的映射 + 类目跨平台对照）—— 这本身就是用户要的分析交付物；② 补上**类目跨平台对照机制**（这是唯一的真卡点）；③ 让推送端点对**任意来源**的商品都稳健（缺的必填字段用统一字段派生 / 用解析出的目标平台类目 / 推送前做一个宽松的完整性校验）；④ UI 上把「推送到任意平台」的流程跑通（类目对不上时让用户现场选）。

## What Changes

- **文档化「字段匹配映射」**（spec 形式）：
  - 统一商品 schema = `ProductMaster`（标题/品牌/类目ids+path/描述/封面/源SPU等）+ `ProductVariant`（spec/combine_spec/barcode/原价/折扣价/重量+单位/月销/起购数等）+ `ProductMedia`（master_image/sku_image/detail_image/guide_image）+ `ProductAttribute`（group/key/value）+ `ProductRawPayload`（按平台归档原始报文）
  - 4 个来源的 source→统一 映射（已存在的 `_upsert_meituan_product_master` / `_upsert_*` / `_upsert_sniffer_product_master` 各自怎么填这套字段）
  - 4 个平台的 统一→推送报文 映射（`MeituanPricePushService.push_product_master` 需要的 `categoryId/name/pictures/wmProductSkus/attrList/brandId/missingRequiredInfo`；`_build_eleme_push_payload` 需要的 `barcode/cateId/images/description/itemPropValues/specialPictures/店内分类`；牵牛花 `QnhOverwriteUpdateTaskService` 需要的 `目标SPU + 标准化的图/SKU/详情图`）
  - 哪些字段是「同义不同名」（可直接映射）、哪些是「平台特有」（需对照表 / 现场选 / 拿不到就空着）
- **类目跨平台对照机制（含 DeepSeek 选类目）**：
  - 新增 `platform_category_tree` 表：缓存每个平台的官方商品标准类目树（一次性用已有商家后台 cookie 逆向「建商品选类目」接口拉全树写库；牵牛花不需要）
  - 新增 `platform_category_mapping` 表：`(from_platform, from_category_id, from_category_path?) → (to_platform, to_category_id, to_category_name, decided_by)`，逐步沉淀
  - **DeepSeek 选类目**：用后台已配的 `DEEPSEEK_*`（`api.deepseek.com` / `deepseek-v4-flash`），给一个商品（标题/品牌/规格/源平台类目路径）在目标平台类目树里**逐层走**（先选一级→再二级→再三级叶子，每层 10~50 个选项、3 次小调用）选出叶子类目
  - 「目标类目解析器」：override → 对照表命中 → 同平台原生 → **DeepSeek 选（树已抓且有把握）** → 都没有就「需人工指定」；DeepSeek/手填的结果都回写进对照表，下次同类商品直接命中、不再调 LLM
- **推送端点对任意来源稳健化**：
  - `/master/push-to-meituan` / `/master/push-to-eleme` 的 body 增加可选的 per-master `target_category_id`（对照表没命中时由 UI 传）
  - 推送时：`categoryId`/`cateId` 优先用「解析出的目标平台类目」（对照表 / UI 传入 / 自动解析），不再盲目用 master 身上的源类目
  - 其余必填字段从统一字段派生（不管来源是哪个平台）：`name←title`、`pictures←ProductMedia(master_image)`、`wmProductSkus/sku_list←ProductVariant`（`upcCode←barcode`、价格、重量、起购数）、`description(HTML)←描述+detail_images`、`attrList/itemPropValues←ProductAttribute`（映射不上就空）
  - 推送前做**宽松完整性校验**：标题、至少 1 张图、至少 1 个带 barcode 的 SKU、有目标平台类目 —— 缺则返回明确错误而不是发个残缺报文
  - 现有 `_assert_meituan_master_payload_complete` 等强校验仍只对 `meituan` 来源；跨平台推送走宽松版
- **UI（后台商品库页 `ProductMasterManage.vue`）**：
  - 「推送到美团 / 饿了么 / 牵牛花」按钮对**任意来源**的行可用（不再隐含「只能推同平台」）
  - 推送弹窗：每个选中的商品显示「解析出的目标平台类目」；解析不到的，给一个目标平台类目选择器让用户填（牵牛花的目标 SPU 选择器保留）
  - 推送成功后把用户选/解析的类目对照回写后台

## Capabilities

### New Capabilities
- `unified-product-schema`：跨 4 平台的统一商品 schema + 各来源的 source→统一 映射 + 各平台的 统一→推送报文 映射 的契约文档（哪些字段同义、哪些平台特有）
- `cross-platform-category-mapping`：类目跨平台对照表 + 目标类目解析器（override→对照表→原生→需人工）+ 推送时用它定类目 + 成功后回写对照 的契约
- `llm-category-resolution`：官方类目总表（`platform_category_tree`，用已有 cookie 自动抓）+ DeepSeek 在目标平台类目树里逐层走选叶子类目 + 选出的结果缓存进对照表 的契约
- `any-source-push`：推送端点对任意来源商品稳健化（必填字段从统一字段派生、宽松完整性校验、target_category 覆盖）的契约

### Modified Capabilities
<!-- 无（push-to-meituan / push-to-eleme 不在已有 spec 范围内；本变更全是新能力，且明确保证「同平台推送」行为不变） -->


## Impact

- 后台 `routers/products.py`：`_push_product_masters_to_cookie`（美团推送）、`_build_eleme_push_payload` / `_push_product_masters_to_eleme_cookie`（饿了么推送）、`push-to-meituan` / `push-to-eleme` 路由（加 `target_category_id`）；新增「目标类目解析器」函数
- 后台 `services/meituan_price_push_service.py`：`push_product_master` 接受外部传入的 `categoryId`（不再只从 master 取）
- 后台 `services/toolbox/qnh_overwrite_update_task_service.py`：QNH 这条本来就需要目标 SPU，基本不改（确认能接任意来源 master）
- 后台 `models.py` / DB：新增 `platform_category_mapping` 表（+ 一次建表迁移）
- 后台前端 `frontend/src/views/ProductLibrary/ProductMasterManage.vue`：推送弹窗加目标类目解析显示 + 选择器；按钮对任意来源可用
- wx-sniffer：不改（它只管抓 + 推进商品库；商品库到各平台的推送是后台的事）
- 风险：① 类目对照表初期是空的，跨平台推送需要人工选类目，用一段时间才会自动命中率上来；② 美团/饿了么的「店内分类」「平台标准类目」是两层，本变更主要解决「平台标准类目」的对照（店内分类饿了么有 allocator 自动分配，美团用 cookie 的 default_push_tag）；③ 不同平台的某些必填属性（如美团某些类目要求的关键属性）跨平台推送时填不上，可能被平台侧拒（这种只能逐个类目维护默认值，列为后续）；④ 未在真实推送链路上端到端验证「饿了么抓的→推美团」这种组合（需要有效 cookie + 真实门店），代码改完后要实测
