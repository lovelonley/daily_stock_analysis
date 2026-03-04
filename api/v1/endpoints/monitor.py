# -*- coding: utf-8 -*-
"""盘中监控状态 API"""

from fastapi import APIRouter, Request

router = APIRouter()


@router.get(
    "/status",
    summary="获取盘中监控状态",
    description="返回 sentinel 实时告警级别、市场快照等信息",
)
async def get_monitor_status(request: Request):
    sentinel = getattr(request.app.state, "sentinel", None)
    if sentinel is None:
        return {"phase": "offline", "level": "offline"}
    return sentinel.get_status()
