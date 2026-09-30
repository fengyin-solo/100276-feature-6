"""冷藏箱温度报警接口：报警上报、沿状态流转、归档、冷机读数与处置看板。

记录链规则（待处理 → 处理中 → 已调温 → 已断电）全部落在 ColdchainService，
这里只负责入参装配与把可读的业务结果回给前端。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.coldchain import STATUS_ORDER, ColdchainService

router = APIRouter(prefix="/api/coldchain", tags=["冷藏箱监控"])

service = ColdchainService()


def _to_result(result: dict[str, Any]) -> ActionResult:
    return ActionResult(ok=bool(result["ok"]), message=result["message"], entry=result.get("entry"), code=result.get("code"))


# 注意：固定路径要排在 /{entry_id} 之前，避免 FastAPI 把 board 当成报警单 id。
@router.get("/board")
def board() -> dict[str, Any]:
    """处置看板：未完结、各状态、归并、归档条数随监控明细现场重算。"""
    return service.board()


@router.get("/readings/{container_no}")
def get_reading(container_no: str) -> dict[str, Any]:
    """读取一台冷藏箱的冷机实时读数；读不到时 ok=False 并给出原因。"""
    return service.read_chiller(container_no)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出未归档报警单（含完整记录链 history），供线下复盘。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "coldchain", "total": total, "items": items}


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按冷藏箱号或报警单号检索"),
    status: str | None = Query(default=None, description="待处理、处理中、已调温、已断电"),
    plug_no: str | None = Query(default=None, description="按插电桩号检索"),
    include_archived: bool = Query(default=False, description="是否带出已归档报警单"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按箱号/单号、状态、插电桩过滤报警单；默认不含已归档单。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    if status and status not in STATUS_ORDER:
        raise HTTPException(status_code=400, detail=f"状态仅支持：{'、'.join(STATUS_ORDER)}")
    items, total = service.list_entries(
        keyword=keyword, status=status, plug_no=plug_no,
        include_archived=include_archived, page=page, size=size,
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.post("/alarms", response_model=ActionResult)
def report_alarm(payload: EntryPayload) -> ActionResult:
    """上报一次温度报警：同箱同桩重复报警按插电桩归并，只认第一次。"""
    return _to_result(service.report_alarm(payload.values))


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """接单处理、调整设定温度、断电处置；越档会被拦截并退回处理中。

    values 支持字段：action、处理人、工班、说明（断电处置必填）。
    """
    action = str(payload.values.get("action") or "").strip()
    if not action:
        return ActionResult(ok=False, message="缺少动作 action", code="missing")
    return _to_result(service.run_action(entry_id, payload.values))


@router.post("/{entry_id}/archive", response_model=ActionResult)
def archive(entry_id: int, payload: EntryPayload) -> ActionResult:
    """归档已完结报警单；读数取不到的已调温单不许归档。"""
    return _to_result(service.archive(entry_id, payload.values))


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条报警单明细与完整流转记录链；不存在时给出可读错误。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"报警单 {entry_id} 不存在或已归档")
    return entry
