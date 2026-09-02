"""System Health Score 聚合引擎 (Production Edition T2, 2026-09-02).

五维遥测 → 加权评分 (0-100), 报告侧零侵入:
  model   0.25  reports/drift/integration_{date}.json (IC/ICIR/漂移)
  data    0.20  reports/degradation_log.jsonl (当日降级条目)
  trading 0.15  reports/tca/fills_{date}.jsonl (成交与 TCA 预估覆盖)
  risk    0.20  reports/evolution/vol_regime_weights_{date}.json (regime)
  capital 0.20  output/shadow_account/s12_shadow_state.json (NAV/fail-fast)

降级语义: 某维数据不可得 → 60 分中性值 + degraded=True 显式标记
(fail-open, 观测路径不阻断).

已知噪音 (v1 接受): degradation_log.jsonl 含测试进程产生的条目,
同日多次 pytest 运行会累计, v1 不区分来源.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

DEGRADED_NEUTRAL = 60.0

WEIGHTS: dict[str, float] = {
    "model": 0.25,
    "data": 0.20,
    "trading": 0.15,
    "risk": 0.20,
    "capital": 0.20,
}


@dataclass
class DimensionScore:
    """单维评分: 0-100 分 + 权重 + 降级标记 + 明细."""

    score: float
    weight: float
    degraded: bool
    detail: dict = field(default_factory=dict)


def status_for(total: float) -> str:
    """总分 → 状态色: ≥85 GREEN / ≥70 YELLOW / <70 RED."""
    if total >= 85.0:
        return "GREEN"
    if total >= 70.0:
        return "YELLOW"
    return "RED"


def _degraded(dimension: str, reason: str) -> DimensionScore:
    """数据不可得时的中性降级评分."""
    return DimensionScore(
        score=DEGRADED_NEUTRAL,
        weight=WEIGHTS[dimension],
        degraded=True,
        detail={"reason": reason},
    )


def score_model(project_root: Path, date: str) -> DimensionScore:
    """模型维: drift integration 的 IC 退化与告警."""
    path = project_root / "reports" / "drift" / f"integration_{date}.json"
    if not path.exists():
        return _degraded("model", "drift integration 文件不存在")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _degraded("model", "drift integration JSON 解析失败")
    if not isinstance(data, dict):
        return _degraded("model", "drift integration 结构异常")
    if data.get("skipped") or data.get("error"):
        return _degraded("model", f"drift integration 未完成: {data.get('error')}")

    try:
        ic_deg = float(data.get("ic_degradation", 1.0))
    except (TypeError, ValueError):
        ic_deg = 1.0
    score = 70.0
    if ic_deg < 0.3:
        score += 30.0
    elif ic_deg < 0.6:
        score += 15.0
    alerts = data.get("alerts") or []
    score = max(0.0, score - 10.0 * len(alerts))
    dm = data.get("delayed_metrics") or {}
    return DimensionScore(
        score=score,
        weight=WEIGHTS["model"],
        degraded=False,
        detail={
            "ic": dm.get("ic"),
            "rank_ic": dm.get("rank_ic"),
            "ic_ir": dm.get("ic_ir"),
            "ic_degradation": ic_deg,
            "alerts": alerts,
        },
    )


def score_data(project_root: Path, date: str) -> DimensionScore:
    """数据维: 当日降级审计条目数 (含测试进程噪音, v1 不区分)."""
    path = project_root / "reports" / "degradation_log.jsonl"
    if not path.exists():
        return _degraded("data", "degradation_log.jsonl 不存在")
    n = 0
    scopes: set[str] = set()
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return _degraded("data", "degradation_log.jsonl 读取失败")
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
        if str(rec.get("ts", "")).startswith(date):
            n += 1
            scopes.add(str(rec.get("scope", "")))
    if n == 0:
        score = 100.0
    elif n <= 2:
        score = 80.0
    elif n <= 5:
        score = 60.0
    else:
        score = 40.0
    return DimensionScore(
        score=score,
        weight=WEIGHTS["data"],
        degraded=False,
        detail={"entries": n, "scopes": sorted(scopes)},
    )


def score_trading(project_root: Path, date: str) -> DimensionScore:
    """交易维: 当日 TCA 成交记录与预估覆盖率."""
    path = project_root / "reports" / "tca" / f"fills_{date}.jsonl"
    if not path.exists():
        return _degraded("trading", "当日 TCA fills 文件不存在 (无交易或未落盘)")
    fills, estimated = 0, 0
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return _degraded("trading", "TCA fills 文件读取失败")
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("type") == "fill":
            fills += 1
            if rec.get("estimate"):
                estimated += 1
    if fills == 0:
        return _degraded("trading", "当日无成交记录")
    coverage = estimated / fills
    score = 100.0 if coverage >= 0.5 else 70.0
    return DimensionScore(
        score=score,
        weight=WEIGHTS["trading"],
        degraded=False,
        detail={"fills": fills, "estimate_coverage": round(coverage, 4)},
    )


_REGIME_SCORES = {"bull": 100.0, "sideways": 95.0, "neutral": 95.0, "bear": 80.0}


def score_risk(project_root: Path, date: str) -> DimensionScore:
    """风险维: vol regime 状态 (bull 100 / sideways 95 / bear 80)."""
    path = project_root / "reports" / "evolution" / f"vol_regime_weights_{date}.json"
    if not path.exists():
        return _degraded("risk", "vol_regime 权重报告不存在")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _degraded("risk", "vol_regime 报告解析失败")
    if not isinstance(data, dict):
        return _degraded("risk", "vol_regime 报告结构异常")
    regime = data.get("regime") or {}
    label = str(regime.get("label", "")).lower()
    if data.get("degraded"):
        return DimensionScore(
            score=50.0,
            weight=WEIGHTS["risk"],
            degraded=False,
            detail={"regime": label, "regime_engine_degraded": True},
        )
    score = _REGIME_SCORES.get(label, 90.0)
    return DimensionScore(
        score=score,
        weight=WEIGHTS["risk"],
        degraded=False,
        detail={
            "regime": label,
            "confidence": regime.get("confidence"),
            "observation_phase": data.get("observation_phase"),
        },
    )
