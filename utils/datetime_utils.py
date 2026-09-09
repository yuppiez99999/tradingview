"""统一时区/时间工具 — A 股业务语义单一入口.

时区策略 (2026-09-09 立项, 见 cairn/LOG.md):
    1. 业务时间 (交易日/盘口/报告日期) = 北京时间 Asia/Shanghai (固定 UTC+8).
       内部统一用 naive datetime 表示北京时间, 避免 aware/naive 混用比较错误.
    2. 审计/事件时间戳 (跨系统对比、持久化、jsonl) = aware UTC + ISO 'Z' 后缀.
    3. 禁用 datetime.utcnow() — Python 3.12+ 已弃用, 且返回 naive datetime
       却标称 UTC, 是 8h 时区错位 bug 的常见来源.
    4. 禁用裸 datetime.now() — 隐式依赖本机时区, 云端 Linux(UTC) 会致 8h 偏移.

与既有实现的对应关系:
    - ms_strategy/src/execution/algo_engine.py:_now_bj_naive()  → now_bj()
    - utils/execution/broker_adapters.py:cst_tz(timezone(+8))   → CN_TZ
    - datetime.utcnow().isoformat() + "Z"                       → utc_iso()
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

# 北京时区: 固定 UTC+8 (中国无夏令时, 等价 Asia/Shanghai)
CN_TZ = timezone(timedelta(hours=8))


def now_utc() -> datetime:
    """当前 UTC 时间 (aware). 用于审计/事件时间戳."""
    return datetime.now(timezone.utc)


def utc_iso() -> str:
    """当前 UTC ISO 时间戳 (带 'Z' 后缀).

    替代 ``datetime.utcnow().isoformat() + "Z"`` — 等价但为标准 aware UTC 写法.
    """
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def now_bj() -> datetime:
    """当前北京时间 (naive). A 股业务语义标准入口.

    无论本机时区如何均返回北京时刻, 消除云端 UTC 的 8h 偏移.
    """
    return datetime.now(CN_TZ).replace(tzinfo=None)


def today_bj() -> date:
    """当前北京日期. 用于交易日判断/文件名日期/报告日期."""
    return now_bj().date()