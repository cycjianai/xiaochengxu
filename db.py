from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime

from config import ensure_data_dir
from platform_paths import db_path


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def now_minute() -> str:
    """Year-month-day-hour-minute timestamp (no seconds). Used for the
    user-facing 'synced at' column where second precision is just noise."""
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _migrate_synced_at_to_minute() -> None:
    """One-shot DB migration: truncate any historical synced_at values from
    'YYYY-MM-DD HH:MM:SS' to 'YYYY-MM-DD HH:MM' so the UI displays only
    minute precision uniformly. Safe to run on every boot — only rewrites
    rows whose synced_at is longer than 16 chars."""
    try:
        with sqlite3.connect(_db()) as conn:
            conn.execute(
                "UPDATE products SET synced_at = substr(synced_at, 1, 16) "
                "WHERE synced_at IS NOT NULL AND length(synced_at) > 16"
            )
            conn.commit()
    except Exception:
        pass  # table may not exist yet on first boot


def _db() -> str:
    return str(db_path())


def init_db() -> None:
    ensure_data_dir()
    _migrate_synced_at_to_minute()
    with sqlite3.connect(_db()) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_platform TEXT NOT NULL DEFAULT 'wechat_meituan',
                poi_name TEXT NOT NULL,
                sku_id TEXT NOT NULL,
                product_name TEXT NOT NULL,
                upc TEXT,
                spec TEXT,
                origin_price REAL DEFAULT 0,
                price REAL DEFAULT 0,
                stock INTEGER DEFAULT 0,
                monthly_sales INTEGER,
                product_pic TEXT,
                raw_json TEXT,
                synced_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(source_platform, poi_name, sku_id)
            )
            """
        )
        # 老库可能没有 monthly_sales 列，补一下（idempotent）
        try:
            conn.execute("ALTER TABLE products ADD COLUMN monthly_sales INTEGER")
        except sqlite3.OperationalError:
            pass  # 列已存在
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                level TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


@contextmanager
def get_conn():
    conn = sqlite3.connect(_db())
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def add_log(level: str, message: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO logs(level, message, created_at) VALUES(?, ?, ?)",
            (level.upper(), message, now_str()),
        )


def list_logs(limit: int = 300) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, level, message, created_at FROM logs ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [
        {
            "id": row["id"],
            "level": row["level"],
            "message": row["message"],
            "time": row["created_at"],
        }
        for row in reversed(rows)
    ]


def clear_logs() -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM logs")


def _resolve_ms_from_raw(raw_text: str | None):
    """从 raw_json 里按『数字非0优先，否则解析月售文案』算出月销（与 addon 同逻辑）。"""
    if not raw_text:
        return None
    try:
        raw = json.loads(raw_text)
    except Exception:
        return None
    if not isinstance(raw, dict):
        return None
    num = raw.get("month_saled")
    txt = raw.get("month_saled_content")
    try:
        n_num = int(num) if num not in (None, "") else None
    except (TypeError, ValueError):
        n_num = None
    if n_num:
        return n_num
    if txt:
        s = str(txt).strip()
        if not any(kw in s for kw in ("想买", "收藏", "关注", "人想")):
            import re
            m = re.search(r"(\d[\d,]*)", s)
            if m:
                try:
                    return int(m.group(1).replace(",", ""))
                except ValueError:
                    pass
    return n_num


def _row_to_product(row: sqlite3.Row) -> dict:
    pics = []
    if row["product_pic"]:
        try:
            pics = json.loads(row["product_pic"])
        except Exception:
            pics = []
    try:
        monthly_sales = row["monthly_sales"]
    except (IndexError, KeyError):
        monthly_sales = None
    # 列里没值或为 0 时，尝试从 raw_json 的 month_saled_content（"月售100+"）现解析
    if monthly_sales in (None, 0):
        derived = _resolve_ms_from_raw(row["raw_json"])
        if derived not in (None,):
            monthly_sales = derived
    return {
        "id": row["id"],
        "source_platform": row["source_platform"],
        "poi_name": row["poi_name"],
        "sku_id": row["sku_id"],
        "product_name": row["product_name"],
        "upc": row["upc"] or "",
        "spec": row["spec"] or "",
        "origin_price": row["origin_price"] or 0,
        "price": row["price"] or 0,
        "stock": row["stock"] or 0,
        "monthly_sales": monthly_sales,
        "product_pic": row["product_pic"] or "[]",
        "product_pic_list": pics,
        "raw_json": row["raw_json"],
        "synced_at": row["synced_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def list_products(keyword: str | None = None) -> list[dict]:
    sql = """
        SELECT * FROM products
        WHERE 1=1
    """
    params: list[object] = []
    if keyword:
        like = f"%{keyword.strip()}%"
        sql += " AND (product_name LIKE ? OR poi_name LIKE ? OR upc LIKE ? OR sku_id LIKE ?)"
        params.extend([like, like, like, like])
    sql += " ORDER BY updated_at DESC, id DESC"
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_product(row) for row in rows]


def get_product(product_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    return _row_to_product(row) if row else None


def _coerce_monthly_sales(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def upsert_product(data: dict, product_id: int | None = None) -> dict:
    payload = {
        "source_platform": (data.get("source_platform") or "wechat_meituan").strip(),
        "poi_name": (data.get("poi_name") or "").strip(),
        "sku_id": str(data.get("sku_id") or "").strip(),
        "product_name": (data.get("product_name") or "").strip(),
        "upc": ((data.get("upc") or "").strip() or None),
        "spec": ((data.get("spec") or "").strip() or None),
        "origin_price": float(data.get("origin_price") or 0),
        "price": float(data.get("price") or 0),
        "stock": int(data.get("stock") or 0),
        "monthly_sales": _coerce_monthly_sales(data.get("monthly_sales")),
        "product_pic": json.dumps(data.get("product_pic") or [], ensure_ascii=False),
        "raw_json": json.dumps(data.get("raw_json"), ensure_ascii=False) if data.get("raw_json") is not None else None,
    }
    if not payload["poi_name"] or not payload["sku_id"] or not payload["product_name"]:
        raise ValueError("poi_name、sku_id、product_name 不能为空")

    current = now_str()
    with get_conn() as conn:
        if product_id is not None:
            conn.execute(
                """
                UPDATE products
                SET source_platform=?, poi_name=?, sku_id=?, product_name=?, upc=?, spec=?,
                    origin_price=?, price=?, stock=?, monthly_sales=?, product_pic=?, raw_json=?, updated_at=?
                WHERE id=?
                """,
                (
                    payload["source_platform"], payload["poi_name"], payload["sku_id"], payload["product_name"],
                    payload["upc"], payload["spec"], payload["origin_price"], payload["price"], payload["stock"],
                    payload["monthly_sales"], payload["product_pic"], payload["raw_json"], current, product_id,
                ),
            )
            target_id = product_id
        else:
            existing = conn.execute(
                """
                SELECT id FROM products
                WHERE source_platform=? AND poi_name=? AND sku_id=?
                """,
                (payload["source_platform"], payload["poi_name"], payload["sku_id"]),
            ).fetchone()
            if existing:
                target_id = existing["id"]
                conn.execute(
                    """
                    UPDATE products
                    SET product_name=?, upc=?, spec=?, origin_price=?, price=?, stock=?, monthly_sales=?,
                        product_pic=?, raw_json=?, updated_at=?
                    WHERE id=?
                    """,
                    (
                        payload["product_name"], payload["upc"], payload["spec"], payload["origin_price"],
                        payload["price"], payload["stock"], payload["monthly_sales"],
                        payload["product_pic"], payload["raw_json"], current, target_id,
                    ),
                )
            else:
                cursor = conn.execute(
                    """
                    INSERT INTO products(
                        source_platform, poi_name, sku_id, product_name, upc, spec,
                        origin_price, price, stock, monthly_sales, product_pic, raw_json, created_at, updated_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        payload["source_platform"], payload["poi_name"], payload["sku_id"], payload["product_name"],
                        payload["upc"], payload["spec"], payload["origin_price"], payload["price"], payload["stock"],
                        payload["monthly_sales"], payload["product_pic"], payload["raw_json"], current, current,
                    ),
                )
                target_id = int(cursor.lastrowid)
    product = get_product(target_id)
    if product is None:
        raise RuntimeError("保存商品失败")
    return product


def bulk_upsert_products(items: list[dict]) -> dict:
    created = 0
    updated = 0
    for item in items:
        with get_conn() as conn:
            exists = conn.execute(
                """
                SELECT id FROM products
                WHERE source_platform=? AND poi_name=? AND sku_id=?
                """,
                (
                    (item.get("source_platform") or "wechat_meituan").strip(),
                    (item.get("poi_name") or "").strip(),
                    str(item.get("sku_id") or "").strip(),
                ),
            ).fetchone()
        upsert_product(item, exists["id"] if exists else None)
        if exists:
            updated += 1
        else:
            created += 1
    return {"created": created, "updated": updated, "total": created + updated}


def import_captured_products(items: list[dict], source: str = "mitmproxy") -> dict:
    created = 0
    updated = 0
    for item in items:
        payload = dict(item)
        payload.setdefault("source_platform", "wechat_meituan")
        raw_json = payload.get("raw_json")
        if raw_json is None:
            payload["raw_json"] = {"source": source, "captured_item": item}
        with get_conn() as conn:
            exists = conn.execute(
                """
                SELECT id FROM products
                WHERE source_platform=? AND poi_name=? AND sku_id=?
                """,
                (
                    (payload.get("source_platform") or "wechat_meituan").strip(),
                    (payload.get("poi_name") or "").strip(),
                    str(payload.get("sku_id") or "").strip(),
                ),
            ).fetchone()
        upsert_product(payload, exists["id"] if exists else None)
        if exists:
            updated += 1
        else:
            created += 1
    return {"created": created, "updated": updated, "total": created + updated}


def backfill_missing_upc() -> int:
    """Manual-only UPC backfill: for each (poi_name, product_name) group,
    if a sibling row has a non-empty UPC, copy it into the rows that have
    empty UPC. NOT called automatically — GS1 spec says each retail unit
    should have a unique UPC, so we don't want to silently propagate a
    likely-wrong value. Exposed via UI button for users who know their
    specific shop reuses the same UPC across specs.

    Returns the number of rows updated.
    """
    current = now_str()
    with get_conn() as conn:
        # First: pick one canonical UPC per (poi, product) group
        rows = conn.execute(
            """
            SELECT poi_name, product_name, MIN(upc) AS upc
            FROM products
            WHERE upc IS NOT NULL AND upc != ''
            GROUP BY poi_name, product_name
            """
        ).fetchall()
        n = 0
        for r in rows:
            cursor = conn.execute(
                """
                UPDATE products
                SET upc = ?, updated_at = ?
                WHERE poi_name = ?
                  AND product_name = ?
                  AND (upc IS NULL OR upc = '')
                """,
                (r["upc"], current, r["poi_name"], r["product_name"]),
            )
            n += cursor.rowcount
    return n


def delete_product(product_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM products WHERE id = ?", (product_id,))


def mark_synced(product_ids: list[int]) -> None:
    if not product_ids:
        return
    placeholders = ",".join("?" for _ in product_ids)
    with get_conn() as conn:
        conn.execute(
            f"UPDATE products SET synced_at = ?, updated_at = updated_at WHERE id IN ({placeholders})",
            [now_minute(), *product_ids],
        )
