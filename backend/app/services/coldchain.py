"""冷藏箱温度报警记录链：状态流转、插电桩归并、冷机读数与看板重算都收在这里。

报警单沿固定状态一路流转：
    待处理 -> 处理中 -> 已调温 -> 已断电
其中「已调温 / 已断电」为完结状态。规则要点：

* 调整设定温度时，设定温度只能取冷机实时读数，读数取不到不允许落「已调温」；
* 断电处置必须留下处置说明，且只有「已调温」的单能落到「已断电」；
* 越档（跳过必经状态直接了结）一律拦截，并把报警单退回「处理中」；
* 同一只冷藏箱在同一个插电桩上反复报警，归并到第一张报警单，只认首报；
* 完结后的单还要校验读数来源才能归档；
* 看板每次都基于监控明细实时重算，每条流转都留下处理人与工班，换班可追溯。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.store import store

MODULE = "coldchain"
READINGS_MODULE = "coldchain_readings"

# 报警单状态：顺序即允许的正向流转顺序，后两个为完结状态。
STATUS_PENDING = "待处理"
STATUS_PROCESSING = "处理中"
STATUS_ADJUSTED = "已调温"
STATUS_POWERED_OFF = "已断电"
STATUS_ORDER = [STATUS_PENDING, STATUS_PROCESSING, STATUS_ADJUSTED, STATUS_POWERED_OFF]
OPEN_STATUSES = {STATUS_PENDING, STATUS_PROCESSING}
TERMINAL_STATUSES = {STATUS_ADJUSTED, STATUS_POWERED_OFF}

# 每个动作的目标状态，以及目标状态要求的前置状态（用于识别越档）。
ACTION_TARGET = {
    "接单处理": STATUS_PROCESSING,
    "调整设定温度": STATUS_ADJUSTED,
    "断电处置": STATUS_POWERED_OFF,
}
PREDECESSOR = {
    STATUS_PROCESSING: STATUS_PENDING,
    STATUS_ADJUSTED: STATUS_PROCESSING,
    STATUS_POWERED_OFF: STATUS_ADJUSTED,
}
DEFAULT_OPERATOR = "值班管理员"
DEFAULT_SHIFT = "白班 08:00-20:00"

# 模拟从冷机控制器读到的实时数据。真实项目里这里换成冷机/物联平台的读接口；
# 取不到（离线、通讯故障、未知箱号）时统一返回 ok=False，业务侧据此拒绝调温与归档。
CHILLER_READINGS: dict[str, dict[str, Any]] = {
    "RCU-2201": {"ok": True, "设定温度": -18.0, "当前温度": -9.2},
    "RCU-2202": {"ok": True, "设定温度": -20.0, "当前温度": -25.8},
    "RCU-2203": {"ok": True, "设定温度": -18.0, "当前温度": -18.4},
    "RCU-2204": {"ok": True, "设定温度": 2.0, "当前温度": 2.2},
    "RCU-2205": {"ok": True, "设定温度": -25.0, "当前温度": -10.6},
    "RCU-2206": {"ok": True, "设定温度": -18.0, "当前温度": -18.1},
    "RCU-3001": {"ok": True, "设定温度": -18.0, "当前温度": -7.4},
    # 这一台冷机通讯故障，用来验证「读数取不到，不许调温/归档」。
    "RCU-9001": {"ok": False, "设定温度": None, "当前温度": None},
}


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _norm(value: Any) -> str:
    return str(value or "").strip()


class ColdchainService:
    def __init__(self) -> None:
        self._seed_demo_tickets()

    # ------------------------------------------------------------------ 冷机读数
    def read_chiller(self, container_no: str) -> dict[str, Any]:
        """读一台冷藏箱冷机的实时设定温度/当前温度；读不到时明确返回失败原因。"""
        container_no = _norm(container_no)
        reading = CHILLER_READINGS.get(container_no)
        if reading is None:
            return {
                "ok": False,
                "冷藏箱号": container_no,
                "设定温度": None,
                "当前温度": None,
                "时间": now_text(),
                "message": "冷机未在监控平台登记，无法取到读数",
            }
        if not reading.get("ok"):
            return {
                "ok": False,
                "冷藏箱号": container_no,
                "设定温度": None,
                "当前温度": None,
                "时间": now_text(),
                "message": "冷机通讯故障，实时读数取不到",
            }
        return {
            "ok": True,
            "冷藏箱号": container_no,
            "设定温度": reading["设定温度"],
            "当前温度": reading["当前温度"],
            "时间": now_text(),
            "message": "读数正常",
        }

    # ------------------------------------------------------------------ 查询看板
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        plug_no: str | None = None,
        include_archived: bool = False,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = list(store.rows(MODULE))
        if not include_archived:
            rows = [row for row in rows if not row.get("归档")]
        if keyword:
            key = keyword.strip()
            rows = [
                row
                for row in rows
                if key in str(row.get("冷藏箱号", "")) or key in str(row.get("报警单号", ""))
            ]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if plug_no:
            rows = [row for row in rows if plug_no.strip() in str(row.get("插电桩号", ""))]
        # 未完结的单排在前面，同组按最新上报优先。
        rows.sort(key=lambda row: (row.get("归档", False), row.get("status") not in OPEN_STATUSES, -int(row.get("id", 0))))
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def board(self) -> dict[str, Any]:
        """处置看板：未完结等条数一律随监控明细现场重算，不缓存任何计数。"""
        rows = [row for row in store.rows(MODULE) if not row.get("归档")]
        status_counts = {status: 0 for status in STATUS_ORDER}
        for row in rows:
            status_counts[str(row.get("status"))] = status_counts.get(str(row.get("status")), 0) + 1
        open_count = sum(status_counts[status] for status in OPEN_STATUSES)
        # 报警次数大于 1 的部分都是按插电桩归并掉的重复报警。
        merged_count = sum(int(row.get("报警次数", 1)) - 1 for row in rows)
        archived_count = sum(1 for row in store.rows(MODULE) if row.get("归档"))
        cards = [
            {"label": "未完结报警单", "value": open_count},
            {"label": "待处理", "value": status_counts[STATUS_PENDING]},
            {"label": "处理中", "value": status_counts[STATUS_PROCESSING]},
            {"label": "已调温", "value": status_counts[STATUS_ADJUSTED]},
            {"label": "已断电", "value": status_counts[STATUS_POWERED_OFF]},
            {"label": "已归并重复报警", "value": merged_count},
            {"label": "已归档", "value": archived_count},
        ]
        return {
            "cards": cards,
            "status_counts": status_counts,
            "未完结": open_count,
            "已归并": merged_count,
            "已归档": archived_count,
            "统计时间": now_text(),
        }

    # ------------------------------------------------------------------ 报警上报
    def report_alarm(self, values: dict[str, Any]) -> dict[str, Any]:
        """上报一次温度报警；同一冷藏箱在同一插电桩的重复报警归并到首张单。"""
        container_no = _norm(values.get("冷藏箱号"))
        plug_no = _norm(values.get("插电桩号"))
        operator = _norm(values.get("处理人")) or DEFAULT_OPERATOR
        shift = _norm(values.get("工班")) or DEFAULT_SHIFT
        missing = [name for name, value in (("冷藏箱号", container_no), ("插电桩号", plug_no)) if not value]
        if missing:
            return {"ok": False, "entry": None, "code": "missing", "message": f"缺少必填字段：{'、'.join(missing)}"}

        existing = self._find_open_ticket(container_no, plug_no)
        reading = self.read_chiller(container_no)
        if existing is not None:
            # 同箱同桩且首张单未完结：按插电桩归并，只认第一次上报，状态与处理人都不动。
            existing["报警次数"] = int(existing.get("报警次数", 1)) + 1
            existing["最近报警时间"] = now_text()
            if reading["ok"]:
                existing["最新冷机温度"] = reading["当前温度"]
            self._append_history(
                existing,
                action="重复报警归并",
                to_status=existing["status"],
                operator=operator,
                shift=shift,
                note=(
                    f"同一冷藏箱在插电桩 {plug_no} 第 {existing['报警次数']} 次报警，"
                    f"按插电桩归并到首报，状态与处理人不变，仅认首报 {existing.get('首报人')}"
                ),
                merged=True,
            )
            return {
                "ok": True,
                "entry": existing,
                "code": "merged",
                "message": (
                    f"该冷藏箱在插电桩 {plug_no} 已有未完结报警单 {existing['报警单号']}，"
                    f"本次为第 {existing['报警次数']} 次报警，已归并，只认首报"
                ),
            }

        rows = store.rows(MODULE)
        ticket = self._new_ticket(
            rows,
            container_no=container_no,
            plug_no=plug_no,
            operator=operator,
            shift=shift,
            reading=reading,
        )
        rows.append(ticket)
        return {"ok": True, "entry": ticket, "code": "created", "message": f"温度报警已登记，报警单 {ticket['报警单号']} 待处理"}

    # ------------------------------------------------------------------ 流转动作
    def run_action(self, entry_id: int, values: dict[str, Any]) -> dict[str, Any]:
        action = _norm(values.get("action"))
        operator = _norm(values.get("处理人")) or DEFAULT_OPERATOR
        shift = _norm(values.get("工班")) or DEFAULT_SHIFT
        note = _norm(values.get("说明"))

        ticket = store.find(MODULE, entry_id)
        if ticket is None:
            return {"ok": False, "entry": None, "code": "not_found", "message": f"报警单 {entry_id} 不存在或已归档"}
        if ticket.get("归档"):
            return {"ok": False, "entry": ticket, "code": "archived", "message": f"报警单 {ticket['报警单号']} 已归档，不允许再流转"}
        if action not in ACTION_TARGET:
            return {"ok": False, "entry": ticket, "code": "bad_action", "message": f"动作「{action}」不属于温度报警处置可执行范围"}

        current = str(ticket["status"])
        target = ACTION_TARGET[action]

        # 已断电是最终了结：之后不允许再执行任何处置动作。
        if current == STATUS_POWERED_OFF and action != "归档":
            return {
                "ok": False,
                "entry": ticket,
                "code": "closed",
                "message": f"报警单已是「{STATUS_POWERED_OFF}」最终完结状态，处置记录链不再接受动作",
            }
        # 已调温只允许继续断电处置；其它动作按越档处理（退回处理中）。
        if current == STATUS_ADJUSTED and action != "断电处置":
            return self._reject_jump(ticket, action, current, ACTION_TARGET[action], operator, shift)

        # 越档拦截：前置状态不匹配，说明跳过了必经环节，不许了结，强制退回处理中。
        if current != PREDECESSOR[target]:
            return self._reject_jump(ticket, action, current, target, operator, shift)

        if action == "接单处理":
            return self._claim(ticket, operator, shift)
        if action == "调整设定温度":
            return self._adjust_setpoint(ticket, operator, shift)
        if action == "断电处置":
            return self._power_off(ticket, operator, shift, note)
        return {"ok": False, "entry": ticket, "code": "bad_action", "message": f"动作「{action}」未配置处理逻辑"}

    def archive(self, entry_id: int, values: dict[str, Any]) -> dict[str, Any]:
        """归档完结报警单；已调温的单要求冷机读数仍可取，读数取不到不许归档。"""
        operator = _norm(values.get("处理人")) or DEFAULT_OPERATOR
        shift = _norm(values.get("工班")) or DEFAULT_SHIFT
        ticket = store.find(MODULE, entry_id)
        if ticket is None:
            return {"ok": False, "entry": None, "code": "not_found", "message": f"报警单 {entry_id} 不存在或已归档"}
        if ticket.get("归档"):
            return {"ok": False, "entry": ticket, "code": "archived", "message": f"报警单 {ticket['报警单号']} 已归档，请勿重复归档"}
        current = str(ticket["status"])
        if current not in TERMINAL_STATUSES:
            return {"ok": False, "entry": ticket, "code": "not_terminal", "message": f"报警单处于「{current}」，未完结的单不许归档"}
        if not ticket.get("设定温度") or not ticket.get("冷机读数快照"):
            return {
                "ok": False,
                "entry": ticket,
                "code": "no_reading",
                "message": "设定温度缺少冷机读数来源，读数取不到的报警单不许归档",
            }
        # 已调温：冷机仍在运行，归档前再取一次实时读数确认；已断电：以调温时留存的读数为准。
        if current == STATUS_ADJUSTED:
            reading = self.read_chiller(str(ticket["冷藏箱号"]))
            if not reading["ok"]:
                return {
                    "ok": False,
                    "entry": ticket,
                    "code": "no_reading",
                    "message": f"冷机读数取不到（{reading['message']}），不许归档",
                }
        ticket["归档"] = True
        ticket["归档时间"] = now_text()
        ticket["归档人"] = operator
        ticket["pending"] = False
        ticket["abnormal"] = False
        self._append_history(
            ticket,
            action="归档",
            from_status=current,
            to_status=current,
            operator=operator,
            shift=shift,
            note="完结报警单归档留存",
        )
        return {"ok": True, "entry": ticket, "code": "archived", "message": f"报警单 {ticket['报警单号']} 已归档，记录链封存"}

    # ------------------------------------------------------------------ 内部规则
    def _claim(self, ticket: dict[str, Any], operator: str, shift: str) -> dict[str, Any]:
        ticket["status"] = STATUS_PROCESSING
        ticket["当前处理人"] = operator
        ticket["当前工班"] = shift
        ticket["接单时间"] = now_text()
        self._sync_flags(ticket)
        self._append_history(
            ticket, action="接单处理", from_status=STATUS_PENDING, to_status=STATUS_PROCESSING,
            operator=operator, shift=shift, note="接单开始处置",
        )
        return {"ok": True, "entry": ticket, "code": "claimed", "message": f"{operator} 已接单，报警单进入处理中"}

    def _adjust_setpoint(self, ticket: dict[str, Any], operator: str, shift: str) -> dict[str, Any]:
        # 设定温度只认冷机读数，不接受人工手填；读数取不到就不许落已调温。
        reading = self.read_chiller(str(ticket["冷藏箱号"]))
        if not reading["ok"]:
            return {
                "ok": False,
                "entry": ticket,
                "code": "no_reading",
                "message": f"冷机读数取不到（{reading['message']}），设定温度无法确认，不许落「已调温」",
            }
        ticket["设定温度"] = reading["设定温度"]
        ticket["最新冷机温度"] = reading["当前温度"]
        ticket["冷机读数快照"] = reading
        ticket["调温时间"] = reading["时间"]
        ticket["调温人"] = operator
        ticket["当前处理人"] = operator
        ticket["当前工班"] = shift
        ticket["status"] = STATUS_ADJUSTED
        self._sync_flags(ticket)
        self._append_history(
            ticket, action="调整设定温度", from_status=STATUS_PROCESSING, to_status=STATUS_ADJUSTED,
            operator=operator, shift=shift,
            note=f"按冷机读数把设定温度调到 {reading['设定温度']}℃（当前读数 {reading['当前温度']}℃）",
        )
        return {
            "ok": True,
            "entry": ticket,
            "code": "adjusted",
            "message": f"已按冷机读数把设定温度调到 {reading['设定温度']}℃，报警单落到已调温",
        }

    def _power_off(self, ticket: dict[str, Any], operator: str, shift: str, note: str) -> dict[str, Any]:
        if not note:
            return {"ok": False, "entry": ticket, "code": "missing_note", "message": "断电处置必须留下处置说明，不许无说明直接断电了结"}
        ticket["断电说明"] = note
        ticket["断电时间"] = now_text()
        ticket["断电人"] = operator
        ticket["当前处理人"] = operator
        ticket["当前工班"] = shift
        ticket["status"] = STATUS_POWERED_OFF
        self._sync_flags(ticket)
        self._append_history(
            ticket, action="断电处置", from_status=STATUS_ADJUSTED, to_status=STATUS_POWERED_OFF,
            operator=operator, shift=shift, note=f"断电并结束处置：{note}",
        )
        return {"ok": True, "entry": ticket, "code": "powered_off", "message": "已断电并登记处置说明，报警单处置结束"}

    def _reject_jump(
        self,
        ticket: dict[str, Any],
        action: str,
        current: str,
        target: str,
        operator: str,
        shift: str,
    ) -> dict[str, Any]:
        # 越档的单不许直接了结：退回处理中并留痕（待处理接单视同退回）。
        from_status = current
        ticket["status"] = STATUS_PROCESSING
        ticket["当前处理人"] = operator
        ticket["当前工班"] = shift
        self._sync_flags(ticket)
        self._append_history(
            ticket,
            action="越档拦截退回",
            from_status=from_status,
            to_status=STATUS_PROCESSING,
            operator=operator,
            shift=shift,
            note=f"尝试「{action}」从「{from_status}」直接跳到「{target}」，属越档了结，已拦截并退回处理中",
        )
        return {
            "ok": False,
            "entry": ticket,
            "code": "jump_returned",
            "message": f"越档不允许直接了结：「{from_status}」不能直接{action}到「{target}」，报警单已退回处理中",
        }

    def _find_open_ticket(self, container_no: str, plug_no: str) -> dict[str, Any] | None:
        for row in store.rows(MODULE):
            if row.get("归档"):
                continue
            if str(row.get("冷藏箱号")) == container_no and str(row.get("插电桩号")) == plug_no:
                return row
        return None

    def _new_ticket(
        self,
        rows: list[dict[str, Any]],
        *,
        container_no: str,
        plug_no: str,
        operator: str,
        shift: str,
        reading: dict[str, Any],
    ) -> dict[str, Any]:
        next_id = max((int(row.get("id", 0)) for row in rows), default=0) + 1
        stamp = now_text()
        alarm_type = None
        if reading["ok"]:
            alarm_type = "温度偏高" if reading["当前温度"] > reading["设定温度"] else "温度偏低"
        ticket: dict[str, Any] = {
            "id": next_id,
            "status": STATUS_PENDING,
            "pending": True,
            "abnormal": True,
            "报警单号": f"ALM-{datetime.now():%Y%m%d}-{next_id:03d}",
            "冷藏箱号": container_no,
            "插电桩号": plug_no,
            "报警类型": alarm_type or "温度异常",
            "报警温度": reading["当前温度"],
            "设定温度": None,
            "断电说明": None,
            "报警次数": 1,
            "首次报警时间": stamp,
            "最近报警时间": stamp,
            "接单时间": None,
            "调温时间": None,
            "断电时间": None,
            "归档时间": None,
            "首报人": operator,
            "首报工班": shift,
            "当前处理人": None,
            "当前工班": None,
            "调温人": None,
            "断电人": None,
            "归档人": None,
            "冷机读数快照": None,
            "归档": False,
            "history": [],
        }
        self._append_history(
            ticket, action="报警上报", to_status=STATUS_PENDING, operator=operator, shift=shift,
            note=(
                f"插电桩 {plug_no} 上报温度报警"
                + (f"，冷机当前读数 {reading['当前温度']}℃（设定 {reading['设定温度']}℃）" if reading["ok"]
                   else "，冷机读数暂不可用")
            ),
        )
        return ticket

    def _append_history(
        self,
        ticket: dict[str, Any],
        *,
        action: str,
        operator: str,
        shift: str,
        note: str,
        to_status: str,
        from_status: str | None = None,
        merged: bool = False,
    ) -> None:
        history = ticket.setdefault("history", [])
        history.append({
            "序号": len(history) + 1,
            "时间": now_text(),
            "动作": action,
            "原状态": from_status if from_status is not None else ticket.get("status"),
            "新状态": to_status,
            "处理人": operator,
            "工班": shift,
            "说明": note,
            "归并": merged,
        })

    def _sync_flags(self, ticket: dict[str, Any]) -> None:
        # 让通用 overview 看板也能直接读懂每张单：未完结才算待处理/异常。
        ticket["pending"] = ticket["status"] in OPEN_STATUSES
        ticket["abnormal"] = ticket["status"] in OPEN_STATUSES

    # ------------------------------------------------------------------ 演示数据
    def _seed_demo_tickets(self) -> None:
        rows = store.rows(MODULE)
        if rows:
            return

        def build(
            ticket_id: int,
            *,
            container: str,
            plug: str,
            status: str,
            reporter: str,
            report_shift: str,
            handler: str | None,
            handler_shift: str | None,
            count: int,
            note: str,
            power_note: str | None = None,
            archived: bool = False,
        ) -> dict[str, Any]:
            reading = self.read_chiller(container)
            stamp = now_text()
            ticket = {
                "id": ticket_id,
                "status": status,
                "pending": status in OPEN_STATUSES,
                "abnormal": status in OPEN_STATUSES,
                "报警单号": f"ALM-{datetime.now():%Y%m%d}-{ticket_id:03d}",
                "冷藏箱号": container,
                "插电桩号": plug,
                "报警类型": "温度偏高",
                "报警温度": reading["当前温度"],
                "设定温度": reading["设定温度"] if status in TERMINAL_STATUSES else None,
                "断电说明": power_note,
                "报警次数": count,
                "首次报警时间": stamp,
                "最近报警时间": stamp,
                "接单时间": stamp if status != STATUS_PENDING else None,
                "调温时间": stamp if status in TERMINAL_STATUSES else None,
                "断电时间": stamp if status == STATUS_POWERED_OFF else None,
                "归档时间": stamp if archived else None,
                "首报人": reporter,
                "首报工班": report_shift,
                "当前处理人": handler,
                "当前工班": handler_shift,
                "调温人": handler if status in TERMINAL_STATUSES else None,
                "断电人": handler if status == STATUS_POWERED_OFF else None,
                "归档人": handler if archived else None,
                "冷机读数快照": reading if status in TERMINAL_STATUSES and reading["ok"] else None,
                "归档": archived,
                "history": [],
            }
            self._append_history(ticket, action="报警上报", to_status=STATUS_PENDING,
                                 operator=reporter, shift=report_shift, note=note)
            # 后续报警都发生在首报之后、接单之前，按时间顺序归并进记录链。
            for seq in range(2, count + 1):
                self._append_history(
                    ticket, action="重复报警归并", to_status=STATUS_PENDING,
                    operator=reporter, shift=report_shift,
                    note=f"同一冷藏箱在插电桩 {plug} 第 {seq} 次报警，按插电桩归并到首报，只认首报",
                    merged=True,
                )
            if status != STATUS_PENDING:
                self._append_history(ticket, action="接单处理", from_status=STATUS_PENDING,
                                     to_status=STATUS_PROCESSING, operator=handler or reporter,
                                     shift=handler_shift or report_shift, note="接单开始处置")
            if status in TERMINAL_STATUSES:
                self._append_history(ticket, action="调整设定温度", from_status=STATUS_PROCESSING,
                                     to_status=STATUS_ADJUSTED, operator=handler or reporter,
                                     shift=handler_shift or report_shift,
                                     note=f"按冷机读数把设定温度调到 {reading['设定温度']}℃")
            if status == STATUS_POWERED_OFF:
                self._append_history(ticket, action="断电处置", from_status=STATUS_ADJUSTED,
                                     to_status=STATUS_POWERED_OFF, operator=handler or reporter,
                                     shift=handler_shift or report_shift, note=f"断电并结束处置：{power_note}")
            if archived:
                self._append_history(ticket, action="归档", from_status=status, to_status=status,
                                     operator=handler or reporter, shift=handler_shift or report_shift,
                                     note="完结报警单归档留存")
            return ticket

        rows.append(build(
            1, container="RCU-2201", plug="P-07", status=STATUS_PENDING,
            reporter="王建国", report_shift="夜班 20:00-08:00", handler=None, handler_shift=None,
            count=1, note="夜班巡检发现箱内温度高于设定，上报待接班处置",
        ))
        rows.append(build(
            2, container="RCU-2202", plug="P-08", status=STATUS_PROCESSING,
            reporter="王建国", report_shift="夜班 20:00-08:00", handler="李秀英", handler_shift="白班 08:00-20:00",
            count=3, note="同箱同桩反复报警，首报后两次已按插电桩归并",
        ))
        rows.append(build(
            3, container="RCU-2203", plug="P-09", status=STATUS_ADJUSTED,
            reporter="王建国", report_shift="夜班 20:00-08:00", handler="李秀英", handler_shift="白班 08:00-20:00",
            count=1, note="温度偏高报警，调温后持续观察",
        ))
        rows.append(build(
            4, container="RCU-2204", plug="P-10", status=STATUS_POWERED_OFF,
            reporter="赵大勇", report_shift="白班 08:00-20:00", handler="赵大勇", handler_shift="白班 08:00-20:00",
            count=1, note="冷机异响伴随温度异常，上报处置",
            power_note="冷机异响疑似故障，已拔除 P-10 电源并挂牌，转修箱班组",
        ))
        rows.append(build(
            5, container="RCU-2206", plug="P-11", status=STATUS_POWERED_OFF,
            reporter="赵大勇", report_shift="白班 08:00-20:00", handler="陈志强", handler_shift="夜班 20:00-08:00",
            count=1, note="温度异常处置完成",
            power_note="货主要求提箱前断电，已在 P-11 断电并核对箱温",
            archived=True,
        ))
