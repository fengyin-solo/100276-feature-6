"""冷机遥测读数：调温归档时的设定温度只认这里，不许手填。

真实项目里这里会换成对冷机监控网关（如 Modbus/载波模块）的读取；当前用一份
内存读数表模拟，并刻意保留若干「离线 / 读数缺失」的插电桩，用于演示取不到
读数时禁止归档的规则。
"""
from __future__ import annotations

from typing import Any

# key 为插电桩号；online=False 或缺少 setpoint 都视为读数取不到
CHILLER_READINGS: dict[str, dict[str, Any]] = {
    "P-1001": {"online": True, "setpoint": "-18.0", "current_temp": "-12.6", "current_amp": "14.2", "read_at": "2026-09-30 08:12:30"},
    "P-1002": {"online": True, "setpoint": "-20.0", "current_temp": "-24.8", "current_amp": "12.8", "read_at": "2026-09-30 08:12:30"},
    "P-1003": {"online": False, "setpoint": None, "current_temp": None, "current_amp": None, "read_at": "2026-09-30 07:58:02"},
    "P-1004": {"online": True, "setpoint": "2.0", "current_temp": "8.9", "current_amp": "11.5", "read_at": "2026-09-30 08:12:30"},
    "P-1005": {"online": True, "setpoint": "-25.0", "current_temp": "-25.2", "current_amp": "13.6", "read_at": "2026-09-30 08:12:30"},
    # P-1006 冷机在线但设定温度读数帧缺失，同样视为取不到
    "P-1006": {"online": True, "setpoint": None, "current_temp": "-15.0", "current_amp": "10.9", "read_at": "2026-09-30 08:10:55"},
}


def read_chiller(plug_no: str) -> dict[str, Any] | None:
    """按插电桩读一次冷机；离线或读数帧缺失时返回 None。

    归档（已调温）要求拿到有效的设定温度，只要 setpoint 为空就判为取不到，
    避免把 None / 空串当成合法设定温度写进记录链。
    """
    reading = CHILLER_READINGS.get(str(plug_no).strip())
    if reading is None or not reading.get("online"):
        return None
    if reading.get("setpoint") in (None, ""):
        return None
    return dict(reading)
