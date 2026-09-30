"""冷藏箱监控接口：冷藏箱明细、冷机读数、温度报警单的提交与沿状态流转。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.coldchain import ColdchainService
from app.services.coldchain_alarm import (
    ACTION_ADJUST,
    ACTION_POWER_OFF,
    ACTION_ROLLBACK,
    ACTION_START,
    OPEN_STATUSES,
    STATUS_ORDER,
    ColdchainAlarmService,
)
from app.telemetry import CHILLER_READINGS

router = APIRouter(prefix="/api/coldchain", tags=["冷藏箱监控"])

service = ColdchainService()
alarm_service = ColdchainAlarmService()

LIST_FIELDS = ["冷藏箱号", "设定温度", "当前温度", "运行电流", "插电桩号", "温度偏差", "报警记录", "监控状态"]
STATUSES = ["温度正常", "温度偏高", "温度偏低", "已断电"]

ALARM_ACTIONS = [ACTION_START, ACTION_ADJUST, ACTION_POWER_OFF, ACTION_ROLLBACK]


# --------------------------------------------------------------------- 冷机读数
@router.get("/readings")
def list_readings() -> dict[str, Any]:
    """冷机遥测读数：调温归档的设定温度以此为准，前端明细也从这里取在线状态。"""
    return {"total": len(CHILLER_READINGS), "items": CHILLER_READINGS}


# --------------------------------------------------------------------- 处置看板
@router.get("/alarms/board")
def alarm_board() -> dict[str, Any]:
    """处置看板：未完结条数与监控明细同源实时重算。"""
    return alarm_service.board()


# --------------------------------------------------------------------- 报警单
@router.get("/alarms", response_model=PageResult[dict])
def list_alarms(
    keyword: str | None = Query(default=None, description="按报警单号或冷藏箱号检索"),
    status: str | None = Query(default=None, description="待处理、处理中、已调温、已断电"),
    plug_no: str | None = Query(default=None, description="按插电桩号归并口径检索"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """列出温度报警单；默认未完结在前，重复报警已按插电桩归并到首单。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    if status and status not in STATUS_ORDER:
        raise HTTPException(status_code=400, detail=f"报警单状态仅支持：{'、'.join(STATUS_ORDER)}")
    items, total = alarm_service.list_tickets(
        keyword=keyword, status=status, plug_no=plug_no, page=page, size=size
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/alarms/open")
def list_open_alarms() -> dict[str, Any]:
    """未完结报警单速览：供看板与下拉归并提示共用。"""
    items, _ = alarm_service.list_tickets(page=1, size=200)
    open_items = [item for item in items if item.get("status") in OPEN_STATUSES]
    return {"total": len(open_items), "items": open_items}


@router.get("/alarms/{ticket_id}", response_model=dict)
def get_alarm(ticket_id: int) -> dict:
    """读取单条报警单（含完整事件链）；不存在时给出可读的错误说明。"""
    ticket = alarm_service.get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail=f"报警单 {ticket_id} 不存在或已归档")
    return ticket


@router.post("/alarms", response_model=ActionResult)
def submit_alarm(payload: EntryPayload) -> ActionResult:
    """提交温度报警：同一插电桩未完结单重复提交时归并，只认第一次。"""
    operator = str(payload.values.get("operator") or payload.remark or "").strip() or "值班管理员"
    ticket, message, created = alarm_service.submit_alarm(payload.values, operator)
    if ticket is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=ticket)


@router.post("/alarms/{ticket_id}/actions", response_model=ActionResult)
def run_alarm_action(ticket_id: int, payload: EntryPayload) -> ActionResult:
    """报警单沿状态流转；越档拦截、读数缺失、断电无说明都会被拦下并说明原因。"""
    values = payload.values
    action = str(values.get("action") or "").strip()
    if action not in ALARM_ACTIONS:
        return ActionResult(
            ok=False,
            message=f"动作「{action}」不属于温度报警单可执行范围，可选：{'、'.join(ALARM_ACTIONS)}",
        )
    operator = str(values.get("operator") or "").strip() or "值班管理员"
    note = values.get("note") or payload.remark
    ticket, message = alarm_service.run_action(ticket_id, action, operator, note=note)
    if ticket is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=ticket)


# --------------------------------------------------------------------- 冷藏箱明细
@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按冷藏箱号检索"),
    status: str | None = Query(default=None, description="温度正常、温度偏高、温度偏低、已断电"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按冷藏箱号与状态过滤冷藏箱监控列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出冷藏箱监控清单：返回当前全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "coldchain", "total": total, "items": items}


@router.get("/entry/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条冷藏箱明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"冷藏箱 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条冷藏箱，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="冷藏箱已登记", entry=entry)


@router.post("/entry/{entry_id}/actions", response_model=ActionResult)
def run_entry_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条冷藏箱执行监控动作；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
