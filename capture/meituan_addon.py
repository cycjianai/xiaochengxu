"""mitmproxy addon — 拦截美团小程序商品接口，解析后回传 Flask 后台

两段式抓取：
  1) 进店浏览分类列表（quickbuy/v1/poi/sputag/products、smooth/render）→ 缓存
     {spu_id → 三级分类}（分类只在列表接口里，单品详情接口没有）。
  2) 点进单品（quickbuy/v2/poi/product/info）→ 解析 sku/价格/库存/月销/详情图/
     结构化属性，并把第 1 步缓存的三级分类匹配进来 → 回传本地 Flask。

字段来源（详见各 _parse_* 函数）：
  - sku 基础：data.skus[].{id,spu_id,name,spec,combine_spec,upccode,price,
    origin_price,stock,min_order_count,spec_num_unit_string,description}
  - 月销：data.month_saled (+ data.month_saled_content 文案)
  - 详情图：data.pic_content.contents
  - 结构化属性 + 品牌名 + 资质文字：data.standard_productinfo_list
  - 主图：data.opt_pictures / data.pictures
  - 三级分类：sputag 列表 product_spu_list[].standardCategorys（level 1/2/3）
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections import OrderedDict
from typing import Any

from mitmproxy import http

from capture.http_client import HttpPoster

logger = logging.getLogger(__name__)

# 临时诊断开关：env WX_SNIFFER_DISCOVER=1 时，把含 product/categor/detail/spu/menu
# 的接口（method+url+完整响应体）逐行 dump 到 logs/discover.jsonl，用来摸接口结构。
_DISCOVER = os.environ.get("WX_SNIFFER_DISCOVER", "") in ("1", "true", "yes")
_DISCOVER_KEYWORDS = ("product", "categor", "detail", "spu", "menu", "tag", "list", "reuse", "goods")


def _discover_path() -> str:
    from platform_paths import logs_dir
    return str(logs_dir() / "discover.jsonl")


def _category_cache_path() -> str:
    """持久化的「spu_id → 三级分类」缓存文件。进店刷一次分类页就永久记住，
    跨重启、跨会话累积；同步时（server.py）也会读它给老商品补分类。"""
    from platform_paths import app_data_dir
    return str(app_data_dir() / "category_cache.json")


# 单品详情接口（点进商品时触发）。按子串匹配以兼容 v1/v2/v3、/quickbuy/、/mtweapp/ 等变体。
FILTER_PATHS = (
    "/poi/product/info",
    "/poi/product/detail",
    "/product/info",
    "/product/detail",
    "/product/spu/detail",
    "/sku/detail",
)

# 门店分类/商品列表接口（进店浏览时触发）。响应里 product_spu_list[].standardCategorys
# 携带每个 SPU 的三级分类——单品详情接口没有这个，所以靠这一步缓存。
LISTING_PATHS = (
    "/poi/sputag/products",
    "/poi/product/smooth/render",
    "/poi/category/products",
    "/poi/products",
)

# 资质类字段名关键词（standard_productinfo_list 里命中这些的归到「资质」组）
_QUALIFICATION_KEYS = ("注册证", "备案", "许可证", "执照", "生产企业", "经营企业", "批准文号", "批号", "卫生许可")


def _g(d: dict, *keys, default=None):
    for k in keys:
        if isinstance(d, dict) and k in d:
            return d[k]
    return default


def _clean_pic_url(url: Any) -> str:
    """详情图 URL 形如 http://p0.meituan.net/sgopen/xxx.jpg?w=750&h=874；去掉尺寸 query。"""
    s = str(url or "").strip()
    if not s:
        return ""
    return s.split("?", 1)[0]


def _parse_categories(spu: dict) -> tuple[list[str], list[str]]:
    """从 sputag 列表里某个 spu 的 standardCategorys 解析三级分类。
    standardCategorys = [{id, name, level}, ...]，level 1=一级 2=二级 3=三级。
    返回 (category_ids 按 level 升序, category_path 按 level 升序)。"""
    cats = spu.get("standardCategorys") or spu.get("standard_categorys") or []
    if not isinstance(cats, list):
        return [], []
    valid = [c for c in cats if isinstance(c, dict) and c.get("id") and c.get("name")]
    valid.sort(key=lambda c: c.get("level") or 99)
    ids = [str(c["id"]) for c in valid]
    path = [str(c["name"]) for c in valid]
    return ids, path


def _parse_detail_images(data: dict) -> list[str]:
    """图文详情图：data.pic_content = {"type":1, "contents":["http://...?w=750&h=...", ...]}"""
    pc = data.get("pic_content") or {}
    if not isinstance(pc, dict):
        return []
    contents = pc.get("contents") or pc.get("urls") or []
    if not isinstance(contents, list):
        return []
    out: list[str] = []
    for u in contents:
        cu = _clean_pic_url(u if isinstance(u, str) else (u.get("url") if isinstance(u, dict) else ""))
        if cu:
            out.append(cu)
    # 去重保序
    seen, dedup = set(), []
    for u in out:
        if u not in seen:
            seen.add(u)
            dedup.append(u)
    return dedup


def _parse_attributes(data: dict) -> tuple[list[dict[str, str]], str]:
    """结构化属性：data.standard_productinfo_list = [{"fieldName":"品牌","value":"杜蕾斯"}, ...]
    返回 (attributes 列表[{group,key,value}], brand_name)。资质类字段归到「资质」组，
    其余归「商品参数」组；命中「品牌」字段时同时抽出 brand_name。"""
    lst = data.get("standard_productinfo_list") or data.get("standardProductInfoList") or []
    if not isinstance(lst, list):
        return [], ""
    attrs: list[dict[str, str]] = []
    brand_name = ""
    for item in lst:
        if not isinstance(item, dict):
            continue
        key = str(item.get("fieldName") or item.get("name") or item.get("key") or "").strip()
        val = str(item.get("value") or item.get("content") or item.get("text") or "").strip()
        if not key:
            continue
        group = "资质" if any(kw in key for kw in _QUALIFICATION_KEYS) else "商品参数"
        attrs.append({"group": group, "key": key, "value": val})
        if key in ("品牌", "品牌名", "Brand", "brand") and val and not brand_name:
            brand_name = val
    # data.attrs 里有时也有结构化属性（[{groupName,name,value}]）
    extra = data.get("attrs") or []
    if isinstance(extra, list):
        for item in extra:
            if not isinstance(item, dict):
                continue
            key = str(item.get("name") or item.get("attrName") or item.get("key") or "").strip()
            val = str(item.get("value") or item.get("attrValue") or item.get("content") or "").strip()
            if not key:
                continue
            grp = str(item.get("groupName") or item.get("group") or "商品参数").strip() or "商品参数"
            attrs.append({"group": grp, "key": key, "value": val})
    return attrs, brand_name


def _parse_videos(data: dict) -> list[dict[str, str]]:
    """data.product_video_infos / data.live_info 里的视频。返回 [{url, cover}]。"""
    out: list[dict[str, str]] = []
    for v in (data.get("product_video_infos") or []):
        if isinstance(v, dict):
            url = _clean_pic_url(v.get("videoUrl") or v.get("url") or v.get("video_url") or "")
            cover = _clean_pic_url(v.get("coverUrl") or v.get("cover") or v.get("firstFrameUrl") or "")
            if url:
                out.append({"url": url, "cover": cover})
    return out


def _parse_sales_text(text: Any) -> int | None:
    """从 "月售100+" / "月售6000+" / "月售0" / "已售200" 这类文案里抠出数字。
    "X人想买" / "X人收藏" 这类不是销量，返回 None。"""
    if not text:
        return None
    s = str(text).strip()
    if any(kw in s for kw in ("想买", "收藏", "关注", "人想")):
        return None
    import re
    m = re.search(r"(\d[\d,]*)", s)
    if not m:
        return None
    try:
        return int(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _resolve_monthly_sales(numeric: Any, content_text: Any) -> int | None:
    """月销取值：数字字段 month_saled 非 0 时用它（更精确）；否则用 month_saled_content
    文案解析值（"月售100+" → 100，这才是商家后台真实展示的量）。两者都没有 → None。"""
    n_num = None
    try:
        n_num = int(numeric) if numeric not in (None, "") else None
    except (TypeError, ValueError):
        n_num = None
    if n_num:  # 非 0 非 None
        return n_num
    n_text = _parse_sales_text(content_text)
    if n_text is not None:
        return n_text
    return n_num  # 可能是 0（"月售0"）或 None


def _read_weight(sku: dict) -> tuple[float | None, str | None]:
    """美团小程序把净含量塞在 sku.spec_num_unit_string，形如
    {"total_spec":{"num":0.0,"numHigh":40.0,"numLow":40.0,"unit":"g"}, ...}
    取 numHigh（与 numLow 一般相等；区间品取上限），unit 作重量单位。"""
    raw = sku.get("spec_num_unit_string") or sku.get("specNumUnitString")
    if not raw:
        return None, None
    try:
        obj = json.loads(raw) if isinstance(raw, str) else raw
        ts = obj.get("total_spec") or obj.get("totalSpec") or {}
        num = ts.get("numHigh") or ts.get("numLow") or ts.get("num")
        unit = ts.get("unit")
        if num in (None, "", 0, 0.0):
            num = ts.get("num") if ts.get("num") not in (None, "", 0, 0.0) else None
        return (float(num) if num not in (None, "") else None), (str(unit).strip() if unit else None)
    except Exception:
        return None, None


_UPC_KEYS = ("upccode", "upcCode", "upc", "barcode", "bar_code", "barCode")


def _read_upc(d: dict) -> str:
    if not isinstance(d, dict):
        return ""
    for k in _UPC_KEYS:
        v = d.get(k)
        if v:
            return str(v).strip()
    return ""


def _parse_meituan_payload(payload: dict[str, Any], category_cache: dict | None = None) -> list[dict[str, Any]]:
    """解析单品详情接口（quickbuy/v2/poi/product/info）的响应 → 每个 SKU 一条记录。

    category_cache: 由 _handle_listing 维护的 {spu_id(str) → {category_ids, category_path}}；
    用来把进店时缓存的三级分类匹配到当前 SPU 上。
    """
    data = payload.get("data") or {}
    if not isinstance(data, dict):
        return []

    skus = data.get("skus") or []
    if not isinstance(skus, list) or not skus:
        return []

    poi_info = data.get("poi_info") or {}
    poi_name = _g(poi_info, "poi_name", "name", "store_name", default="") or ""
    poi_pic_obj = _g(poi_info, "pic_url", "picture", "logo", default="") or ""

    # SPU 级主图：优先 opt_pictures（最全），其次 pictures
    spu_pics: list[str] = []
    for src_key in ("opt_pictures", "pictures"):
        for pic in (data.get(src_key) or []):
            if isinstance(pic, str) and pic.strip():
                spu_pics.append(_clean_pic_url(pic))
            elif isinstance(pic, dict):
                u = pic.get("url") or pic.get("pic_url") or pic.get("picture")
                if u:
                    spu_pics.append(_clean_pic_url(u))
        if spu_pics:
            break
    # 去重保序
    _seen, _dedup = set(), []
    for u in spu_pics:
        if u and u not in _seen:
            _seen.add(u); _dedup.append(u)
    spu_pics = _dedup

    spu_name = data.get("name") or _g(data, "title", "product_name", default="") or ""
    spu_id_top = data.get("id") or data.get("spu_id") or data.get("cspu_id")
    spu_id_top = str(spu_id_top) if spu_id_top not in (None, "", 0, "0") else None

    monthly_sales_text = str(data.get("month_saled_content") or "").strip() or None
    monthly_sales = _resolve_monthly_sales(data.get("month_saled"), monthly_sales_text)

    detail_images = _parse_detail_images(data)
    attributes, brand_name = _parse_attributes(data)
    videos = _parse_videos(data)

    # 三级分类：用 SPU id 在缓存里找（详情接口本身不带分类）
    category_ids: list[str] = []
    category_path: list[str] = []
    if category_cache:
        for key in (spu_id_top,):
            if key and key in category_cache:
                cc = category_cache[key]
                category_ids = list(cc.get("category_ids") or [])
                category_path = list(cc.get("category_path") or [])
                break

    items: list[dict[str, Any]] = []
    for sku in skus:
        if not isinstance(sku, dict):
            continue
        sku_id = sku.get("id")
        if not sku_id:
            continue
        # 缓存也可能用 sku.spu_id 索引，二次兜底
        sku_spu_id = sku.get("spu_id") or sku.get("spuId") or sku.get("cspuId")
        sku_spu_id = str(sku_spu_id) if sku_spu_id not in (None, "", 0, "0") else None
        if not category_ids and category_cache and sku_spu_id and sku_spu_id in category_cache:
            cc = category_cache[sku_spu_id]
            category_ids = list(cc.get("category_ids") or [])
            category_path = list(cc.get("category_path") or [])

        sku_pics = []
        pic = sku.get("pic_url") or sku.get("picture") or sku.get("pic")
        if pic:
            sku_pics.append(_clean_pic_url(pic))
        sku_pics.extend(spu_pics)
        # 去重保序
        _s, _d = set(), []
        for u in sku_pics:
            if u and u not in _s:
                _s.add(u); _d.append(u)
        sku_pics = _d

        weight, weight_unit = _read_weight(sku)
        min_order = sku.get("min_order_count") or sku.get("minOrderCount") or sku.get("minOrderCnt")

        items.append(
            {
                "source_platform": "wechat_meituan",
                "poi_name": poi_name or "未知门店",
                "poi_pic": poi_pic_obj,
                "sku_id": str(sku_id),
                "spu_id": (sku_spu_id or spu_id_top),
                "upc": _read_upc(sku),
                "product_name": sku.get("name") or spu_name,
                "spec": sku.get("spec") or "",
                "variant_title": sku.get("combine_spec") or sku.get("combineSpec") or "",
                "description": sku.get("description") or "",
                "brand_name": brand_name or None,
                "origin_price": sku.get("origin_price") or sku.get("originPrice") or 0,
                "price": sku.get("price") or 0,
                "stock": sku.get("stock") or 0,
                "monthly_sales": monthly_sales,
                "monthly_sales_text": monthly_sales_text,
                "weight": weight,
                "weight_unit": weight_unit,
                "min_purchase_qty": int(min_order) if min_order not in (None, "") else None,
                "category_ids": list(category_ids),
                "category_path": list(category_path),
                "product_pic": sku_pics,
                "detail_images": list(detail_images),
                "attributes": list(attributes),
                "videos": list(videos),
                "raw_json": {
                    "sku": sku,
                    "poi_info": poi_info,
                    "spu_name": spu_name,
                    "spu_id": spu_id_top,
                    "month_saled": data.get("month_saled"),
                    "month_saled_content": data.get("month_saled_content"),
                    "pic_content": data.get("pic_content"),
                    "opt_pictures": data.get("opt_pictures"),
                    "standard_productinfo_list": data.get("standard_productinfo_list"),
                    "attrs": data.get("attrs"),
                    "product_video_infos": data.get("product_video_infos"),
                    "category_ids": list(category_ids),
                    "category_path": list(category_path),
                },
            }
        )
    return items


class MeituanCaptureAddon:
    """In-process mitmproxy addon. Loaded via `master.addons.add(...)`."""

    _CATEGORY_CACHE_MAX = 5000

    def __init__(self, import_url: str, capture_token: str) -> None:
        self._import_url = import_url
        self._capture_token = capture_token
        self._poster = HttpPoster()
        self._count = 0
        # {spu_id(str) → {"category_ids": [...], "category_path": [...]}}，FIFO 上限
        self._category_cache: "OrderedDict[str, dict]" = OrderedDict()
        self._load_category_cache()

    # -------- persistent category cache --------

    def _load_category_cache(self) -> None:
        try:
            p = _category_cache_path()
            if not os.path.exists(p):
                return
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for k, v in data.items():
                    if isinstance(v, dict) and v.get("category_ids"):
                        self._category_cache[str(k)] = {
                            "category_ids": [str(x) for x in (v.get("category_ids") or [])],
                            "category_path": [str(x) for x in (v.get("category_path") or [])],
                        }
            while len(self._category_cache) > self._CATEGORY_CACHE_MAX:
                self._category_cache.popitem(last=False)
            logger.info(f"category cache loaded: {len(self._category_cache)} entries")
        except Exception as exc:
            logger.warning(f"load category cache failed: {exc}")

    def _save_category_cache(self) -> None:
        try:
            p = _category_cache_path()
            tmp = p + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(dict(self._category_cache), f, ensure_ascii=False)
            os.replace(tmp, p)
        except Exception as exc:
            logger.warning(f"save category cache failed: {exc}")

    # -------- path matching --------

    def _is_detail(self, flow: http.HTTPFlow) -> bool:
        path = flow.request.path or ""
        return any(p in path for p in FILTER_PATHS)

    def _is_listing(self, flow: http.HTTPFlow) -> bool:
        path = flow.request.path or ""
        return any(p in path for p in LISTING_PATHS)

    # -------- category cache --------

    def _cache_categories_from_listing(self, payload: dict) -> int:
        data = payload.get("data") or {}
        if not isinstance(data, dict):
            return 0
        spu_list = data.get("product_spu_list") or data.get("productSpuList") or data.get("spu_list") or []
        if not isinstance(spu_list, list):
            return 0
        n = 0
        for spu in spu_list:
            if not isinstance(spu, dict):
                continue
            spu_id = spu.get("id") or spu.get("spu_id") or spu.get("spuId")
            if spu_id in (None, "", 0, "0"):
                continue
            ids, path = _parse_categories(spu)
            if not ids:
                continue
            key = str(spu_id)
            self._category_cache.pop(key, None)
            self._category_cache[key] = {"category_ids": ids, "category_path": path}
            n += 1
            while len(self._category_cache) > self._CATEGORY_CACHE_MAX:
                self._category_cache.popitem(last=False)
        if n:
            self._save_category_cache()
        return n

    # -------- mitmproxy hooks --------

    def request(self, flow: http.HTTPFlow) -> None:
        try:
            url = flow.request.pretty_url
            short = url if len(url) <= 160 else url[:157] + "..."
            if self._is_detail(flow):
                from db import add_log
                add_log("INFO", f"[请求] {short}")
            elif _DISCOVER:
                from db import add_log
                add_log("DEBUG", f"{flow.request.method} {short}")
            else:
                logger.debug(f"flow {flow.request.method} {url}")
        except Exception:
            pass

    def _discover_dump(self, flow: http.HTTPFlow) -> None:
        if not _DISCOVER:
            return
        try:
            path = (flow.request.path or "").lower()
            if not any(k in path for k in _DISCOVER_KEYWORDS):
                return
            body = flow.response.get_text(strict=False) or ""
            try:
                parsed = json.loads(body)
            except Exception:
                return
            rec = {
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "method": flow.request.method,
                "url": flow.request.pretty_url,
                "host": flow.request.host,
                "path": flow.request.path,
                "status": flow.response.status_code,
                "req_body": (flow.request.get_text(strict=False) or "")[:2000],
                "resp": parsed,
            }
            with open(_discover_path(), "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            from db import add_log
            add_log("INFO", f"[discover] {flow.request.host}{flow.request.path[:80]} -> {flow.response.status_code} (已 dump)")
        except Exception as exc:
            logger.warning(f"discover dump failed: {exc}")

    def response(self, flow: http.HTTPFlow) -> None:
        self._discover_dump(flow)

        # 进店浏览列表：只用来缓存 {spu_id → 三级分类}，不在这里抓商品
        if self._is_listing(flow):
            try:
                payload = json.loads(flow.response.get_text(strict=False) or "{}")
                cached = self._cache_categories_from_listing(payload)
                if cached:
                    from db import add_log
                    add_log("INFO", f"[分类缓存] {flow.request.path[:60]} → 缓存 {cached} 个 SPU 的三级分类（共 {len(self._category_cache)}）")
            except Exception as exc:
                logger.warning(f"listing parse failed: {exc}")
            return

        if not self._is_detail(flow):
            return

        try:
            payload = json.loads(flow.response.get_text(strict=False) or "{}")
        except Exception as exc:
            logger.warning(f"响应体 JSON 解析失败: {exc}")
            return

        items = _parse_meituan_payload(payload, category_cache=self._category_cache)
        self._count += 1
        try:
            from db import add_log
            cat = (items[0].get("category_path") if items else None) or []
            add_log(
                "INFO",
                f"[#{self._count}] {flow.request.method} {flow.request.pretty_url} -> {flow.response.status_code} "
                f"items={len(items)} 分类={'/'.join(cat) if cat else '（缓存里没有，先进店刷一下分类）'}",
            )
        except Exception:
            pass

        if not items:
            return

        try:
            self._poster.post_json(
                self._import_url,
                {"source": "mitmproxy", "items": items},
                headers={"X-Capture-Token": self._capture_token},
                timeout=15,
            )
        except Exception as exc:
            logger.error(f"回传失败: {exc}")
