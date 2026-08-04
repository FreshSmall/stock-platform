"""Tests for the V2-N1 news sentiment module (BP-V2-008).

覆盖三个层级：
* :mod:`app.services.news_service` —— ``score_sentiment`` (mock LLM) +
  ``list_news`` (哨兵数据)。
* :mod:`app.data.sync_news` —— mock akshare 验证落库 + 幂等。
* :mod:`app.api.news` —— 端到端路由（GET 列表 / POST sync）。

SAFETY：所有测试都写真实 ``stock_analysis`` 库，但只在哨兵标题前缀
``ZZTEST-`` 下写，且在 ``finally`` 里清掉，保证库干净（参照
:mod:`tests.test_sync_daily` 的 SENTINEL 模式）。LLM 全程 mock，断网运行。
"""

from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import delete, select

from app.ai import llm_client
from app.data import akshare_client, sync_news
from app.models.news import SaNewsSentiment
from app.services import news_service

# 哨兵前缀：所有测试写入的 title 都以它打头，teardown 时按 LIKE 前缀清掉。
SENTINEL_PREFIX = "ZZTEST-NEWS-"


@pytest.fixture
def cleanup_news(db_session):
    """清掉所有哨兵行（开始 + 结束都清，防止上次崩溃残留）。"""
    db_session.execute(
        delete(SaNewsSentiment).where(SaNewsSentiment.title.like(f"{SENTINEL_PREFIX}%"))
    )
    db_session.commit()
    try:
        yield db_session
    finally:
        db_session.execute(
            delete(SaNewsSentiment).where(SaNewsSentiment.title.like(f"{SENTINEL_PREFIX}%"))
        )
        db_session.commit()


# ----------------------------------------------------------------------------
# score_sentiment：mock LLM，验证情绪分 / 摘要 / 关联个股板块解析。
# ----------------------------------------------------------------------------

def _patch_llm_chat(monkeypatch, payload: str):
    """把 ``llm_client.chat`` 替换为返回固定 ``payload`` 的桩。"""
    monkeypatch.setattr(llm_client, "chat", lambda messages: payload)


def test_score_sentiment_parses_llm_json(monkeypatch):
    """LLM 返回合法 JSON 时，正确解析出情绪分 / 摘要 / 个股 / 板块。"""
    _patch_llm_chat(
        monkeypatch,
        '{"sentiment": 0.6, "summary": "利好财报", '
        '"stock_codes": ["600519", "858"], "sector": "白酒"}',
    )
    result = news_service.score_sentiment("贵州茅台Q2营收大增")

    assert result["sentiment"] == Decimal("0.600")
    assert result["summary"] == "利好财报"
    # 个股代码补零到 6 位。
    assert result["stock_codes"] == ["600519", "000858"]
    assert result["sector"] == "白酒"


def test_score_sentiment_clamps_out_of_range(monkeypatch):
    """LLM 给出超界情绪分（如 5.0）时夹到 +1。"""
    _patch_llm_chat(monkeypatch, '{"sentiment": 5.0, "summary": "x"}')
    result = news_service.score_sentiment("利好")
    assert result["sentiment"] == Decimal("1.000")


def test_score_sentiment_tolerates_markdown_fence(monkeypatch):
    """LLM 输出被 ```json ... ``` 包裹时仍能抽出 JSON。"""
    _patch_llm_chat(
        monkeypatch,
        '```json\n{"sentiment": -0.3, "summary": "利空"}\n```',
    )
    result = news_service.score_sentiment("某股暴跌")
    assert result["sentiment"] == Decimal("-0.300")
    assert result["summary"] == "利空"


def test_score_sentiment_returns_empty_on_garbage(monkeypatch):
    """LLM 输出无法解析时不抛异常，返回空 dict（调用方据此留 NULL）。"""
    _patch_llm_chat(monkeypatch, "完全不是 JSON 的乱码")
    assert news_service.score_sentiment("xxx") == {}


def test_score_sentiment_returns_empty_on_llm_error(monkeypatch):
    """LLM 调用抛异常时不传播，返回空 dict。"""
    def _boom(messages):
        raise RuntimeError("upstream down")

    monkeypatch.setattr(llm_client, "chat", _boom)
    assert news_service.score_sentiment("xxx") == {}


def test_score_sentiment_empty_text_returns_empty():
    """空文本直接短路返回空 dict，不调 LLM。"""
    assert news_service.score_sentiment("") == {}
    assert news_service.score_sentiment("   ") == {}


# ----------------------------------------------------------------------------
# list_news：哨兵数据查询。
# ----------------------------------------------------------------------------

def _make_row(
    db,
    *,
    title: str,
    sentiment=Decimal("0.500"),
    stock_codes=None,
    sector=None,
    pub_time=None,
):
    """插入并 commit 一条哨兵资讯，返回 ORM 行。"""
    row = SaNewsSentiment(
        pub_time=pub_time or datetime(2026, 8, 3, 9, 30, 0),
        title=title,
        content="哨兵正文",
        source="cls",
        stock_codes=stock_codes or [],
        sector=sector,
        sentiment=sentiment,
        summary="哨兵摘要",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_list_news_filters_by_stock(cleanup_news):
    """按个股代码过滤：命中 stock_codes JSON 数组里包含该代码的行。"""
    db = cleanup_news
    _make_row(db, title=f"{SENTINEL_PREFIX}A", stock_codes=["600519", "000858"])
    _make_row(db, title=f"{SENTINEL_PREFIX}B", stock_codes=["300750"])

    items = news_service.list_news(db, stock="000858", limit=10)
    titles = [it["title"] for it in items]
    assert any(t.endswith("-A") for t in titles)
    assert not any(t.endswith("-B") for t in titles)


def test_list_news_filters_by_sector(cleanup_news):
    """按板块过滤：sector 精确等值。"""
    db = cleanup_news
    _make_row(db, title=f"{SENTINEL_PREFIX}C", sector="白酒")
    _make_row(db, title=f"{SENTINEL_PREFIX}D", sector="新能源")

    items = news_service.list_news(db, sector="白酒", limit=10)
    titles = [it["title"] for it in items]
    assert any(t.endswith("-C") for t in titles)
    assert not any(t.endswith("-D") for t in titles)


def test_list_news_orders_by_pub_time_desc(cleanup_news):
    """结果按 pub_time 倒序（最新的在前）。"""
    db = cleanup_news
    _make_row(
        db, title=f"{SENTINEL_PREFIX}OLD",
        pub_time=datetime(2026, 7, 1, 9, 0, 0),
    )
    _make_row(
        db, title=f"{SENTINEL_PREFIX}NEW",
        pub_time=datetime(2026, 8, 3, 9, 0, 0),
    )

    items = news_service.list_news(db, limit=10)
    titles = [it["title"] for it in items if it["title"].startswith(SENTINEL_PREFIX)]
    # 最新的在前。
    assert titles[0].endswith("-NEW")
    assert titles[1].endswith("-OLD")


def test_list_news_serializes_sentiment(cleanup_news):
    """返回的 sentiment 已被序列化为 float。"""
    db = cleanup_news
    _make_row(db, title=f"{SENTINEL_PREFIX}E", sentiment=Decimal("0.123"))
    items = news_service.list_news(db, limit=10)
    target = next(it for it in items if it["title"].endswith("-E"))
    assert target["sentiment"] == pytest.approx(0.123, abs=1e-4)
    assert isinstance(target["sentiment"], float)


# ----------------------------------------------------------------------------
# sync_news：mock akshare，验证落库 + 幂等 + 集成情绪打分。
# ----------------------------------------------------------------------------

def test_sync_recent_persists_fetched_rows(monkeypatch, cleanup_news):
    """mock akshare 返回固定资讯，sync_recent 把它们落库。"""
    db = cleanup_news

    sample = [
        {
            "pub_time": datetime(2026, 8, 3, 10, 0, 0),
            "title": f"{SENTINEL_PREFIX}sync-1",
            "content": "测试资讯一",
            "source": "cls",
        },
        {
            "pub_time": datetime(2026, 8, 3, 10, 1, 0),
            "title": f"{SENTINEL_PREFIX}sync-2",
            "content": "测试资讯二",
            "source": "em",
        },
    ]
    monkeypatch.setattr(akshare_client, "fetch_news_cls", lambda: sample[:1])
    monkeypatch.setattr(akshare_client, "fetch_news_em", lambda: sample[1:])

    written = sync_news.sync_recent(db, limit=50, sources=("cls", "em"))
    assert written == 2

    fetched = db.execute(
        select(SaNewsSentiment).where(
            SaNewsSentiment.title.like(f"{SENTINEL_PREFIX}%")
        )
    ).scalars().all()
    assert len(fetched) == 2
    titles = {r.title for r in fetched}
    assert titles == {f"{SENTINEL_PREFIX}sync-1", f"{SENTINEL_PREFIX}sync-2"}


def test_sync_recent_is_idempotent(monkeypatch, cleanup_news):
    """同一批数据重跑只更新不重复插入（幂等）。"""
    db = cleanup_news

    sample = [
        {
            "pub_time": datetime(2026, 8, 3, 10, 0, 0),
            "title": f"{SENTINEL_PREFIX}idem",
            "content": "v1",
            "source": "cls",
        }
    ]
    monkeypatch.setattr(akshare_client, "fetch_news_cls", lambda: sample)
    monkeypatch.setattr(akshare_client, "fetch_news_em", lambda: [])

    sync_news.sync_recent(db, limit=50, sources=("cls", "em"))
    # 改 content 后再跑：应只更新那一行。
    sample[0]["content"] = "v2"
    n = sync_news.sync_recent(db, limit=50, sources=("cls", "em"))
    assert n == 1

    db.expire_all()
    fetched = db.execute(
        select(SaNewsSentiment).where(SaNewsSentiment.title == f"{SENTINEL_PREFIX}idem")
    ).scalars().all()
    assert len(fetched) == 1
    assert fetched[0].content == "v2"


def test_sync_recent_with_scorer(monkeypatch, cleanup_news):
    """score=True + 注入 scorer：采集后回填情绪分 / 摘要。"""
    db = cleanup_news

    sample = [
        {
            "pub_time": datetime(2026, 8, 3, 10, 0, 0),
            "title": f"{SENTINEL_PREFIX}score",
            "content": "原始内容",
            "source": "cls",
        }
    ]
    monkeypatch.setattr(akshare_client, "fetch_news_cls", lambda: sample)
    monkeypatch.setattr(akshare_client, "fetch_news_em", lambda: [])

    def fake_scorer(text):
        return {
            "sentiment": Decimal("0.777"),
            "summary": "由 mock 打的摘要",
            "stock_codes": ["600519"],
            "sector": "白酒",
        }

    written = sync_news.sync_recent(
        db, limit=50, sources=("cls",), score=True, scorer=fake_scorer
    )
    assert written == 1

    row = db.execute(
        select(SaNewsSentiment).where(SaNewsSentiment.title == f"{SENTINEL_PREFIX}score")
    ).scalar_one()
    assert row.sentiment == Decimal("0.777")
    assert row.summary == "由 mock 打的摘要"
    assert row.stock_codes == ["600519"]
    assert row.sector == "白酒"


def test_sync_recent_skips_invalid_rows(monkeypatch, cleanup_news):
    """无 title 且无 content 的行被丢弃；其他行正常落库。"""
    db = cleanup_news

    sample = [
        {"pub_time": datetime(2026, 8, 3, 10, 0, 0), "title": "", "content": None, "source": "cls"},
        {"pub_time": datetime(2026, 8, 3, 10, 1, 0), "title": f"{SENTINEL_PREFIX}ok", "content": "ok", "source": "cls"},
    ]
    monkeypatch.setattr(akshare_client, "fetch_news_cls", lambda: sample)
    monkeypatch.setattr(akshare_client, "fetch_news_em", lambda: [])

    written = sync_news.sync_recent(db, limit=50, sources=("cls", "em"))
    assert written == 1

    fetched = db.execute(
        select(SaNewsSentiment).where(SaNewsSentiment.title.like(f"{SENTINEL_PREFIX}%"))
    ).scalars().all()
    assert len(fetched) == 1
    assert fetched[0].title == f"{SENTINEL_PREFIX}ok"


def test_sync_recent_handles_fetch_error(monkeypatch, cleanup_news):
    """某个 source 抓取抛异常时不影响其他 source。"""
    db = cleanup_news

    def boom():
        raise RuntimeError("cls down")

    good = [
        {
            "pub_time": datetime(2026, 8, 3, 10, 0, 0),
            "title": f"{SENTINEL_PREFIX}em-ok",
            "content": "ok",
            "source": "em",
        }
    ]
    monkeypatch.setattr(akshare_client, "fetch_news_cls", boom)
    monkeypatch.setattr(akshare_client, "fetch_news_em", lambda: good)

    written = sync_news.sync_recent(db, limit=50, sources=("cls", "em"))
    assert written == 1


# ----------------------------------------------------------------------------
# API 端到端：GET /news / POST /news/sync（mock akshare + LLM）。
# ----------------------------------------------------------------------------

def test_api_list_news_returns_envelope(monkeypatch, cleanup_news):
    """GET /api/v1/news 返回统一信封 {code:0, msg, data:list}。"""
    from fastapi.testclient import TestClient

    from app.main import app

    db = cleanup_news
    _make_row(db, title=f"{SENTINEL_PREFIX}api", sentiment=Decimal("0.400"))

    client = TestClient(app)
    resp = client.get("/api/v1/news", params={"limit": 50})
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    titles = [it["title"] for it in body["data"]]
    assert any(t == f"{SENTINEL_PREFIX}api" for t in titles)


def test_api_sync_writes_rows(monkeypatch, cleanup_news):
    """POST /api/v1/news/sync 触发采集并落库（mock akshare）。"""
    from fastapi.testclient import TestClient

    from app.main import app

    db = cleanup_news
    sample = [
        {
            "pub_time": datetime(2026, 8, 3, 11, 0, 0),
            "title": f"{SENTINEL_PREFIX}api-sync",
            "content": "ok",
            "source": "cls",
        }
    ]
    monkeypatch.setattr(akshare_client, "fetch_news_cls", lambda: sample)
    monkeypatch.setattr(akshare_client, "fetch_news_em", lambda: [])

    client = TestClient(app)
    resp = client.post("/api/v1/news/sync", params={"limit": 50})
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["written"] >= 1

    # 验证落库。
    row = db.execute(
        select(SaNewsSentiment).where(SaNewsSentiment.title == f"{SENTINEL_PREFIX}api-sync")
    ).scalar_one_or_none()
    assert row is not None
