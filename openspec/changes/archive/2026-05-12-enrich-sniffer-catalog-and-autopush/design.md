## Context

接 `wire-sniffer-to-product-catalog`（已归档）：通道打通了，但抓的字段少、要手动点同步、UI 冗余。摸了 `discover.jsonl`（`WX_SNIFFER_DISCOVER=1` dump 的真实接口响应）后确认：
- **分类**只在门店列表接口 `quickbuy/v1/poi/sputag/products` 的 `product_spu_list[].standardCategorys`（`[{id,name,level}]`，level 1/2/3）里；单品详情 `quickbuy/v2/poi/product/info` 不带分类。
- **详情图**在 `product/info` 的 `data.pic_content.contents`（带 `?w=750&h=...` query）。
- **结构化属性 + 品牌名 + 资质文字**在 `data.standard_productinfo_list`（`[{fieldName,value}]`，含「品牌」「注册证编号/备案凭证编号」「生产企业名称」等）。
- **月销**：`data.month_saled` 数字字段常为 0；真实展示量在 `data.month_saled_content` 文案（"月售100+" / "月售9999+" / "月售0" / "12人想买"）。
- 重量在 `sku.spec_num_unit_string`（JSON 串，`total_spec.{numHigh,numLow,unit}`）；起购数 `sku.min_order_count`；真实 SPU id `sku.spu_id` / `data.id`；规格全称 `sku.combine_spec`。

后台 `routers/products.py` 是真正生效的路由文件（`main.py` 引的是 `routers.products` 不是顶层 `products.py`）；`_upsert_meituan_product_master` 是「按唯一键查 master → 清空重建子表 → 重填」的覆盖式 upsert 模板；`/api/products/master` 列表按 `last_synced_at desc, id desc` 排。

## Goals / Non-Goals

**Goals:**
- wx-sniffer 抽出小程序接口能给的全部字段，跟后台美团平台来源对齐（能对齐的对齐，对不齐的明确记录原因）
- 月销取真实展示量（解析「月售100+」），不被 0 数字字段误导
- 三级分类靠两段式抓取 + 持久缓存：刷一次分类页永久记住，之后任何商品（含早抓的）都能按 spu_id 反查到分类
- 抓一条立刻自动传后台，不用手动点同步
- UI 精简到只剩「抓取开关 + 月销列 + 自动刷新 + 日志面板」
- 后台业务代码只做「接收字段」的扩展，不改既有逻辑

**Non-Goals:**
- 不抓「图文详情」之外的更多接口（资质证照图等接口不拦）
- 不做「分类没缓存到时主动发请求反查」（`sputag/products` 要 `product_tag_id` 才能查，单品详情拿不到 tag_id，会循环 —— 用持久缓存 + 按 spu_id 反查替代）
- 不做后台→wx-sniffer 反向同步
- 不动 wx-sniffer 「新增商品」手动录入表单里的「库存」字段（只改抓取展示列）
- 不打包 .app（另一个变更）

## Decisions

### D1：两段式三级分类 + 持久磁盘缓存
- addon `_is_listing()` 匹配 `LISTING_PATHS`（`/poi/sputag/products`、`/poi/product/smooth/render`、`/poi/category/products`、`/poi/products`）。命中 → `_cache_categories_from_listing(payload)`：遍历 `product_spu_list`，对每个 spu `_parse_categories`（按 `level` 升序 → `category_ids=[lvl1_id,lvl2_id,lvl3_id]`、`category_path=[lvl1_name,...]`），存进 `self._category_cache`（`OrderedDict`，FIFO 上限 5000），并 `_save_category_cache()` 写盘。
- `__init__` 时 `_load_category_cache()` 从 `~/Library/Application Support/wx-sniffer/category_cache.json` 加载。
- 单品详情解析时（`_parse_meituan_payload(payload, category_cache)`）用 `data.id`（SPU id）/ `sku.spu_id` 在缓存里找分类，匹配到就附到每个 item 的 `category_ids/category_path`。
- **server 侧兜底**：`api_sync_products` / 也可推广到 auto-push —— raw_json 里没分类时按 product 的 spu_id 去 `category_cache.json` 反查（`_load_category_cache_for_sync`）。这样「先抓商品后刷分类页」的情况，下次重推时也能补上。
- **替代（否决）**：主动发请求反查分类 —— 需要 tag_id，单品详情拿不到，循环；且多发请求增加风控特征。

### D2：月销取真实展示量
- `_resolve_monthly_sales(numeric, content_text)`：`numeric` 非 0 → 用它（更精确）；否则 `_parse_sales_text(content_text)`（正则抠数字，"X人想买/收藏/关注" 这类排除）；都没有 → 返回 numeric（可能是 0 或 None）。
- addon 解析、`server.py::api_sync_products` 重导出、`db.py::_row_to_product`（列为 0/空时从 raw_json 现解析）三处一致。UI 表格「月销」列就是它。
- **代价**："月售100+" 解析成 100 是下界（美团展示就是分桶的）；numeric 非 0 时更准。可接受，与后台美团来源用 `sellCount` 的口径一致。

### D3：抓一条传一条（auto-push）
- `server.py` 加 `_post_items_to_backend(items)`：读 `config.sync.{base_url,sync_path,host_header,timeout_seconds}`，POST `{"items": items}` 到 `base_url+sync_path`（带 `Host: boss.fuliops.cn` —— 后台 nginx 默认 vhost 对 `/api/` 返 404，必须带正确 Host），返回后台返回体或 `{success:False,error,target_url}`。
- `api_internal_capture_products`（addon 回传入口）：`import_captured_products(items)` 入库后立刻 `_post_items_to_backend(items)`（addon 发来的 items 已是完整字段，直接转发），结果写 `add_log`。失败 → WARN，数据已在本地，下次抓取会再传。
- `api_sync_products`（批量重推，保留无按钮）也改用 `_post_items_to_backend`，去重逻辑不变。
- **同步 vs 异步**：选同步（addon POST 到 localhost:5188 → 该 handler 再 POST 到后台，~1-2s/条），简单、用户立即在日志面板看到结果。用户一次点一个商品，延迟可接受。

### D4：UI 精简
- `templates/index.html`：删 5 个按钮（同步主系统/补齐 UPC/刷新列表/下载日志/清空日志），顶栏只剩开始/停止抓取（+ 健康徽章在 toolbar-left）；「新增商品」手动录入按钮保留；表头「库存」→「月销」。
- `static/app.js`：删 `syncProducts/backfillUpc/downloadLogs/clearLogs` 函数；商品表格行 `p.stock` → `p.monthly_sales`（null 显示「-」）；商品列表每 3s 自动刷新、日志面板每 2s 刷新（之前已有，保留）。
- 端点保留（无按钮）：`/api/products/sync`（带 `?q=` 批量重推）、`/api/products/backfill-upc`、`/api/logs*`（日志文件也还在磁盘）。
- **理由**：运营同事用，越少按钮越好；库存对他们没意义、月销有代表性；列表/日志自动刷新所以手动按钮多余；日志下载/清空是开发关心的，文件还在磁盘上。

### D5：后台字段对齐（`routers/products.py`）
- `SnifferProductItem` 加 12 个可选字段。`_upsert_sniffer_product_master`：
  - `master.source_product_id = item.spu_id or slug(product_name)`（真实美团 SPU id）
  - `master.brand_name = item.brand_name`（非空才覆盖）；属性里带「品牌」时回填
  - `master.category_ids / category_path = json.dumps([...])`（非空才写）
  - `variant.weight / weight_unit / min_purchase_qty / monthly_sales / variant_title`
  - `ProductMedia(media_type="detail_image", url=...)` —— add-if-new（按 url 去重，不动 master_image）
  - `ProductAttribute(attr_group, attr_key, attr_value)` —— add-if-new（按 group+key 去重）；资质类字段名（注册证/备案/许可证/执照/生产企业/批准文号…）归到「资质」组
- 不改 `_upsert_meituan_product_master`、`/master/*`、`/sniffer/sync`（顶层 products.py 的，本就没生效）等既有逻辑。
- **替代（否决）**：detail_image / attributes 用「整组替换」（像美团 cookie 采集那样 `.clear()`）—— wx-sniffer 是逐个点商品抓的，可能只抓到部分，整组替换会丢；用 add-if-new。

### D6：后台前端两处微调
- `ProductMasterManage.vue`：来源平台标签映射 + 筛选下拉加 `wechat_meituan → 小程序美团`。
- 表格「创建时间」列 → 「同步时间」（显示 `last_synced_at`，也就是行排序依据）—— 解决「重复推送的商品 created_at 是旧的、看着像没排上来」的误会。`npm run build` 重新构建。

## Risks / Trade-offs

- **[先抓商品后刷分类页 → 该商品没分类]** → server 侧 `api_sync_products` 按 spu_id 查持久缓存兜底（下次重推时补上）；auto-push 路径暂不做这个兜底（每条立刻推，推时分类没缓存就没有），日志会提示「先进店刷一下分类」。
- **[auto-push 同步阻塞 addon]** → ~1-2s/条，用户一次点一个商品，可接受；如成为瓶颈再改后台线程。
- **[auto-push 失败静默丢]** → 不会丢：数据已存本地 SQLite，写 WARN 日志，下次抓取会再随 `/api/internal/capture-products` 触发（不过那次只带新抓的 items —— 老的失败项要靠手动 `/api/products/sync` 重推）。
- **[月销 "月售100+" 解析为 100 是下界]** → 接受；numeric 非 0 时更准。
- **[detail_image / attribute add-if-new 不删旧]** → 商品详情变了的话旧详情图/属性会残留；小概率，可接受。
- **[UPC / 资质图 / 部分品牌名拿不到]** → 数据源限制（商家没填 / 买家侧接口不返回），非缺陷，已在 README + proposal 记录。
- **[后台 routers/products.py 直接 sed-patch]** → 已备份 `.bak2/.bak3`、`py_compile` 校验、服务重启验证；回滚 = 还原 bak。

## Migration Plan

1. addon：加 `LISTING_PATHS` + 持久分类缓存 + 详情多字段解析 + `_resolve_monthly_sales`；本地 smoke（构造假 listing/detail payload 验证解析）
2. 本地 DB：`init_db` 加 `monthly_sales` 列（ALTER，idempotent）；`_row_to_product` 月销兜底
3. server：`_post_items_to_backend` + `api_internal_capture_products` auto-push + `api_sync_products` 透传新字段；模拟一次 `/api/internal/capture-products` 验证 auto-push 到后台
4. UI：删按钮、库存→月销；JS 语法检查、无悬空引用
5. 后台 `routers/products.py`：patch2（spu_id/variant_title/description/weight/min_qty）+ patch3（brand/category/detail_image/attribute/monthly_sales）；`py_compile` + 重启 + curl 验证（含分类/详情图/属性/月销全套字段入库）
6. 后台前端：标签 + 同步时间列；`npm run build`
7. README 同步更新

**回滚**：addon/db/server/UI 改动在 git/文件层 revert；后台 `routers/products.py` 还原 `.bak3` → 重启；前端 `ProductMasterManage.vue` 还原 `.bak2` → rebuild。`product_masters` 里 `wechat_meituan` 来源的行可单独 DELETE，不影响其它平台。

## Open Questions

- 是否给 auto-push 也加「分类没缓存到时按 spu_id 查持久缓存」兜底？（现在只在 `api_sync_products` 有）—— 本次不做，因为 auto-push 时商品刚抓，理想流程是先刷分类页；如果实测发现经常漏，再加。
- auto-push 失败的「老失败项」目前只能手动 `/api/products/sync` 重推 —— 要不要加个「定时重试本地未成功上传的」？本次不做，列为后续。
- 是否要在 wx-sniffer UI 上显示「这条已上传后台 / 上传失败」的状态标记（而不只是日志面板）？本次不做。
