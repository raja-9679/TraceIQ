"""Sizing calculator endpoint (WP13c, decision C6).

Any signed-in user: the inputs are hypothetical numbers and the output is
arithmetic over the documented figures in app/services/sizing.py — it reads no
tenant data, so there is nothing to scope.
"""
from typing import Any, Dict

from fastapi import APIRouter, Depends

from app.core.auth import get_current_user
from app.models import User
from app.services.sizing import SizingInput, estimate

router = APIRouter()


@router.get("/sizing/defaults")
async def sizing_defaults(current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    """The default inputs and the estimate they produce (the page's first paint)."""
    return estimate(SizingInput())


@router.post("/sizing/estimate")
async def sizing_estimate(body: SizingInput,
                          current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    return estimate(body)
