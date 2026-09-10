"""TrendCast 信号卡片 — 以只读方式追加到每日报告.

设计约束 (2026-09-09, 与 16_↔28 集成一期一致):
  * 观测路径 fail-open: 快照缺失/解析失败/写入失败一律跳过并告警, 绝不影响主报告生成;
  * 只读: 卡片仅作决策上下文, 不构成下单/调仓建议, 不回写任何交易状态;
  * 幂等: 同一报告重复调用不会追加两份卡片 (判断是否已含卡片标题).

接入点:
  * ``generate_daily_report.py`` — 写 Markdown 日报后追加 (EOD 主链路);
  * ``daily_runner.py`` — 步骤3 报告生成后追加 (legacy 入口).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from utils.datetime_utils import now_bj

logger = logging.getLogger(__name__)

CARD_TITLE = "## 外部信号 · TrendCast Pro 多周期方向预测"
_HORIZONS = ("short_term", "mid_term", "long_term")


def _project_root() -> Path:
    # utils/reporting/ -> 项目根
    return Path(__file__).resolve().parents[2]


def snapshot_path(report_date: Optional[str] = None) -> Path:
    """当日 TrendCast 信号快照路径 (logs/trendcast/signals_YYYY-MM-DD.json)."""
    date_str = report_date or now_bj().strftime("%Y-%m-%d")
    return _project_root() / "logs" / "trendcast" / f"signals_{date_str}.json"


def build_card(data: dict) -> str:
    """由快照内容构造 Markdown 卡片文本; 无预测时返回空串."""
    preds = data.get("predictions") or []
    if not preds:
        return ""

    generated_at = data.get("generated_at", "?")
    section = [
        "",
        CARD_TITLE,
        "",
        "> 数据来源: 16_ 金融市场预测模型 (LightGBM); 仅作决策上下文, 不构成下单建议。",
        "",
        f"- 预测标的数: {len(preds)} | 模型: {data.get('model_type', '?')} | 生成时间: {generated_at}",
    ]

    if _is_degenerate(data):
        section.extend(
            [
                "",
                "> [警告] 当前模型输出无区分度 (所有周期概率恒为 50%) —— 16_ 数据源仍为"
                " simulation, 该信号禁止用于打分/下单, 仅作占位展示。",
            ]
        )

    for p in preds[:20]:
        symbol = p.get("symbol", "?")
        horizons = p.get("horizons") or {}
        parts = []
        for horizon in _HORIZONS:
            info = horizons.get(horizon)
            if isinstance(info, dict):
                parts.append(
                    f"{horizon[:4]}:{info.get('direction', '?')}"
                    f"({info.get('probability', 0):.0%})"
                )
        if parts:
            section.append(f"- **{symbol}**: " + "  ".join(parts))
    if len(preds) > 20:
        section.append(f"- (仅显示前 20 只, 共 {len(preds)} 只)")
    section.append("")
    return "\n".join(section)


def _is_degenerate(data: dict) -> bool:
    """所有周期概率恒为 50% 时视为无区分度 (16_ 数据源仍为 simulation 的特征)."""
    probs = []
    for pred in data.get("predictions") or []:
        for info in (pred.get("horizons") or {}).values():
            if isinstance(info, dict):
                probs.append(float(info.get("probability", 0.0)))
    return bool(probs) and all(abs(p - 0.5) < 1e-9 for p in probs)


def append_trendcast_card(report_paths: Any, report_date: Optional[str] = None) -> bool:
    """把当日 TrendCast 信号卡片追加到指定报告文件.

    Args:
        report_paths: 报告文件路径 (str/Path 或它们的序列)
        report_date: 报告日期 YYYY-MM-DD, 缺省取今天

    Returns:
        是否至少成功追加了一份报告。
    """
    try:
        snap = snapshot_path(report_date)
        if not snap.exists():
            logger.info("[TrendCast] 无当日信号快照 (%s), 跳过卡片追加", snap.name)
            return False

        data = json.loads(snap.read_text(encoding="utf-8"))
        block = build_card(data)
        if not block:
            logger.info("[TrendCast] 快照无预测记录, 跳过卡片追加")
            return False

        if isinstance(report_paths, (str, Path)):
            paths = [report_paths]
        else:
            paths = list(report_paths)

        appended = False
        for path in paths:
            try:
                target = Path(path)
                if not target.exists():
                    logger.warning("[TrendCast] 报告文件不存在, 跳过: %s", target)
                    continue
                existing = target.read_text(encoding="utf-8", errors="ignore")
                if CARD_TITLE in existing:
                    logger.info("[TrendCast] 卡片已存在, 幂等跳过: %s", target)
                    continue
                with open(target, "a", encoding="utf-8") as f:
                    f.write(block)
                appended = True
                logger.info("[TrendCast] 信号卡片已追加: %s", target)
            except OSError as exc:
                logger.warning("[TrendCast] 追加信号卡片失败 %s: %s", path, exc)
        return appended
    except (json.JSONDecodeError, OSError, TypeError, ValueError) as exc:
        logger.warning("[TrendCast] 信号卡片追加跳过: %s", exc)
        return False
