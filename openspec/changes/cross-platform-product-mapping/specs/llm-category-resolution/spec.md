## ADDED Requirements

### Requirement: 官方商品类目总表
后台 SHALL 维护一张 `platform_category_tree` 表，缓存每个平台（至少 `meituan`、`eleme`；`qianniuhua` 不需要）的官方商品标准类目树。字段至少含 `platform`、`category_id`、`category_name`、`parent_id`(可空)、`level`、`is_leaf`、`full_path`(JSON 数组, 从根到本节点的中文名)、`full_path_ids`(JSON 数组)、`fetched_at`；唯一键 `(platform, category_id)`。SHALL 提供一个「抓取类目树」的操作：用 `cookies` 表里已有的对应平台商家后台 cookie，逆向「建商品时选类目」那个类目树接口，分页/递归把全树拉下来写库（idempotent，可重复刷新）。

#### Scenario: 抓取并缓存类目树
- **WHEN** 触发某平台的「抓取类目树」操作（带一个有效的该平台商家后台 cookie）
- **THEN** 后台 SHALL 把该平台的官方商品标准类目全树写入 `platform_category_tree`（每个节点一行，含层级、parent、是否叶子、从根到本节点的路径），可重复运行（已存在则更新 `fetched_at` 等）

#### Scenario: 类目树未抓时降级
- **WHEN** 某平台的 `platform_category_tree` 还是空的（没抓过）
- **THEN** 目标类目解析器 SHALL 跳过 LLM 这一步，直接返回 `needs_manual`（让用户在 UI 现场填类目），不报错

### Requirement: DeepSeek 在目标平台类目树里选叶子类目
SHALL 有 `llm_pick_category(target_platform, product_hint, db) -> {id, name, path, confidence}`，用后台已配的 DeepSeek（`DEEPSEEK_BASE_URL=https://api.deepseek.com`、`DEEPSEEK_CHAT_MODEL=deepseek-v4-flash`、`DEEPSEEK_API_KEY`），在目标平台的 `platform_category_tree` 里**逐层走树**选出一个叶子类目：① 给 LLM `product_hint`（标题、品牌、规格、源平台类目路径、若干关键属性）+ level-1 类目名列表 → 让它选一个一级类目；② 在那个一级下取 level-2 列表 → 选二级；③ 在那个二级下取 level-3 叶子列表 → 选叶子。每层只给该层的选项（10~50 个），共约 3 次小调用。SHALL 让 LLM 同时给出置信度（high/medium/low）或理由；`confidence` 为 `low` 时 SHALL 视为「没选出来」。

#### Scenario: 逐层选出叶子类目
- **WHEN** 对一个商品（如「洽洽 香瓜子 308g/袋」，源类目路径「休闲食品/坚果炒货/瓜子」）调 `llm_pick_category("meituan", hint, db)`，且美团类目树已抓
- **THEN** SHALL 通过 3 次 DeepSeek 调用（选一级→二级→三级）选出一个美团叶子类目，返回 `{id, name, path: ["零食","炒货坚果","葵花籽"]之类, confidence}`；3 层每层的选项 SHALL 只是该层在树里的实际子类目

#### Scenario: LLM 没把握
- **WHEN** `llm_pick_category` 返回的 `confidence` 是 `low`（商品信息太少 / 树里没合适的）
- **THEN** SHALL 视为没选出来，解析器据此返回 `needs_manual`

#### Scenario: DeepSeek 不可达不阻塞
- **WHEN** DeepSeek API 调用失败（超时 / key 失效 / 网络）
- **THEN** `llm_pick_category` SHALL 返回「没选出来」（不抛异常打断推送流程），解析器返回 `needs_manual`，日志记一条 WARN

### Requirement: LLM 选出的类目缓存进对照表
当 `llm_pick_category` 成功选出一个目标类目，解析器 SHALL 把这条 `(master.source_platform, master 的源类目 id, master 的源类目 path) → (target_platform, 选出的类目 id+名)` upsert 进 `platform_category_mapping`（`decided_by="llm"`），使下次同源类目的商品直接命中对照表、不再调 LLM。

#### Scenario: 第二次同类商品不调 LLM
- **WHEN** 第一次某「源类目X→目标平台Y」靠 DeepSeek 选出了类目 Z 并缓存；之后又来一个源类目同样是 X 的商品要推到 Y
- **THEN** 解析器 SHALL 在「查对照表」那一步直接命中（返回 Z，`source="mapping"`），SHALL NOT 再调 DeepSeek
