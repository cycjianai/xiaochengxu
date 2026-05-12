## ADDED Requirements

### Requirement: 类目跨平台对照表
后台 SHALL 维护一张 `platform_category_mapping` 表，记录「某来源平台的某类目 → 某目标平台的某类目」的对照：字段至少含 `from_platform`、`from_category_id`、`from_category_path`(可空, JSON, 用于 path 级匹配)、`to_platform`、`to_category_id`、`to_category_name`、`hit_count`、`created_at`、`updated_at`；唯一键 `(from_platform, from_category_id, to_platform)`。这张表 SHALL 可逐步沉淀（每次成功用过一个对照就 upsert/累加 hit_count）。

#### Scenario: 建表迁移
- **WHEN** 后台启动 / 跑迁移
- **THEN** `platform_category_mapping` 表 SHALL 存在（idempotent 建表），不影响其它表

#### Scenario: 成功推送后回写对照
- **WHEN** 一次跨平台推送成功，用的目标类目是用户手填的或同平台原生的
- **THEN** 后台 SHALL 把 `(master.source_platform, master 的源类目 id) → (目标平台, 实际用的类目 id+名)` upsert 进 `platform_category_mapping`（已存在则 `hit_count += 1` 并更新 `to_category_name`）

### Requirement: 目标类目解析器
SHALL 有 `resolve_target_category(db, master, to_platform, override_id=None)` 按以下优先级解析目标平台类目，返回 `{id, name, source}`（`source` ∈ {`manual`, `mapping`, `native`, `needs_manual`}）：
1. `override_id` 非空 → `{id: override_id, source: "manual"}`
2. 查 `platform_category_mapping`（`from_platform=master.source_platform`、`from_category_id=master 的源类目 id`、`to_platform`）命中 → `{id, name, source: "mapping"}` 并 `hit_count += 1`；也可按 `from_category_path` 模糊匹配
3. `to_platform == master.source_platform`（同平台推送）→ `{id: master 的源类目 id, source: "native"}`
4. 都没有 → `{id: None, source: "needs_manual"}`

#### Scenario: 同平台推送用原生类目
- **WHEN** 把一个 `meituan` 来源的 master 推到美团
- **THEN** `resolve_target_category(..., "meituan")` SHALL 返回 `source="native"`、`id` 为该 master 的源美团类目 id

#### Scenario: 对照表命中
- **WHEN** 把一个 `eleme` 来源的 master 推到美团，且 `platform_category_mapping` 里有 `(eleme, <该饿了么类目>) → (meituan, <某美团类目>)`
- **THEN** `resolve_target_category(..., "meituan")` SHALL 返回 `source="mapping"`、`id` 为那个美团类目，并把该对照的 `hit_count += 1`

#### Scenario: 没命中需要人工指定
- **WHEN** 把一个 `wechat_meituan` 来源的 master 推到饿了么，对照表里没有对应记录，且没传 override
- **THEN** `resolve_target_category(..., "eleme")` SHALL 返回 `source="needs_manual"`、`id=None`；调用方（推送端点）SHALL 据此把该 master 列入「需指定目标类目」返回给 UI，而不是用一个错误的类目硬推

#### Scenario: UI 传入覆盖
- **WHEN** UI 在弹窗里为某 master 填了目标平台类目 id 并随推送请求传上来
- **THEN** `resolve_target_category(..., override_id=<填的>)` SHALL 返回 `source="manual"`、`id` 为填的那个；推送成功后 SHALL 回写进对照表
