"""生产运营中心数据加载层 (Production Edition T5, 2026-09-02).

纯数据函数, 不 import streamlit — 页面渲染层只消费这里的返回值.
数据源三件 (全部只读):
  reports/health_score/health_score_{date}.json   T2 聚合引擎产物
  output/shadow_account/s12_shadow_state.json     S12 shadow 账户状态
  reports/degradation_log.jsonl                   降级审计日志
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

_STATUS_COLORS = {"GREEN": "green", "YELLOW": "orange", "RED": "red"}


def derive_status_color(status: str) -> str:
    """评分状态 → 显示色."""
    return _STATUS_COLORS.get(str(status).upper(), "gray")


def load_health_history(project_root: Path, days: int = 30) -> list[dict]:
    """按日期升序加载近 N 日评分 JSON (损坏文件跳过)."""
    d = Path(project_root) / "reports" / "health_score"
    if not d.is_dir():
        return []
    records: list[dict] = []
    for f in d.glob("health_score_*.json"):
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(rec, dict) and "date" in rec and "total_score" in rec:
            records.append(rec)
    records.sort(key=lambda r: str(r["date"]))
    return records[-days:] if days > 0 else records


def load_latest_health(project_root: Path) -> dict | None:
    """最新一日评分 (无历史返回 None)."""
    hist = load_health_history(project_root, days=1)
    return hist[-1] if hist else None


def load_anomaly_timeline(project_root: Path, days: int = 7, today: str | None = None) -> list[dict]:
    """近 N 日降级日志条目 (升序, 损坏行跳过)."""
    path = Path(project_root) / "reports" / "degradation_log.jsonl"
    if not path.is_file():
        return []
    today_str = today or datetime.now().strftime("%Y-%m-%d")
    try:
        cutoff = (
            datetime.strptime(today_str, "%Y-%m-%d") - timedelta(days=days)
        ).strftime("%Y-%m-%d")
    except ValueError:
        cutoff = "0000-00-00"
    out: list[dict] = []
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict):
            continue
        ts = str(rec.get("ts", ""))
        if ts >= cutoff:
            out.append(rec)
    return out


_GRADIENT_STAGES = [
    {"name": "Sprint3-1 · 20 万测试", "target": "小资金链路验证"},
    {"name": "Sprint3-2 · 100 万灰度 shadow", "target": "30 天影子对照"},
    {"name": "Sprint3-3 · 200 万正式", "target": "实盘切换"},
]


def load_shadow_progress(project_root: Path) -> dict:
    """v9_200w_preset 灰度阶段进度 (静态阶段表 + shadow state 动态天数)."""
    state_path = Path(project_root) / "output" / "shadow_account" / "s12_shadow_state.json"
    state: dict = {}
    if state_path.is_file():
        try:
            loaded = json.loads(state_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                state = loaded
        except (OSError, ValueError):
            state = {}
    stages = [
        {**s, "status": "未开始"} for s in _GRADIENT_STAGES
    ]
    trading_days = int(state.get("trading_day_count", 0) or 0)
    current = "未启动"
    if state:
        # shadow state 存在 (S12_SHADOW_P3, 200 万虚拟资本) = 已越过 20 万测试与
        # 100 万灰度, 处于 Phase 3 影子验证 (200 万虚拟) — 即 Sprint3-3 实盘切换前的
        # 影子对照阶段, 未满 30 个交易日按"进行中", 满 30 日按"待切换".
        stages[0]["status"] = "已完成"
        stages[1]["status"] = "已完成"
        stages[2]["status"] = "进行中"
        current = "Phase 3 影子验证 (200 万虚拟)"
        if trading_days >= 30:
            stages[2]["status"] = "待切换"
            current = stages[2]["name"]
    return {
        "current_stage": current,
        "stages": stages,
        "trading_day_count": trading_days,
        "start_date": state.get("start_date"),
        "account_id": state.get("account_id"),
    }
