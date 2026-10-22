"""输出文件名模板。

默认 `{name}_{i:03d}`。界面上直接选预设，也可以自己改；真正撞名时
渲染器才追加随机后缀，不会静默覆盖已有成片。

占位符同时支持两套写法，历史模板不会失效：
- `{name}` / `{i}`  —— 界面展示用的短写法
- `{task}` / `{index}` —— 早期写法
"""

from __future__ import annotations

import random
import re
from datetime import datetime
from typing import Optional

DEFAULT_NAME_TEMPLATE = "{name}_{i:03d}"

# 给界面提示用
PLACEHOLDERS = {
    "name": "任务名（同 {task}）",
    "i": "第几条，可写 {i:03d} 补零（同 {index}）",
    "date": "日期 20261013",
    "time": "时分秒 185900",
    "datetime": "日期_时分秒",
    "rand": "4 位随机串",
    "resolution": "分辨率 1080x1920",
}

# 字段名 -> values 里的键。两套写法都要能落到实际字段上，
# 少写一个就会导致那个占位符原样输出（曾经漏掉 date/time/rand）。
_ALIASES = {
    "name": "task",
    "task": "task",
    "i": "index",
    "index": "index",
    "date": "date",
    "time": "time",
    "datetime": "datetime",
    "rand": "rand",
    "resolution": "resolution",
}

_FIELD = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)(?::([^{}]+))?\}")
_ILLEGAL = '<>:"/\\|?*'


def render_name_template(
    template: str,
    *,
    task: str,
    index: int,
    resolution: Optional[str] = None,
    now: Optional[datetime] = None,
    rand: Optional[str] = None,
) -> str:
    now = now or datetime.now()
    rand = rand or f"{random.randint(0, 0xFFFF):04x}"
    values = {
        "task": task,
        "index": index,
        "date": now.strftime("%Y%m%d"),
        "time": now.strftime("%H%M%S"),
        "datetime": now.strftime("%Y%m%d_%H%M%S"),
        "rand": rand,
        "resolution": (resolution or "").replace("*", "x"),
    }

    def replace(match: re.Match) -> str:
        key = _ALIASES.get(match.group(1))
        spec = match.group(2)
        if key is None:
            # 未知占位符原样留着，用户一眼能看出写错了
            return match.group(0)
        value = values[key]
        if spec:
            try:
                return format(value, spec)
            except (TypeError, ValueError):
                return str(value)
        return str(value)

    name = _FIELD.sub(replace, template or DEFAULT_NAME_TEMPLATE)
    for ch in _ILLEGAL:
        name = name.replace(ch, "_")
    name = name.strip(" .")
    return name[:180] or "output"
