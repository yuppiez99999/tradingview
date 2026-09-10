"""System Health Score 聚合引擎 (Production Edition T2, 2026-09-02).

五维遥测 → 加权评分 (0-100), 报告侧零侵入:
  model   0.25  reports/drift/integration_{date}.json (IC/ICIR/漂移)
  data    0.20  reports/degradation_log.jsonl (当日降级条目)
  trading 0.15  reports/tca/fills_{date}.jsonl (成交与 TCA 预估覆盖)
  risk    0.20  reports/evolution/vol_regime_weights_{date}.json (regime)
  capital 0.20  output/shadow_account/s12_shadow_state.json (NAV/fail-fast)

降级语义: 某维数据不可得 → 60 分中性值 + degraded=True 显式标记
(fail-open, 观测路径不阻断).

shadow 阶段豁免 (v3, 2026-09-02): model/trading/risk 三维的数据源属
**主策略实盘链**产物, 纯 S12 shadow 阶段不会生成 → 系统处于 shadow 期
(shadow 账户在跑且 reports/tca 从未有过 fills, 即实盘未启动) 时, 该三维
数据源缺失不按降级计, 标记 exempted=True 并从总分中剔除权重 (归一化)。
实盘启动 (出现历史 fills 文件) 后豁免自动失效 —— 届时主链产物缺失重新
按降级计分, 分数回落即"实盘链路补全"的验证信号。

数据维去重 (v2, 2026-09-02): degradation_log.jsonl 跨进程累计, 同一
(scope, key) 事件在多次运行中会重复落盘 (config_manager 配置缺失刷屏).
score_data 以**当日去重后的事件数**计分, 衡量"当天发生了几种不同的降级
问题", 而非原始条目数; 测试/演练产生的降级 (scope 前缀 chaos_/test_)
默认不计入生产健康度, 避免人为演练把评分打成假 RED.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from utils.datetime_utils import now_bj

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
    exempted: bool = False


def _live_fills_exist(project_root: Path) -> bool:
    """reports/tca 是否存在**实盘**成交 (非仿真 broker).

    注意: fills_*.jsonl 可能来自期权对冲仿真链 (broker 含 "sim", 如
    OptionsSimBroker) —— 仿真成交不代表主链实盘启动, 不能关掉豁免。
    仅出现非仿真 broker (如 QMT) 的 fills 才视为实盘已启动; broker 缺失
    时保守视为实盘 (宁可多降级, 不误豁免)。
    """
    tca_dir = project_root / "reports" / "tca"
    if not tca_dir.is_dir():
        return False
    for f in tca_dir.glob("fills_*.jsonl"):
        try:
            content = f.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in content.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not (isinstance(rec, dict) and rec.get("type") == "fill"):
                continue
            broker = str((rec.get("fill") or {}).get("broker", ""))
            if "sim" not in broker.lower():
                return True
    return False


def _shadow_phase(project_root: Path) -> bool:
    """系统处于 shadow 阶段: shadow 账户在跑且从未有过实盘 TCA fills.

    实盘启动 (出现非仿真 broker 的 fills) 后豁免自动失效。
    """
    shadow_state = project_root / "output" / "shadow_account" / "s12_shadow_state.json"
    if not shadow_state.exists():
        return False
    return not _live_fills_exist(project_root)


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


def _exempt_or_degraded(dimension: str, reason: str,
                        project_root: Path) -> DimensionScore:
    """主链专属数据源缺失: shadow 阶段豁免 (不适用), 否则中性降级."""
    if _shadow_phase(project_root):
        return DimensionScore(
            score=100.0,
            weight=WEIGHTS[dimension],
            degraded=False,
            exempted=True,
            detail={"reason": reason, "exemption": "shadow_phase"},
        )
    return _degraded(dimension, reason)


def score_model(project_root: Path, date: str) -> DimensionScore:
    """模型维: drift integration 的 IC 退化与告警."""
    path = project_root / "reports" / "drift" / f"integration_{date}.json"
    if not path.exists():
        return _exempt_or_degraded(
            "model", "drift integration 文件不存在", project_root)
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
    # shadow phase: 等权策略 ic_ir=0 是预期行为 (无选股能力),
    # ic_ir_degradation alert 不扣分, model 维度豁免 (2026-09-04 分析)
    if _shadow_phase(project_root) and alerts:
        score = 100.0
        return DimensionScore(
            score=score,
            weight=WEIGHTS["model"],
            degraded=False,
            exempted=True,
            detail={
                "ic": (data.get("delayed_metrics") or {}).get("ic"),
                "rank_ic": (data.get("delayed_metrics") or {}).get("rank_ic"),
                "ic_ir": (data.get("delayed_metrics") or {}).get("ic_ir"),
                "ic_degradation": ic_deg,
                "alerts": alerts,
                "reason": "shadow phase 等权策略 ic_ir=0 预期行为",
                "exemption": "shadow_phase",
            },
        )
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


def _backup_stale(backup_root: Path, today: str) -> bool:
    """最新备份目录距今 >4 天 (含周末+节假日缓冲) 视为过期.

    评分 (17:05) 在备份 (17:30) 之前, 故检查的是最新一次备份而非当日.
    """
    if not backup_root.is_dir():
        return True
    dated: list[str] = []
    for d in backup_root.iterdir():
        if d.is_dir() and len(d.name) == 10 and d.name[4] == "-":
            try:
                datetime.strptime(d.name, "%Y-%m-%d")
            except ValueError:
                continue
            dated.append(d.name)
    if not dated:
        return True
    latest = datetime.strptime(max(dated), "%Y-%m-%d").date()
    today_dt = datetime.strptime(today, "%Y-%m-%d").date()
    return (today_dt - latest).days > 4


DEFAULT_IGNORE_SCOPE_PREFIXES = ("chaos_", "test_")
# 已知慢性配置债 scope: config_manager 的"配置文件不存在"是长期存在的已知缺口,
# 每天被重复记录, 若计入会令数据维永久满负荷, 无法指示新的运行时降级 ——
# 只列入 detail 明细 (config_missing_keys), 不参与计分。
DEFAULT_CHRONIC_SCOPES = ("config_manager",)


def score_data(
    project_root: Path,
    date: str,
    backup_root: Path | None = None,
    ignore_scope_prefixes: tuple[str, ...] = DEFAULT_IGNORE_SCOPE_PREFIXES,
    chronic_scopes: tuple[str, ...] = DEFAULT_CHRONIC_SCOPES,
) -> DimensionScore:
    """数据维: 当日**去重后**的运行时降级事件数 + 备份新鲜度 (可选).

    语义 (v2, 2026-09-02):
      - degradation_log.jsonl 跨进程累计, 同一 (scope, key) 事件重复落盘
        → 用去重后的事件数计分, 而非原始条目数 (避免 config_manager 跨进程
        重复刷屏把评分打成假 RED)。
      - 测试/演练产生的降级 (scope 前缀匹配 ignore_scope_prefixes, 默认
        chaos_/test_) 不计入生产健康度。
      - 慢性配置债 scope (chronic_scopes, 默认 config_manager) 只入 detail
        明细, 不参与计分 —— 让数据维反映"运行时降级"而非长期存在的已知缺口。

    backup_root 提供时: 最新备份距今 >4 天 → 额外 -20 (下限 0)。
    """
    path = project_root / "reports" / "degradation_log.jsonl"
    if not path.exists():
        return _degraded("data", "degradation_log.jsonl 不存在")
    events: set[tuple[str, str]] = set()
    chronic_keys: set[str] = set()
    raw_n = 0
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
            raw_n += 1
            scope = str(rec.get("scope", ""))
            key = str(rec.get("key", ""))
            if scope in chronic_scopes:
                chronic_keys.add(key)
                continue
            if any(scope.startswith(p) for p in ignore_scope_prefixes):
                continue
            events.add((scope, key))
            scopes.add(scope)
    n = len(events)
    if n == 0:
        score = 100.0
    elif n <= 2:
        score = 80.0
    elif n <= 5:
        score = 60.0
    else:
        score = 40.0
    detail: dict = {
        "entries": raw_n,
        "distinct_events": n,
        "scopes": sorted(scopes),
        "config_missing_keys": sorted(chronic_keys),
    }
    if backup_root is not None:
        stale = _backup_stale(backup_root, date)
        detail["backup_stale"] = stale
        if stale:
            score = max(0.0, score - 20.0)
    return DimensionScore(
        score=score,
        weight=WEIGHTS["data"],
        degraded=False,
        detail=detail,
    )


def score_trading(project_root: Path, date: str) -> DimensionScore:
    """交易维: 当日 TCA 成交记录与预估覆盖率."""
    path = project_root / "reports" / "tca" / f"fills_{date}.jsonl"
    if not path.exists():
        return _exempt_or_degraded(
            "trading", "当日 TCA fills 文件不存在 (无交易或未落盘)",
            project_root)
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
        return _exempt_or_degraded(
            "risk", "vol_regime 权重报告不存在", project_root)
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


def score_capital(project_root: Path, date: str) -> DimensionScore:
    """资金维: shadow 账户状态 (NAV 合理性 + fail-fast)."""
    path = project_root / "output" / "shadow_account" / "s12_shadow_state.json"
    if not path.exists():
        return _degraded("capital", "shadow 账户状态文件不存在")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _degraded("capital", "shadow 账户状态解析失败")
    if not isinstance(data, dict):
        return _degraded("capital", "shadow 账户状态结构异常")
    if data.get("fail_fast_triggered"):
        return DimensionScore(
            score=0.0,
            weight=WEIGHTS["capital"],
            degraded=False,
            detail={"fail_fast_triggered": True},
        )
    try:
        nav = float(data.get("nav", 1.0))
    except (TypeError, ValueError):
        nav = 1.0
    score = 100.0 if 0.5 <= nav <= 2.0 else 50.0
    return DimensionScore(
        score=score,
        weight=WEIGHTS["capital"],
        degraded=False,
        detail={"nav": nav, "trading_day_count": data.get("trading_day_count")},
    )


def compute_health_score(project_root: Path, date: str,
                         backup_root: Path | None = None) -> dict:
    """五维聚合 → 评分报告 dict (落盘由 CLI 负责)."""
    scorers = {
        "model": score_model,
        "data": score_data,
        "trading": score_trading,
        "risk": score_risk,
        "capital": score_capital,
    }
    dims: dict[str, DimensionScore] = {}
    for name, fn in scorers.items():
        if name == "data":
            dims[name] = fn(project_root, date, backup_root=backup_root)
        else:
            dims[name] = fn(project_root, date)
    # shadow 豁免维度不计入总分: 权重剔除后归一化 (豁免 = 不适用, 非满分)
    active = [d for d in dims.values() if not d.exempted]
    weight_sum = sum(d.weight for d in active)
    if active and weight_sum > 0:
        total = round(sum(d.score * d.weight for d in active) / weight_sum, 1)
    else:
        total = round(sum(d.score * d.weight for d in dims.values()), 1)
    return {
        "date": date,
        "generated_at": now_bj().isoformat(timespec="seconds"),
        "total_score": total,
        "status": status_for(total),
        "dimensions": {k: asdict(v) for k, v in dims.items()},
        "degraded_dimensions": [k for k, v in dims.items() if v.degraded],
        "exempted_dimensions": [k for k, v in dims.items() if v.exempted],
    }
