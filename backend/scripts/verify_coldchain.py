"""冷藏箱报警记录链业务规则的场景验证（不依赖 fastapi，直接跑服务层）。

运行：python3 backend/scripts/verify_coldchain.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.coldchain import (  # noqa: E402
    STATUS_ADJUSTED,
    STATUS_PENDING,
    STATUS_POWERED_OFF,
    STATUS_PROCESSING,
    ColdchainService,
)

NIGHT = "夜班 20:00-08:00"
DAY = "白班 08:00-20:00"
passed: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"FAIL: {name} {detail}")
    passed.append(name)


def main() -> None:
    svc = ColdchainService()

    # 0) 演示数据看板：1 待处理 + 1 处理中 = 2 未完结；归并 2（票2 报3次）；归档 1。
    board = svc.board()
    check("看板随明细重算-未完结2", board["未完结"] == 2, str(board["cards"]))
    check("看板状态计数", board["status_counts"][STATUS_PENDING] == 1
          and board["status_counts"][STATUS_PROCESSING] == 1
          and board["status_counts"][STATUS_ADJUSTED] == 1
          and board["status_counts"][STATUS_POWERED_OFF] == 1, str(board["status_counts"]))
    check("看板归并计数2", board["已归并"] == 2, str(board["cards"]))
    check("看板归档计数1", board["已归档"] == 1, str(board["cards"]))

    # 1) 缺字段拦截
    r = svc.report_alarm({"冷藏箱号": "RCU-3001"})
    check("上报缺插电桩被拒", r["ok"] is False and r["code"] == "missing", r["message"])

    # 2) 首次上报 -> 待处理
    r = svc.report_alarm({"冷藏箱号": "RCU-3001", "插电桩号": "P-21", "处理人": "甲", "工班": NIGHT})
    t1 = r["entry"]
    check("首报创建", r["code"] == "created" and t1["status"] == STATUS_PENDING, r["message"])
    check("首报人记录", t1["首报人"] == "甲" and t1["当前处理人"] is None)
    check("pending标记", t1["pending"] is True and t1["abnormal"] is True)

    # 3) 同箱同桩重复报警 -> 归并到第一张，只认第一次
    r = svc.report_alarm({"冷藏箱号": "RCU-3001", "插电桩号": "P-21", "处理人": "乙", "工班": DAY})
    check("重复报警归并", r["code"] == "merged" and r["entry"]["id"] == t1["id"], r["message"])
    check("归并不改状态/处理人", t1["status"] == STATUS_PENDING and t1["当前处理人"] is None)
    check("归并只认首报人", t1["首报人"] == "甲")
    check("报警次数累计", t1["报警次数"] == 2)
    r = svc.report_alarm({"冷藏箱号": "RCU-3001", "插电桩号": "P-21", "处理人": "丙"})
    check("第三次仍归并同一张", r["entry"]["id"] == t1["id"] and t1["报警次数"] == 3, r["message"])
    check("归并留痕", any(h["动作"] == "重复报警归并" and h["归并"] for h in t1["history"]))

    # 4) 同箱不同桩 -> 新单
    r = svc.report_alarm({"冷藏箱号": "RCU-3001", "插电桩号": "P-22", "处理人": "甲", "工班": NIGHT})
    check("换桩另立单", r["code"] == "created" and r["entry"]["id"] != t1["id"], r["message"])

    # 5) 越档：待处理直接断电 -> 拦截并退回处理中
    r = svc.run_action(t1["id"], {"action": "断电处置", "处理人": "乙", "工班": DAY, "说明": "想直接了结"})
    check("越档断电被拦截", r["ok"] is False and r["code"] == "jump_returned", r["message"])
    check("越档退回处理中", t1["status"] == STATUS_PROCESSING and t1["pending"] is True)
    check("越档退回有留痕", any(h["动作"] == "越档拦截退回" for h in t1["history"]))

    # 6) 处理中直接断电（跳过已调温）-> 仍算越档退回
    r = svc.run_action(t1["id"], {"action": "断电处置", "处理人": "乙", "说明": "再试直接断电"})
    check("处理中直接断电仍越档", r["code"] == "jump_returned", r["message"])

    # 7) 调温：设定温度取冷机读数
    r = svc.run_action(t1["id"], {"action": "调整设定温度", "处理人": "乙", "工班": DAY})
    check("调温成功", r["ok"] and r["code"] == "adjusted" and t1["status"] == STATUS_ADJUSTED, r["message"])
    check("设定温度取冷机读数", t1["设定温度"] == -18.0, str(t1["设定温度"]))
    check("调温人/时间留痕", t1["调温人"] == "乙" and t1["调温时间"])
    check("完结取消pending", t1["pending"] is False)

    # 8) 断电无说明 -> 拒绝
    r = svc.run_action(t1["id"], {"action": "断电处置", "处理人": "乙", "工班": DAY})
    check("断电无说明拒绝", r["ok"] is False and r["code"] == "missing_note", r["message"])
    check("拒绝后仍已调温", t1["status"] == STATUS_ADJUSTED)

    # 9) 断电带说明 -> 已断电
    r = svc.run_action(t1["id"], {"action": "断电处置", "处理人": "乙", "工班": DAY, "说明": "已拔 P-21 电源挂牌转修箱"})
    check("断电成功", r["ok"] and t1["status"] == STATUS_POWERED_OFF, r["message"])
    check("断电说明留存", t1["断电说明"] and t1["断电人"] == "乙")

    # 10) 完结后再操作 -> 拒绝
    r = svc.run_action(t1["id"], {"action": "接单处理", "处理人": "乙"})
    check("完结后动作拒绝", r["ok"] is False and r["code"] == "closed", r["message"])

    # 11) 归档：已断电 -> 成功
    r = svc.archive(t1["id"], {"处理人": "丙", "工班": NIGHT})
    check("已断电可归档", r["ok"] and t1["归档"] is True, r["message"])
    check("归档后默认列表隐藏", svc.get_entry(t1["id"]) is not None)
    items, _ = svc.list_entries(page=1, size=100)
    check("默认不含归档单", all(row["id"] != t1["id"] for row in items))
    items_all, _ = svc.list_entries(include_archived=True, page=1, size=100)
    check("include_archived带出", any(row["id"] == t1["id"] for row in items_all))

    # 12) 换班可追溯：记录链保留首报人(甲/夜班)、越档(乙/白班)、调温断电(乙/白班)、归档(丙/夜班)
    actors = [(h["动作"], h["处理人"], h["工班"]) for h in t1["history"]]
    check("记录链首报可追溯", ("报警上报", "甲", NIGHT) in actors, str(actors))
    check("记录链调温可追溯", ("调整设定温度", "乙", DAY) in actors, str(actors))
    check("记录链断电可追溯", ("断电处置", "乙", DAY) in actors, str(actors))
    check("记录链归档可追溯", ("归档", "丙", NIGHT) in actors, str(actors))

    # 13) 冷机读数取不到（RCU-9001）：不许调温
    r = svc.report_alarm({"冷藏箱号": "RCU-9001", "插电桩号": "P-30", "处理人": "甲", "工班": NIGHT})
    t2 = r["entry"]
    check("读数故障箱可上报", r["code"] == "created", r["message"])
    svc.run_action(t2["id"], {"action": "接单处理", "处理人": "乙", "工班": DAY})
    r = svc.run_action(t2["id"], {"action": "调整设定温度", "处理人": "乙"})
    check("读数取不到不许调温", r["ok"] is False and r["code"] == "no_reading", r["message"])
    check("调温失败状态停留处理中", t2["status"] == STATUS_PROCESSING)

    # 14) 读数取不到的已调温单不许归档：构造一张已调温但冷机离线
    r = svc.report_alarm({"冷藏箱号": "RCU-3009", "插电桩号": "P-31", "处理人": "甲", "工班": NIGHT})
    t3 = r["entry"]
    svc.run_action(t3["id"], {"action": "接单处理", "处理人": "乙", "工班": DAY})
    svc.run_action(t3["id"], {"action": "调整设定温度", "处理人": "乙"})  # RCU-3009 读数可？见读数表
    # RCU-3009 未登记读数 -> 取不到；确认调温失败
    check("未知箱调温失败", t3["status"] == STATUS_PROCESSING)
    r = svc.archive(t3["id"], {"处理人": "乙"})
    check("未完结不许归档", r["ok"] is False and r["code"] == "not_terminal", r["message"])

    # 15) 已调温单归档前再取实时读数：把已调温单(演示票3)归档应成功（读数在线）
    t_demo = svc.get_entry(3)
    r = svc.archive(3, {"处理人": "丁", "工班": NIGHT})
    check("已调温读数在线可归档", r["ok"], r["message"])

    # 16) 归档后看板重算：未完结减少
    board2 = svc.board()
    check("看板归档后重算", board2["已归档"] == board["已归档"] + 2, str(board2["cards"]))
    check("看板统计时间刷新", "统计时间" in board2)

    # 17) 重复上报只认第一次：同箱同桩即便状态变化也归并到原单（未完结期间）
    r = svc.report_alarm({"冷藏箱号": "RCU-2202", "插电桩号": "P-08", "处理人": "路人", "工班": NIGHT})
    t_demo2 = svc.get_entry(2)
    check("处理中重复仍归并首报", r["code"] == "merged" and t_demo2["首报人"] == "王建国", r["message"])
    check("处理人不被重复上报顶替", t_demo2["当前处理人"] == "李秀英")

    print(f"\n全部 {len(passed)} 项校验通过：")
    for name in passed:
        print("  ✓", name)


if __name__ == "__main__":
    main()
