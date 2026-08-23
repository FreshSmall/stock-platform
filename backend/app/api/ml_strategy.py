"""ML strategy router: meta-labeling pipeline (rule signals + RF referee).

Mounted under ``/api/v1/ml``. The run executes synchronously — a single stock
finishes in well under a second; the universe is capped by the schema so the
worst case stays bounded.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import get_current_user_id, get_db
from app.schemas.ml import MetaLabelRequest
from app.services import meta_label_service

router = APIRouter(prefix="/ml", tags=["ml"])


def _ok(data=None, msg: str = "ok") -> dict:
    """Build the unified success envelope (lazy import to avoid a cycle)."""
    from app.main import api_ok

    return api_ok(data, msg)


@router.post("/meta-label")
def run_meta_label(
    req: MetaLabelRequest,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
) -> dict:
    """Run the meta-labeling pipeline; returns raw vs filtered performance."""
    data = meta_label_service.run_meta_label(
        db,
        req.stock_codes,
        req.start,
        req.end,
        pt=req.pt,
        sl=req.sl,
        horizon=req.horizon,
        prob_th=req.prob_th,
        order=req.order,
        init_train=req.init_train,
        step=req.step,
        embargo_days=req.embargo_days,
        atr_barriers=req.atr_barriers,
    )
    return _ok(data)
