"""新闻资讯采集：把 AkShare 抓到的实时资讯写入 ``sa_news_sentiment``。

入口 :func:`sync_recent`：

1. 调 :mod:`app.data.akshare_client` 拉最新资讯（财联社 / 东财全球快讯）。
2. 校验 + 去重（按 ``source + title + pub_time``），保留最近 ``limit`` 条。
3. ``score`` 为真时，逐条调 :func:`app.services.news_service.score_sentiment`
   用 LLM 打情绪分 / 抽摘要 / 关联个股板块（失败不阻塞 —— 失败的行写入 NULL
   情绪分，后续可补跑）。
4. UPSERT 写入 ``sa_news_sentiment``。

幂等：靠 ``uk(source, title, pub_time)`` 唯一索引 + ON DUPLICATE KEY UPDATE，
重跑同一天不会重复或报错（与 :mod:`app.data.sync_daily` 同款套路）。

注意：这里**不直接 import LLM 客户端**，而是把 ``score_sentiment`` 作为可选
callable 传入；这样 :func:`sync_recent` 在测试里可以被无副作用地调用，也方便
API 层根据需要选择“只采集”还是“采集即打分”。
"""

import logging
from datetime import datetime
from typing import Any, Callable

from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session

from app.data import akshare_client
from app.models.news import SaNewsSentiment

logger = logging.getLogger(__name__)

# 默认来源优先级：财联社（短平快、覆盖 A 股相关）+ 东财全球（覆盖宏观/产业）。
DEFAULT_SOURCES = ("cls", "em")


def _to_dt(v: Any) -> datetime | None:
    """容错地把 ``pub_time`` 字段转成 :class:`datetime`。

    akshare_client 已经返回 datetime，但 mock / 手工 dict 测试里可能是字符串，
    这里统一兜一层（None / 不可解析 → None）。
    """
    if v is None:
        return None
    if isinstance(v, datetime):
        return v
    try:
        s = str(v).strip()
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                return datetime.strptime(s[: _FMT_LEN[fmt]], fmt)
            except ValueError:
                continue
    except Exception:
        pass
    return None


_FMT_LEN = {"%Y-%m-%d %H:%M:%S": 19, "%Y-%m-%d %H:%M": 16, "%Y-%m-%d": 10}


def _normalize(row: dict) -> dict | None:
    """把一条 akshare_client 返回的资讯规范化为可入库的 dict。

    返回 None 表示该行无效（无 title 且无 content），调用方应丢弃。
    保留可选的 ``sentiment`` / ``summary``（由 sync_recent 在打分阶段回填）。
    """
    title = (row.get("title") or "").strip() or None
    content = row.get("content")
    if not title and not content:
        return None
    out = {
        "pub_time": _to_dt(row.get("pub_time")),
        "title": title,
        "content": str(content) if content is not None else None,
        "source": row.get("source") or "cls",
        "stock_codes": row.get("stock_codes") or [],
        "sector": row.get("sector"),
    }
    # 仅在原行已带上情绪分/摘要时透传（采集阶段未打分则不写这两个字段）。
    if row.get("sentiment") is not None:
        out["sentiment"] = row["sentiment"]
    if row.get("summary") is not None:
        out["summary"] = row["summary"]
    return out


def upsert_news_rows(db: Session, rows: list[dict]) -> int:
    """把规范化后的资讯行 UPSERT 进 ``sa_news_sentiment``。

    唯一键：(source, title, pub_time) —— 重跑同批次只更新不重复插入。
    若某行 ``title`` 与 ``pub_time`` 同时为 NULL，则该行会被跳过（无唯一键
    会让 ON DUPLICATE KEY 退化为全表冲突）。

    :return: 实际写入（含更新）的行数。
    """
    payload: list[dict] = []
    for r in rows:
        n = _normalize(r)
        if n is None:
            continue
        # 没有唯一键的行跳过（无法幂等 UPSERT）。
        if not n["title"] and n["pub_time"] is None:
            continue
        payload.append(n)
    if not payload:
        return 0
    stmt = mysql_insert(SaNewsSentiment).values(payload)
    # 重复行更新这些字段（含打分结果，便于补跑刷新情绪分）。注意只引用 payload
    # 里实际出现的列 —— MySQL 的 ``AS new`` 别名只包含 INSERT 写入的列，引用
    # 未写入的列会报 ``Unknown column 'new.xxx'``。所以以并集为准。
    update_field_names = set()
    for p in payload:
        update_field_names.update(p.keys())
    # 唯一键字段不参与 UPDATE（pub_time/title/source 是匹配键本身）。
    update_field_names -= {"pub_time", "title", "source"}
    update_cols = {
        c: getattr(stmt.inserted, c) for c in update_field_names
    }
    if update_cols:
        stmt = stmt.on_duplicate_key_update(update_cols)
    db.execute(stmt)
    db.commit()
    return len(payload)


def sync_recent(
    db: Session,
    limit: int = 50,
    *,
    sources: tuple[str, ...] = DEFAULT_SOURCES,
    score: bool = False,
    scorer: Callable[[str], dict] | None = None,
) -> int:
    """拉取最新资讯并写入 ``sa_news_sentiment``。

    :param db: 一个已开启的 SQLAlchemy session（调用方管理生命周期）。
    :param limit: 期望写入的最大条数（每个 source 各取 ``limit``，再合并截断）。
    :param sources: 抓取来源元组，默认 ``('cls', 'em')``。
    :param score: 是否在采集后立刻调 LLM 打情绪分。默认 False（只采集）。
    :param scorer: 打分函数（``text -> {sentiment, summary, stock_codes,
        sector}``），仅在 ``score=True`` 时使用；不传则用
        :func:`app.services.news_service.score_sentiment`（lazy import 避免
        循环依赖）。允许测试注入 mock。
    :return: 实际写入（含更新）的行数。

    采集异常（网络/akshare 挂起）会被 ``akshare_client`` 的 @retry + 30s 墙钟
    兜住；若仍失败则记 warning 并跳过该 source，不影响其他 source。
    """
    fetched: list[dict] = []
    for src in sources:
        try:
            if src == "cls":
                fetched.extend(akshare_client.fetch_news_cls())
            elif src == "em":
                fetched.extend(akshare_client.fetch_news_em())
            elif src == "em-stock":
                # em-stock 需要个股代码，这里跳过；调用方应单独驱动。
                continue
            else:
                logger.warning("sync_recent: 未知来源 %s，已跳过", src)
        except Exception as e:
            # 单个 source 挂了不能拖垮整轮采集。
            logger.warning("sync_recent: 抓取 %s 失败: %s", src, e)

    if not fetched:
        return 0

    # 按 pub_time 倒序取最近 limit 条（None 时间排到最后）。
    fetched.sort(key=lambda r: r.get("pub_time") or datetime.min, reverse=True)
    fetched = fetched[:limit]

    # 可选：逐条打分。打分失败不阻塞，失败行保留 NULL 情绪分。
    if score:
        if scorer is None:
            from app.services import news_service

            scorer = news_service.score_sentiment
        enriched: list[dict] = []
        for r in fetched:
            text = r.get("title") or r.get("content") or ""
            try:
                s = scorer(text)
            except Exception as e:
                logger.warning("sync_recent: 打分失败 (%s): %s", r.get("title"), e)
                s = {}
            merged = dict(r)
            if s.get("sentiment") is not None:
                merged["sentiment"] = s["sentiment"]
            if s.get("summary"):
                merged["summary"] = s["summary"]
            # LLM 抽出的关联个股/板块仅在原行为空时回填，避免覆盖 em-stock 的精确关联。
            if not merged.get("stock_codes") and s.get("stock_codes"):
                merged["stock_codes"] = s["stock_codes"]
            if not merged.get("sector") and s.get("sector"):
                merged["sector"] = s["sector"]
            enriched.append(merged)
        fetched = enriched

    return upsert_news_rows(db, fetched)
