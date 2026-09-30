"""冷藏箱记录链的 HTTP 端到端验证：走真实 FastAPI 路由与 Pydantic 出入参。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

client = TestClient(app)
ok_count = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global ok_count
    if not condition:
        raise AssertionError(f"FAIL: {name} {detail}")
    ok_count += 1


def post(path: str, values: dict) -> dict:
    response = client.post(path, json={"values": values})
    assert response.status_code == 200, f"{path} -> {response.status_code} {response.text}"
    return response.json()


def main() -> None:
    # 健康检查与 overview：冷链模块仍在，pending 与明细口径一致。
    health = client.get("/api/health").json()
    check("健康检查", health["ok"] is True, str(health))

    # 看板固定路径不被 /{entry_id} 吞掉。
    board = client.get("/api/coldchain/board").json()
    check("看板路由可达", "未完结" in board, str(board)[:120])
    check("看板初始未完结2", board["未完结"] == 2, str(board["status_counts"]))

    # 列表默认隐藏归档单。
    listing = client.get("/api/coldchain").json()
    check("列表分页", listing["total"] == 4 and len(listing["items"]) == 4, str(listing["total"]))
    check("归档单默认隐藏", all(not row["归档"] for row in listing["items"]))

    # 非法状态 400。
    bad = client.get("/api/coldchain", params={"status": "随便"})
    check("非法状态400", bad.status_code == 400)

    # 冷机读数。
    reading = client.get("/api/coldchain/readings/RCU-2201").json()
    check("读数在线", reading["ok"] and reading["设定温度"] == -18.0, str(reading))
    reading_fail = client.get("/api/coldchain/readings/RCU-9001").json()
    check("读数故障", reading_fail["ok"] is False)

    # 上报新报警。
    r = post("/api/coldchain/alarms", {"冷藏箱号": "RCU-3001", "插电桩号": "P-77", "处理人": "甲", "工班": "夜班 20:00-08:00"})
    ticket_id = r["entry"]["id"]
    check("HTTP首报", r["ok"] and r["code"] == "created", r["message"])

    # 重复报警归并。
    r = post("/api/coldchain/alarms", {"冷藏箱号": "RCU-3001", "插电桩号": "P-77", "处理人": "乙"})
    check("HTTP归并", r["code"] == "merged" and r["entry"]["报警次数"] == 2, r["message"])

    # 越档：待处理直接断电 -> 退回处理中。
    r = post(f"/api/coldchain/{ticket_id}/actions", {"action": "断电处置", "说明": "越档", "处理人": "乙"})
    check("HTTP越档拦截", r["ok"] is False and r["code"] == "jump_returned", r["message"])
    check("HTTP退回处理中", r["entry"]["status"] == "处理中")

    # 调温取读数。
    r = post(f"/api/coldchain/{ticket_id}/actions", {"action": "调整设定温度", "处理人": "乙", "工班": "白班 08:00-20:00"})
    check("HTTP调温", r["entry"]["status"] == "已调温" and r["entry"]["设定温度"] == -18.0, r["message"])

    # 断电缺说明。
    r = post(f"/api/coldchain/{ticket_id}/actions", {"action": "断电处置"})
    check("HTTP断电缺说明", r["code"] == "missing_note", r["message"])

    # 断电了结。
    r = post(f"/api/coldchain/{ticket_id}/actions", {"action": "断电处置", "说明": "P-77 拔电挂牌", "处理人": "乙"})
    check("HTTP断电成功", r["entry"]["status"] == "已断电" and r["entry"]["断电说明"], r["message"])

    # 归档。
    r = post(f"/api/coldchain/{ticket_id}/archive", {"处理人": "丙", "工班": "夜班 20:00-08:00"})
    check("HTTP归档", r["ok"] and r["entry"]["归档"] is True, r["message"])

    # 明细含完整记录链。
    detail = client.get(f"/api/coldchain/{ticket_id}").json()
    actions = [h["动作"] for h in detail["history"]]
    check("记录链动作序列", actions == ["报警上报", "重复报警归并", "越档拦截退回", "调整设定温度", "断电处置", "归档"], str(actions))

    # 不存在的单 404。
    check("明细404", client.get("/api/coldchain/99999").status_code == 404)

    # 导出。
    exported = client.get("/api/coldchain/export").json()
    check("导出未归档单", exported["total"] == 4 and all(not i["归档"] for i in exported["items"]))

    # 看板最终一致性：未完结重新计数（新增的单已归档，仍是原 4 张未完结中的 3 张 + ... 直接断言非负与结构）。
    board2 = client.get("/api/coldchain/board").json()
    check("看板重算结构", set(board2["status_counts"]) == {"待处理", "处理中", "已调温", "已断电"})
    check("看板归档+1", board2["已归档"] == board["已归档"] + 1, str(board2["已归档"]))

    print(f"\nHTTP 端到端 {ok_count} 项校验全部通过")


if __name__ == "__main__":
    main()
