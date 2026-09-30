"""冷藏箱温度报警单：完整记录链与处置规则。

规则要点（与现场口头约定一一对应）：
- 状态沿 待处理 → 处理中 → 已调温 → 已断电 一路流转，每一跳都落事件，
  断电了结必须留下处置说明；
- 越档动作（跳过环节直接了结）一律拦截，并把单子退回「处理中」；
- 同一只冷藏箱挂在同一个插电桩上反复报警，按插电桩归并到未完结的那一条，
  重复提交只认第一次，后续只累加报警次数、追加归并事件，不刷屏；
- 已调温的设定温度只能取冷机读数，读数取不到不许归档；
- 当前处理人 / 上一位处理人逐跳记录，换班后仍能追到上一位是谁。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.store import store
from app.telemetry import read_chiller

MODULE = "coldchain_alarm"

STATUS_PENDING = "待处理"
STATUS_PROCESSING = "处理中"
STATUS_ADJUSTED = "已调温"
STATUS_POWERED_OFF = "已断电"
# 状态沿此序列逐级推进；已调温/已断电属于完结环节
STATUS_ORDER = [STATUS_PENDING, STATUS_PROCESSING, STATUS_ADJUSTED, STATUS_POWERED_OFF]
OPEN_STATUSES = {STATUS_PENDING, STATUS_PROCESSING}

ACTION_ALARM = "提交报警"
ACTION_MERGED = "重复报警归并"
ACTION_START = "开始处理"
ACTION_ADJUST = "完成调温"
ACTION_POWER_OFF = "断电处置"
ACTION_ROLLBACK = "退回处理中"
ACTION_SKIP_BLOCKED = "越档拦截"

REQUIRED_ALARM_FIELDS = ["冷藏箱号", "插电桩号", "报警方向"]

# 模块级哨兵：区分「未传 from_status（取当前状态）」与「显式传 None（建单首事件）」
_UNSET = object()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _next_seq(ticket: dict[str, Any]) -> int:
    return len(ticket.get("events", [])) + 1


def _append_event(
    ticket: dict[str, Any],
    *,
    action: str,
    to_status: str | None,
    operator: str,
    note: str,
    from_status: str | None | object = _UNSET,
) -> dict[str, Any]:
    """追加一条流转事件；from_status 未传时取追加前的当前状态。"""
    before = ticket.get("status") if from_status is _UNSET else from_status
    event = {
        "seq": _next_seq(ticket),
        "time": _now(),
        "action": action,
        "from_status": before,
        "to_status": to_status if to_status is not None else ticket.get("status"),
        "operator": operator,
        "note": note,
    }
    ticket.setdefault("events", []).append(event)
    return event


def _sync_flags(ticket: dict[str, Any]) -> None:
    """pending/abnormal 永远跟监控明细（状态）重算，看板不另存计数。"""
    ticket["pending"] = ticket["status"] in OPEN_STATUSES
    ticket["abnormal"] = ticket["status"] in OPEN_STATUSES


def _find_open_by_plug(plug_no: str) -> dict[str, Any] | None:
    """同一插电桩上未完结（待处理/处理中）的报警单即归并目标。

    已调温、已断电的单子已走出处置环节，不再接收新报警；那时再报警属于
    新一轮事件，要开新单。
    """
    for row in store.rows(MODULE):
        if row.get("插电桩号") == plug_no and row.get("status") in OPEN_STATUSES:
            return row
    return None


class ColdchainAlarmService:
    # ------------------------------------------------------------------ 查询
    def list_tickets(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        plug_no: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = list(store.rows(MODULE))
        if keyword:
            rows = [
                row
                for row in rows
                if keyword in str(row.get("报警单号", ""))
                or keyword in str(row.get("冷藏箱号", ""))
            ]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if plug_no:
            rows = [row for row in rows if row.get("插电桩号") == plug_no]
        # 未完结在前、新单在前，方便处置看板优先看待办
        rows.sort(key=lambda row: (not row.get("pending", False), -int(row.get("id", 0))))
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_ticket(self, ticket_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, ticket_id)

    def board(self) -> dict[str, Any]:
        """处置看板：未完结等计数全部由监控明细实时重算。"""
        rows = store.rows(MODULE)
        by_status = {status: 0 for status in STATUS_ORDER}
        repeat_merged = 0
        for row in rows:
            status = row.get("status")
            if status in by_status:
                by_status[status] += 1
            # 归并事件数即「只认第一次」拦下的重复提交次数
            repeat_merged += sum(
                1 for event in row.get("events", []) if event.get("action") == ACTION_MERGED
            )
        return {
            "未完结": sum(by_status[status] for status in OPEN_STATUSES),
            "待处理": by_status[STATUS_PENDING],
            "处理中": by_status[STATUS_PROCESSING],
            "已调温": by_status[STATUS_ADJUSTED],
            "已断电": by_status[STATUS_POWERED_OFF],
            "重复报警归并次数": repeat_merged,
            "报警单总数": len(rows),
        }

    # ------------------------------------------------------------------ 建单
    def submit_alarm(
        self, values: dict[str, Any], operator: str
    ) -> tuple[dict[str, Any] | None, str, bool]:
        """提交（或归并）一条温度报警。

        返回 (单子, 说明, 是否为新建)；同一插电桩重复提交只认第一次。
        """
        missing = [field for field in REQUIRED_ALARM_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, f"缺少必填字段：{'、'.join(missing)}", False
        operator = operator.strip() or "未署名处理人"
        plug_no = str(values["插电桩号"]).strip()
        container_no = str(values["冷藏箱号"]).strip()
        direction = str(values["报警方向"]).strip()

        existing = _find_open_by_plug(plug_no)
        if existing is not None:
            # 同一只冷藏箱挂在同一个插电桩上反复报警 → 归并，不新建、不刷屏
            existing["报警次数"] = int(existing.get("报警次数", 1)) + 1
            existing["最近报警时间"] = _now()
            _append_event(
                existing,
                action=ACTION_MERGED,
                to_status=None,
                operator="系统",
                note=(
                    f"同一插电桩 {plug_no} 反复报警（冷藏箱 {container_no}，{direction}），"
                    f"并入本单（第 {existing['报警次数']} 次），不另开单，仍由原处理链跟进"
                ),
            )
            _sync_flags(existing)
            return existing, f"该报警已按插电桩归并到 {existing['报警单号']}，重复提交只认第一次", False

        rows = store.rows(MODULE)
        ticket_id = max((int(row.get("id", 0)) for row in rows), default=0) + 1
        ticket: dict[str, Any] = {
            "id": ticket_id,
            "报警单号": f"CCA-{_today_prefix()}-{ticket_id:03d}",
            "冷藏箱号": container_no,
            "插电桩号": plug_no,
            "报警方向": direction,
            "报警次数": 1,
            "设定温度": "—",
            "当前温度": str(values.get("当前温度") or "—"),
            "温度偏差": str(values.get("温度偏差") or "—"),
            "首次报警时间": _now(),
            "最近报警时间": _now(),
            "首位报警人": operator,
            "当前处理人": None,
            "上一位处理人": None,
            "断电说明": None,
            "events": [],
        }
        ticket["status"] = STATUS_PENDING
        _append_event(
            ticket,
            action=ACTION_ALARM,
            to_status=STATUS_PENDING,
            operator=operator,
            note=str(values.get("remark") or f"{direction}，待安排现场处置"),
            from_status=None,
        )
        _sync_flags(ticket)
        rows.append(ticket)
        return ticket, f"报警单 {ticket['报警单号']} 已建单，状态：待处理", True

    # ------------------------------------------------------------------ 流转
    def run_action(
        self,
        ticket_id: int,
        action: str,
        operator: str,
        *,
        note: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        ticket = store.find(MODULE, ticket_id)
        if ticket is None:
            return None, f"报警单 {ticket_id} 不存在或已归档"
        operator = (operator or "").strip() or "未署名处理人"
        note = (note or "").strip()
        status = ticket.get("status")

        if action == ACTION_START:
            if status == STATUS_PROCESSING:
                return ticket, f"报警单已由「{ticket.get('当前处理人')}」在处理中"
            if status != STATUS_PENDING:
                return self._reject_skip(ticket, operator, action, status, note)
            return self._transition(ticket, STATUS_PROCESSING, action, operator, note or "到场开始处置")

        if action == ACTION_ADJUST:
            if status == STATUS_ADJUSTED:
                return ticket, "该单已调温归档，等待断电了结或退回处理中"
            if status != STATUS_PROCESSING:
                # 待处理直接调温、已断电还调温都属于越档
                return self._reject_skip(ticket, operator, action, status, note)
            return self._adjust(ticket, operator, note)

        if action == ACTION_POWER_OFF:
            if status == STATUS_POWERED_OFF:
                return ticket, "该单已断电了结"
            if not note:
                return None, "断电了结必须填写处置说明（断电原因、现场安排、通知情况）"
            if status == STATUS_PROCESSING:
                # 处理中直接断电＝跳过「已调温」环节，越档不允许直接了结
                return self._reject_skip(ticket, operator, action, status, note)
            if status != STATUS_ADJUSTED:
                return self._reject_skip(ticket, operator, action, status, note)
            return self._power_off(ticket, operator, note)

        if action == ACTION_ROLLBACK:
            if status not in (STATUS_ADJUSTED, STATUS_POWERED_OFF):
                return None, f"当前状态「{status}」无需退回，只有完结环节才能退回处理中"
            return self._rollback(ticket, operator, note)

        return None, f"动作「{action}」不属于温度报警单可执行范围"

    # ------------------------------------------------------------------ 内部
    def _adjust(
        self, ticket: dict[str, Any], operator: str, note: str
    ) -> tuple[dict[str, Any], str]:
        """完成调温：设定温度只取冷机读数，取不到不许归档。"""
        reading = read_chiller(str(ticket["插电桩号"]))
        if reading is None:
            return (
                None,
                f"插电桩 {ticket['插电桩号']} 冷机读数取不到（离线或设定温度帧缺失），"
                "不得调温归档；请先恢复通讯或退回处理中继续处置",
            )
        ticket["设定温度"] = reading["setpoint"]
        ticket["当前温度"] = reading.get("current_temp") or ticket.get("当前温度")
        return self._transition(
            ticket,
            STATUS_ADJUSTED,
            ACTION_ADJUST,
            operator,
            note or f"设定温度按冷机读数置为 {reading['setpoint']}℃，读数时间 {reading.get('read_at')}",
        )

    def _power_off(
        self, ticket: dict[str, Any], operator: str, note: str
    ) -> tuple[dict[str, Any], str]:
        ticket["断电说明"] = note
        return self._transition(ticket, STATUS_POWERED_OFF, ACTION_POWER_OFF, operator, note)

    def _rollback(
        self, ticket: dict[str, Any], operator: str, note: str
    ) -> tuple[dict[str, Any], str]:
        reason = note or "现场复核认为处置未到位，退回处理中继续跟进"
        event_note = f"已从「{ticket['status']}」退回处理中：{reason}"
        # 越档/复核退回都先记下退回原因，再落到处理中
        result, message = self._transition(
            ticket, STATUS_PROCESSING, ACTION_ROLLBACK, operator, event_note
        )
        return result, message

    def _reject_skip(
        self,
        ticket: dict[str, Any],
        operator: str,
        action: str,
        status: str,
        note: str,
    ) -> tuple[dict[str, Any], str]:
        """越档拦截：不允许直接了结，必须退回处理中并留痕。"""
        if status == STATUS_PROCESSING:
            # 已在处理中：拦截本次了结，追加一条拦截事件，状态不动
            _append_event(
                ticket,
                action=ACTION_SKIP_BLOCKED,
                to_status=STATUS_PROCESSING,
                operator=operator,
                note=f"「{action}」跳过了已调温环节，不允许直接了结，维持处理中。{note}".strip(),
            )
            return None, "越档操作已拦截：必须先完成调温归档，再做断电了结"
        # 待处理直接了结 / 完结环节异常操作：强制落到处理中，由操作人认领
        self._touch_handlers(ticket, operator, status, STATUS_PROCESSING)
        ticket["status"] = STATUS_PROCESSING
        _append_event(
            ticket,
            action=ACTION_SKIP_BLOCKED,
            to_status=STATUS_PROCESSING,
            operator=operator,
            note=f"在「{status}」环节直接执行「{action}」不允许，已退回处理中。{note}".strip(),
            from_status=status,
        )
        _sync_flags(ticket)
        return None, "越档操作已拦截，报警单已退回处理中，须按 处理中→已调温→已断电 的顺序推进"

    def _transition(
        self,
        ticket: dict[str, Any],
        target: str,
        action: str,
        operator: str,
        note: str,
    ) -> tuple[dict[str, Any], str]:
        before = ticket.get("status")
        self._touch_handlers(ticket, operator, before, target)
        ticket["status"] = target
        _append_event(ticket, action=action, to_status=target, operator=operator, note=note, from_status=before)
        _sync_flags(ticket)
        return ticket, f"{ticket['报警单号']}：{before} → {target}（{operator}）"

    def _touch_handlers(
        self, ticket: dict[str, Any], operator: str, before: str, target: str
    ) -> None:
        """换班也追得到人：处理人一旦换人，旧的那位进「上一位处理人」。"""
        current = ticket.get("当前处理人")
        if current and current != operator:
            ticket["上一位处理人"] = current
        # 待处理 → 处理中：首次认领；其它环节：保持/更新处理人
        ticket["当前处理人"] = operator


def _today_prefix() -> str:
    return datetime.now().strftime("%Y%m%d")
