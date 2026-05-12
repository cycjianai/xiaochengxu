from __future__ import annotations

import json
import logging
from functools import wraps

import requests
from flask import Flask, jsonify, redirect, render_template, request, send_file, session

from config import LOG_FILE, STATIC_DIR, TEMPLATE_DIR, ensure_data_dir, load_config
from db import (
    add_log,
    backfill_missing_upc,
    bulk_upsert_products,
    clear_logs,
    delete_product,
    get_product,
    import_captured_products,
    init_db,
    list_logs,
    list_products,
    mark_synced,
    upsert_product,
)
from platform_proxy import ProxyManager
from anti_detection import health_snapshot
from cert_installer import install_cert_if_needed


ensure_data_dir()
init_db()  # 启动时初始化一次即可
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)

app = Flask(__name__, static_folder=str(STATIC_DIR), template_folder=str(TEMPLATE_DIR))
app.secret_key = "wx-sniffer-secret"
proxy_manager = ProxyManager()


def login_required(fn):
    # Auth disabled — the tool is local-only and the user wants zero friction.
    return fn


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/login")
def login_page():
    return redirect("/")


@app.get("/api/user/me")
def api_user_me():
    return jsonify({"username": "local"})


@app.get("/api/saved-cred")
def api_saved_cred():
    return jsonify({"saved": False})


@app.get("/api/products")
@login_required
def api_products():
    keyword = (request.args.get("q") or "").strip() or None
    return jsonify(list_products(keyword))


@app.post("/api/products")
@login_required
def api_create_product():
    body = request.get_json(silent=True) or {}
    product = upsert_product(body)
    add_log("INFO", f"新增商品: {product['poi_name']} / {product['product_name']} / {product['sku_id']}")
    return jsonify(product)


@app.put("/api/products/<int:product_id>")
@login_required
def api_update_product(product_id: int):
    body = request.get_json(silent=True) or {}
    product = upsert_product(body, product_id)
    add_log("INFO", f"更新商品: #{product_id} {product['product_name']}")
    return jsonify(product)


@app.delete("/api/products/<int:product_id>")
@login_required
def api_delete_product(product_id: int):
    product = get_product(product_id)
    delete_product(product_id)
    if product:
        add_log("INFO", f"删除商品: #{product_id} {product['product_name']}")
    return jsonify({"success": True})


@app.get("/api/products/<int:product_id>/json")
@login_required
def api_product_json(product_id: int):
    product = get_product(product_id)
    if not product:
        return jsonify({"error": "商品不存在"}), 404
    return jsonify(product)


@app.post("/api/products/backfill-upc")
def api_backfill_upc():
    n = backfill_missing_upc()
    if n:
        add_log("INFO", f"手动回填 UPC: {n} 行")
    return jsonify({"success": True, "backfilled": n})


@app.post("/api/products/import")
@login_required
def api_import_products():
    body = request.get_json(silent=True) or {}
    items = body.get("items") or []
    result = bulk_upsert_products(items)
    add_log("INFO", f"批量导入商品完成: created={result['created']} updated={result['updated']}")
    return jsonify({"success": True, **result})


def _post_items_to_backend(items: list) -> dict:
    """把一批商品 POST 到后台商品库端点（sync.base_url + sync.sync_path，带 host_header）。
    返回后台的返回体，失败时返回 {success: False, error, target_url}。"""
    config = load_config()["sync"]
    base_url = (config.get("base_url") or "").rstrip("/")
    sync_path = config.get("sync_path") or "/api/products/sniffer/sync-to-catalog"
    if not base_url:
        return {"success": False, "error": "未配置后台地址 sync.base_url"}
    target_url = f"{base_url}{sync_path}"
    headers = {}
    host_header = (config.get("host_header") or "").strip()
    if host_header:
        headers["Host"] = host_header
    try:
        resp = requests.post(
            target_url, json={"items": items}, headers=headers or None,
            timeout=int(config.get("timeout_seconds") or 20),
        )
        resp.raise_for_status()
        return resp.json() if resp.content else {"success": True}
    except Exception as exc:
        return {"success": False, "error": str(exc), "target_url": target_url}


@app.post("/api/internal/capture-products")
def api_internal_capture_products():
    cfg = load_config()["capture"]
    token = request.headers.get("X-Capture-Token", "")
    if token != cfg["capture_token"]:
        return jsonify({"success": False, "error": "capture token 无效"}), 401
    body = request.get_json(silent=True) or {}
    items = body.get("items") or []
    result = import_captured_products(items, source=body.get("source") or "mitmproxy")
    add_log("INFO", f"抓包导入完成: created={result['created']} updated={result['updated']}")
    # 抓一条传一条：addon 发来的 items 已是完整字段，直接转发到后台商品库
    push = _post_items_to_backend(items)
    if push.get("success"):
        c = push.get("created", 0); u = push.get("updated", 0); vt = push.get("variants_total")
        add_log("INFO", f"已自动上传后台商品库：新建 {c} / 更新 {u}" + (f"（共 {vt} 个规格）" if vt is not None else ""))
    else:
        add_log("WARN", f"自动上传后台失败: {push.get('error')}（目标: {push.get('target_url','?')}）—— 数据已存本地，稍后会随下次抓取重试")
    return jsonify({"success": True, **result, "backend_push": push})


def _load_category_cache_for_sync() -> dict:
    """读 addon 持久化的「spu_id → 三级分类」缓存，用来给 raw_json 里没分类的老商品补上。"""
    try:
        from platform_paths import app_data_dir
        p = app_data_dir() / "category_cache.json"
        if not p.exists():
            return {}
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


@app.post("/api/products/sync")
@login_required
def api_sync_products():
    config = load_config()["sync"]
    # 「当前页面上的商品」：有搜索词时只推过滤结果，否则推全部本地库
    keyword = (request.args.get("q") or "").strip() or None
    products = list_products(keyword)
    cat_cache = _load_category_cache_for_sync()
    payload_items = []
    ids = []
    for product in products:
        raw = json.loads(product["raw_json"]) if product["raw_json"] else None
        raw = raw if isinstance(raw, dict) else {}
        sku = raw.get("sku") if isinstance(raw.get("sku"), dict) else {}
        # 本地 SQLite 只存了部分列；重量/起购数/spu_id/分类/详情图/属性等从 raw_json
        # 现取（新 addon 已把这些都放进 raw；旧数据 raw.sku 里也有重量/起购数）。
        weight = weight_unit = min_purchase_qty = spu_id = variant_title = description = brand_name = None
        if sku:
            spu_id = sku.get("spu_id") or sku.get("spuId") or sku.get("cspuId")
            spu_id = str(spu_id) if spu_id not in (None, "") else None
            variant_title = sku.get("combine_spec") or sku.get("combineSpec") or None
            description = sku.get("description") or None
            mo = sku.get("min_order_count") or sku.get("minOrderCount") or sku.get("minOrderCnt")
            min_purchase_qty = int(mo) if mo not in (None, "") else None
            sn = sku.get("spec_num_unit_string") or sku.get("specNumUnitString")
            if sn:
                try:
                    obj = json.loads(sn) if isinstance(sn, str) else sn
                    ts = obj.get("total_spec") or obj.get("totalSpec") or {}
                    num = ts.get("numHigh") or ts.get("numLow") or ts.get("num")
                    if num in (None, "", 0, 0.0):
                        num = ts.get("num") if ts.get("num") not in (None, "", 0, 0.0) else None
                    weight = float(num) if num not in (None, "") else None
                    weight_unit = (str(ts.get("unit")).strip() or None) if ts.get("unit") else None
                except Exception:
                    pass
        if not spu_id:
            sp = raw.get("spu_id")
            spu_id = str(sp) if sp not in (None, "", 0, "0") else None
        # 新 addon 直接把这些放进 raw 顶层；没有则为空
        category_ids = raw.get("category_ids") if isinstance(raw.get("category_ids"), list) else []
        category_path = raw.get("category_path") if isinstance(raw.get("category_path"), list) else []
        # raw 里没分类（旧数据 / 抓详情时还没刷过分类页）→ 用持久化的分类缓存按 spu_id 反查
        if not category_ids and spu_id and spu_id in cat_cache:
            cc = cat_cache[spu_id]
            if isinstance(cc, dict) and cc.get("category_ids"):
                category_ids = [str(x) for x in (cc.get("category_ids") or [])]
                category_path = [str(x) for x in (cc.get("category_path") or [])]
        # detail_images / attributes：addon 没把解析后的列表直接放 raw，但放了原始
        # pic_content / standard_productinfo_list；这里现解析（与 addon 同逻辑）
        detail_images = []
        pc = raw.get("pic_content") or {}
        if isinstance(pc, dict):
            for u in (pc.get("contents") or pc.get("urls") or []):
                cu = (str(u).split("?", 1)[0].strip()) if isinstance(u, str) else (str(u.get("url") or "").split("?",1)[0].strip() if isinstance(u, dict) else "")
                if cu and cu not in detail_images:
                    detail_images.append(cu)
        attributes = []
        for it in (raw.get("standard_productinfo_list") or []):
            if not isinstance(it, dict):
                continue
            k = str(it.get("fieldName") or it.get("name") or "").strip()
            v = str(it.get("value") or it.get("content") or "").strip()
            if not k:
                continue
            grp = "资质" if any(x in k for x in ("注册证", "备案", "许可证", "执照", "生产企业", "批准文号", "批号", "卫生许可")) else "商品参数"
            attributes.append({"group": grp, "key": k, "value": v})
            if k in ("品牌", "品牌名") and v and not brand_name:
                brand_name = v
        # 月销：raw 里有 month_saled / month_saled_content 时按「数字非0优先，否则
        # 解析文案『月售100+』」重新算（addon 老版本只存了数字 0，文案才是真值）；
        # raw 里没有就用本地列里的值兜底。
        from capture.meituan_addon import _resolve_monthly_sales as _resolve_ms
        if (raw.get("month_saled") is not None) or raw.get("month_saled_content"):
            monthly_sales = _resolve_ms(raw.get("month_saled"), raw.get("month_saled_content"))
        else:
            monthly_sales = product.get("monthly_sales")
        payload_items.append(
            {
                "source_platform": product["source_platform"],
                "poi_name": product["poi_name"],
                "sku_id": product["sku_id"],
                "spu_id": spu_id,
                "product_name": product["product_name"],
                "upc": product["upc"] or None,
                "spec": product["spec"] or None,
                "variant_title": variant_title,
                "description": description,
                "brand_name": brand_name,
                "origin_price": product["origin_price"],
                "price": product["price"],
                "stock": product["stock"],
                "monthly_sales": monthly_sales,
                "weight": weight,
                "weight_unit": weight_unit,
                "min_purchase_qty": min_purchase_qty,
                "category_ids": category_ids,
                "category_path": category_path,
                "product_pic": product["product_pic_list"],
                "detail_images": detail_images,
                "attributes": attributes,
                "raw_json": raw,
            }
        )
        ids.append(product["id"])
    result = _post_items_to_backend(payload_items)
    if not result.get("success"):
        add_log("ERROR", f"批量同步后台失败: {result.get('error')}（目标: {result.get('target_url','?')}）")
        return jsonify({"success": False, "error": result.get("error"), "target_url": result.get("target_url")}), 500

    mark_synced(ids)
    target_url = result.get("target_url") or (config.get("base_url", "") + (config.get("sync_path") or ""))
    created = result.get("created") if isinstance(result, dict) else None
    updated = result.get("updated") if isinstance(result, dict) else None
    variants_total = result.get("variants_total") if isinstance(result, dict) else None
    summary = f"已推送 {len(payload_items)} 条到后台商品库"
    if created is not None or updated is not None:
        summary += f"（新建 {created or 0} / 更新 {updated or 0}"
        if variants_total is not None:
            summary += f"，共 {variants_total} 个规格"
        summary += "）"
    add_log("INFO", summary)
    return jsonify({
        "success": True,
        "target_url": target_url,
        "result": result,
        "count": len(payload_items),
        "summary": summary,
        "filtered_by": keyword or "",
    })


@app.get("/api/proxy/status")
@login_required
def api_proxy_status():
    return jsonify(proxy_manager.status())


@app.get("/api/proxy/wireguard-config")
@login_required
def api_proxy_wireguard_config():
    conf = proxy_manager.wireguard_client_conf()
    if not conf:
        return jsonify({"success": False, "error": "代理未运行或不是 wireguard 模式"}), 409
    # mitmproxy puts the host's autodetected LAN IP in Endpoint=. When the
    # WireGuard client runs on the SAME machine as mitmproxy (our default
    # single-host setup), 127.0.0.1 is always reachable; the LAN IP often
    # isn't (VPN tunnels, virtual NICs, etc.). Rewrite to loopback.
    cap_cfg = load_config()["capture"]
    listen_port = int(cap_cfg["listen_port"])
    import re
    conf = re.sub(r"^Endpoint\s*=.*$", f"Endpoint = 127.0.0.1:{listen_port}", conf, flags=re.MULTILINE)
    from platform_paths import mitmproxy_dir
    out_path = mitmproxy_dir() / "wx-sniffer-client.conf"
    out_path.write_text(conf, encoding="utf-8")
    fmt = (request.args.get("format") or "").lower()
    if fmt == "text":
        return conf, 200, {"Content-Type": "text/plain; charset=utf-8"}
    return jsonify({"success": True, "config": conf, "saved_to": str(out_path)})


@app.get("/api/health/anti-detection")
@login_required
def api_health_anti_detection():
    return jsonify(health_snapshot())


@app.post("/api/proxy/start")
@login_required
def api_proxy_start():
    result = proxy_manager.start()
    if result.get("running"):
        try:
            install_cert_if_needed()
        except Exception as exc:
            add_log("WARN", f"证书安装/检查失败: {exc}")
    return jsonify(result)


@app.post("/api/proxy/stop")
@login_required
def api_proxy_stop():
    return jsonify(proxy_manager.stop())


@app.get("/api/logs")
@login_required
def api_logs():
    return jsonify(list_logs())


@app.delete("/api/logs")
@login_required
def api_clear_logs():
    clear_logs()
    add_log("INFO", "日志已清空")
    return jsonify({"success": True})


@app.get("/api/logs/download")
@login_required
def api_download_logs():
    return send_file(LOG_FILE, as_attachment=True, download_name="wx-sniffer.log")


def run_server() -> None:
    cfg = load_config()["app"]
    app.run(host=cfg["host"], port=int(cfg["port"]), debug=False, use_reloader=False)
