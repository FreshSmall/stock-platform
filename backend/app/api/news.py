"""News sentiment router (V2 阶段 N1, BP-V2-008)。

所有端点挂载在 ``/api/v1/news`` 下（``/api/v1`` 前缀来自 :mod:`app.main` 的
父路由）。统一响应信封用 :func:`app.main.api_ok`，参照
:mod:`app.api.market` 的 ``_ok`` 模式：lazy import 避免与 ``app.main`` 互相
import 导致的循环依赖（本路由会在启动时被 ``app.main`` 引入）。
"""

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.services import news_service

router = APIRouter(prefix="/news", tags=["news"])


def _ok(data=None, msg: str = "ok") -> dict:
    """构造统一成功信封（lazy import 避免循环，同 :mod:`app.api.market`）。"""
    from app.main import api_ok

    return api_ok(data, msg)


@router.get("")
def list_news(
    stock: Optional[str] = Query(None, description="按个股代码过滤（6 位）"),
    sector: Optional[str] = Query(None, description="按板块名称过滤"),
    date: Optional[date] = Query(None, description="按发布日期过滤（YYYY-MM-DD）"),
    limit: int = Query(50, ge=1, le=200, description="返回条数上限"),
    db: Session = Depends(get_db),
) -> dict:
    """新闻列表 + 情绪分。

    支持按个股 / 板块 / 发布日期组合过滤；结果按 ``pub_time`` 倒序返回。
    每条含 LLM 打出的 ``sentiment``(-1~+1) 与 ``summary``（未打分的为 null）。
    """
    items = news_service.list_news(db, stock=stock, sector=sector, date=date, limit=limit)
    return _ok(items)


@router.post("/sync")
def sync(
    limit: int = Query(50, ge=1, le=500),
    score: bool = Query(False, description="采集后是否立即调 LLM 打情绪分"),
    db: Session = Depends(get_db),
) -> dict:
    """手动触发一次新闻采集（默认只采集不打分）。

    幂等：重复调用只会更新已存在的同条资讯，不会重复插入。返回写入/更新的行数。
    """
    from app.data import sync_news

    n = sync_news.sync_recent(db, limit=limit, score=score)
    return _ok({"written": n}, msg="synced")
