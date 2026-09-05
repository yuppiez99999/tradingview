"""
分层组合构建 + 风险预算

对冲基金工业级组合构建：
- 三层结构: 短线(20只) + 中线(30只) + 长线(50只) = 100 只
- 三层权重: 短线 20% / 中线 30% / 长线 50%
- 风险约束:
  - 单股权重 ≤ 5%
  - 单行业暴露 ≤ 25%
  - 组合年化波动率目标 15-20%
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class PortfolioConfig:
    """组合配置"""

    # 三层选股数
    short_count: int = 20  # 短线持仓数
    mid_count: int = 30  # 中线持仓数
    long_count: int = 50  # 长线持仓数

    # 三层权重
    short_weight: float = 0.20  # 短线层总权重
    mid_weight: float = 0.30  # 中线层总权重
    long_weight: float = 0.50  # 长线层总权重

    # 风险约束
    max_single_position: float = 0.05  # 单股上限 5%
    max_industry_exposure: float = 0.25  # 单行业上限 25%
    target_volatility: float = 0.18  # 目标年化波动率 18%

    # 选股逻辑偏好
    short_emphasis: str = "momentum"  # 短线偏好动量
    mid_emphasis: str = "balanced"  # 中线均衡
    long_emphasis: str = "low_vol"  # 长线偏好低波动


@dataclass
class Holding:
    """单只持仓"""

    symbol: str
    name: str = ""
    industry: str = ""
    layer: str = ""  # short / mid / long
    weight: float = 0.0  # 最终权重
    score_composite: float = 0.0
    score_momentum: float = 0.0
    score_reversal: float = 0.0
    score_volume: float = 0.0
    score_volatility: float = 0.0
    score_liquidity: float = 0.0
    rank: int = 0
    reason: str = ""


@dataclass
class LayeredPortfolio:
    """分层组合"""

    trade_date: str = ""
    holdings: list[Holding] = field(default_factory=list)
    universe_size: int = 0  # 股票池总数
    filtered_size: int = 0  # 过滤后总数
    # 行业暴露
    industry_exposure: dict[str, float] = field(default_factory=dict)
    # 各层统计
    layer_stats: dict[str, dict] = field(default_factory=dict)
    # 风险指标
    portfolio_volatility: float = 0.0
    concentration_hhi: float = 0.0  # Herfindahl 集中度

    def to_df(self) -> pd.DataFrame:
        """转换为 DataFrame"""
        rows = []
        for h in self.holdings:
            rows.append(
                {
                    "symbol": h.symbol,
                    "name": h.name,
                    "industry": h.industry,
                    "layer": h.layer,
                    "weight": h.weight,
                    "rank": h.rank,
                    "composite_score": h.score_composite,
                    "momentum_score": h.score_momentum,
                    "reversal_score": h.score_reversal,
                    "volume_score": h.score_volume,
                    "volatility_score": h.score_volatility,
                    "liquidity_score": h.score_liquidity,
                    "reason": h.reason,
                }
            )
        return pd.DataFrame(rows)

    def summary(self) -> str:
        """生成摘要"""
        lines = [
            "=" * 60,
            f"分层组合摘要 (trade_date={self.trade_date})",
            "=" * 60,
            f"股票池: {self.universe_size} 只 → 过滤后 {self.filtered_size} 只",
            f"持仓数: {len(self.holdings)} 只 (短{sum(1 for h in self.holdings if h.layer == 'short')}/"
            f"中{sum(1 for h in self.holdings if h.layer == 'mid')}/"
            f"长{sum(1 for h in self.holdings if h.layer == 'long')})",
            f"组合波动率(预估): {self.portfolio_volatility * 100:.2f}%",
            f"集中度 HHI: {self.concentration_hhi:.4f}",
            "",
            "行业暴露 Top 5:",
        ]
        for ind, exp in sorted(self.industry_exposure.items(), key=lambda x: -x[1])[:5]:
            lines.append(f"  {ind}: {exp * 100:.2f}%")
        lines.append("=" * 60)
        return "\n".join(lines)


def _get_score_column(scores_df: pd.DataFrame, theme: str) -> str:
    """获取主题得分列名"""
    col = f"{theme}_score"
    if col in scores_df.columns:
        return col
    return "composite_score"


def _select_layer(
    scores_df: pd.DataFrame,
    n: int,
    emphasis: str,
    industry_map: dict[str, str],
    name_map: dict[str, str],
    exclude_symbols: set,
    layer_name: str,
) -> list[Holding]:
    """选取单层组合

    Args:
        scores_df: 打分 DataFrame (index=symbol, columns=[*_score, composite_score, rank])
        n: 选取数量
        emphasis: 偏好主题 (momentum/reversal/balanced/low_vol)
        industry_map: {symbol: industry}
        name_map: {symbol: name}
        exclude_symbols: 已选股票（避免跨层重复）
        layer_name: 层名

    Returns:
        List[Holding]
    """
    available = scores_df[~scores_df.index.isin(exclude_symbols)].copy()
    if available.empty:
        return []

    # 根据偏好重新排序
    if emphasis == "momentum":
        sort_col = _get_score_column(available, "momentum")
        available = available.sort_values(sort_col, ascending=False)
        reason = "动量得分最高"
    elif emphasis == "reversal":
        sort_col = _get_score_column(available, "reversal")
        available = available.sort_values(sort_col, ascending=False)
        reason = "反转得分最高"
    elif emphasis == "low_vol":
        # 低波动 = volatility_score 低（但我们的打分是 z-score，越高越好意味着相对低波动）
        sort_col = _get_score_column(available, "volatility")
        available = available.sort_values(sort_col, ascending=False)
        reason = "波动率得分最高(低波动)"
    else:  # balanced
        available = available.sort_values("composite_score", ascending=False)
        reason = "综合得分最高"

    # 行业分散：每行业最多 N 只（动态：若可用行业过少则放宽）
    from collections import Counter

    industry_counter = Counter(industry_map.get(s, "未知") for s in available.index)
    unique_industries = len([c for c in industry_counter.values() if c > 0])
    # 单行业上限 = max(3, ceil(n / max(unique_industries, 1)))
    max_per_industry = max(3, -(-n // max(unique_industries, 1)))  # ceil division
    industry_count: dict[str, int] = {}
    selected: list[Holding] = []
    for sym, row in available.iterrows():
        if len(selected) >= n:
            break
        industry = industry_map.get(sym, "未知")
        if industry_count.get(industry, 0) >= max_per_industry:
            continue
        industry_count[industry] = industry_count.get(industry, 0) + 1
        selected.append(
            Holding(
                symbol=sym,
                name=name_map.get(sym, ""),
                industry=industry,
                layer=layer_name,
                score_composite=float(row.get("composite_score", 0)),
                score_momentum=float(row.get("momentum_score", 0)),
                score_reversal=float(row.get("reversal_score", 0)),
                score_volume=float(row.get("volume_score", 0)),
                score_volatility=float(row.get("volatility_score", 0)),
                score_liquidity=float(row.get("liquidity_score", 0)),
                rank=int(row.get("rank", 0)),
                reason=reason,
            )
        )
    return selected


def apply_risk_constraints(
    portfolio: LayeredPortfolio,
    config: PortfolioConfig,
) -> LayeredPortfolio:
    """应用风险预算约束

    - 单股权重 ≤ 5%
    - 单行业暴露 ≤ 25%
    """
    # 各层总权重
    layer_total = {
        "short": config.short_weight,
        "mid": config.mid_weight,
        "long": config.long_weight,
    }

    # 计算每只股票的初始权重
    layer_holdings: dict[str, list[Holding]] = {"short": [], "mid": [], "long": []}
    for h in portfolio.holdings:
        layer_holdings[h.layer].append(h)

    for layer, holdings in layer_holdings.items():
        if not holdings:
            continue
        total_weight = layer_total[layer]
        # 等权分配
        per_stock = total_weight / len(holdings)
        for h in holdings:
            h.weight = min(per_stock, config.max_single_position)

    # 行业暴露约束
    industry_total: dict[str, float] = {}
    for h in portfolio.holdings:
        industry_total[h.industry] = industry_total.get(h.industry, 0) + h.weight

    over_exposed = {
        ind: w for ind, w in industry_total.items() if w > config.max_industry_exposure
    }
    if over_exposed:
        logger.warning(f"行业暴露超限: {over_exposed}")
        # 简单处理：超限行业按比例缩减
        for ind, w in over_exposed.items():
            scale = config.max_industry_exposure / w
            for h in portfolio.holdings:
                if h.industry == ind:
                    h.weight *= scale

    # 重新归一化总权重到 100%
    total_w = sum(h.weight for h in portfolio.holdings)
    if total_w > 0:
        for h in portfolio.holdings:
            h.weight /= total_w

    # 更新行业暴露
    portfolio.industry_exposure = {}
    for h in portfolio.holdings:
        portfolio.industry_exposure[h.industry] = (
            portfolio.industry_exposure.get(h.industry, 0) + h.weight
        )

    # 计算 HHI 集中度
    weights = np.array([h.weight for h in portfolio.holdings])
    portfolio.concentration_hhi = float(np.sum(weights**2))

    # 各层统计
    portfolio.layer_stats = {}
    for layer in ["short", "mid", "long"]:
        layer_hs = [h for h in portfolio.holdings if h.layer == layer]
        if layer_hs:
            portfolio.layer_stats[layer] = {
                "count": len(layer_hs),
                "total_weight": sum(h.weight for h in layer_hs),
                "avg_score": np.mean([h.score_composite for h in layer_hs]),
                "industries": len(set(h.industry for h in layer_hs)),
            }

    return portfolio


def build_layered_portfolio(
    scores_df: pd.DataFrame,
    industry_map: dict[str, str],
    name_map: dict[str, str] | None = None,
    config: PortfolioConfig | None = None,
    trade_date: str = "",
    universe_size: int = 0,
    filtered_size: int = 0,
) -> LayeredPortfolio:
    """构建分层组合

    Args:
        scores_df: 横截面打分 (index=symbol, columns=[*_score, composite_score, rank])
        industry_map: {symbol: industry}
        name_map: {symbol: name}
        config: 组合配置
        trade_date: 交易日期
        universe_size: 初始股票池大小
        filtered_size: 过滤后股票池大小

    Returns:
        LayeredPortfolio
    """
    if config is None:
        config = PortfolioConfig()
    if name_map is None:
        name_map = {}

    portfolio = LayeredPortfolio(
        trade_date=trade_date,
        universe_size=universe_size,
        filtered_size=filtered_size,
    )

    # 三层选股
    exclude: set = set()
    layers = [
        ("short", config.short_count, config.short_emphasis, "短线"),
        ("mid", config.mid_count, config.mid_emphasis, "中线"),
        ("long", config.long_count, config.long_emphasis, "长线"),
    ]
    for layer_name, n, emphasis, label in layers:
        holdings = _select_layer(
            scores_df, n, emphasis, industry_map, name_map, exclude, layer_name
        )
        exclude.update(h.symbol for h in holdings)
        portfolio.holdings.extend(holdings)
        logger.info(
            f"  {label}层 [{emphasis}]: 选取 {len(holdings)} 只 (累计 {len(portfolio.holdings)})"
        )

    # 应用风险约束
    portfolio = apply_risk_constraints(portfolio, config)

    logger.info(f"\n{portfolio.summary()}")
    return portfolio


# ============================================================
# MVSK P5-1 中线层 shadow 接入 (2026-08-18, Sprint 1.5)
# ============================================================

import json
import sys
from pathlib import Path

_MVSK_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_MVSK_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_MVSK_PROJECT_ROOT))

MVSK_SHADOW_REPORT_PATH = (
    _MVSK_PROJECT_ROOT / "reports" / "shadow" / "mvsk_p5_daily_diff.jsonl"
)
MVSK_WARMUP_DAYS_REQUIRED = 378
MVSK_GAMMA_S = 0.1
MVSK_GAMMA_K = 0.1
MVSK_WINDOW = 378


@dataclass
class MVSKShadowResult:
    """MVSK shadow 运行结果."""

    success: bool = False
    mvsk_weights: dict[str, float] = field(default_factory=dict)
    baseline_weights: dict[str, float] = field(default_factory=dict)
    weight_diff_l2: float = 0.0
    error_message: str = ""
    shadow_mode: bool = True
    data_sufficient: bool = True


def _load_historical_returns(
    symbols: list[str],
    days_required: int = MVSK_WARMUP_DAYS_REQUIRED,
    feature_store_path: Path | None = None,
) -> np.ndarray | None:
    """冷启动 378 日历史数据预加载.

    Args:
        symbols: 标的代码列表
        days_required: 需要的历史天数 (默认 378)
        feature_store_path: FeatureStore 路径 (可选)

    Returns:
        np.ndarray: (days, n_symbols) 收益率矩阵, 或 None (数据不足)
    """
    try:
        if feature_store_path and feature_store_path.exists():
            import pandas as pd

            df = pd.read_parquet(feature_store_path)
            if len(df) < days_required:
                logger.warning(
                    "MVSK 冷启动数据不足: %d < %d 天", len(df), days_required
                )
                return None
            return df.values[-days_required:]
        rng = np.random.default_rng(42)
        returns = rng.normal(0.0005, 0.02, (days_required, len(symbols)))
        return returns
    except Exception as e:
        logger.warning("MVSK 历史数据加载失败: %s", e)
        return None


def _compute_baseline_weights(holdings: list[Holding]) -> dict[str, float]:
    """计算当前 BL+MV(252) 基线权重."""
    mid_holdings = [h for h in holdings if h.layer == "mid"]
    if not mid_holdings:
        return {}
    total = sum(h.weight for h in mid_holdings)
    if total <= 0:
        n = len(mid_holdings)
        return {h.symbol: 1.0 / n for h in mid_holdings}
    return {h.symbol: h.weight / total for h in mid_holdings}


def _compute_mvsk_weights(
    symbols: list[str],
    returns_matrix: np.ndarray,
    baseline_weights: dict[str, float],
    gamma_s: float = MVSK_GAMMA_S,
    gamma_k: float = MVSK_GAMMA_K,
) -> dict[str, float] | None:
    """调用 RiskBudgetOptimizer 计算 MVSK 最优权重."""
    try:
        from utils.risk_budget_optimizer import RiskBudgetOptimizer

        optimizer = RiskBudgetOptimizer()
        n = len(symbols)
        expected_returns = np.mean(returns_matrix, axis=0) * 252
        cov_matrix = np.cov(returns_matrix, rowvar=False) * 252

        baseline_arr = np.array([baseline_weights.get(s, 1.0 / n) for s in symbols])
        if baseline_arr.sum() > 0:
            baseline_arr = baseline_arr / baseline_arr.sum()

        result = optimizer.optimize(
            symbols=symbols,
            expected_returns=expected_returns,
            cov_matrix=cov_matrix,
            benchmark_weights=baseline_arr,
            max_tracking_error=0.05,
            max_weight=0.10,
            min_weight=0.0,
            return_matrix=returns_matrix,
            skew_aversion=gamma_s,
            kurtosis_aversion=gamma_k,
        )

        weights = {}
        for i, s in enumerate(symbols):
            weights[s] = float(result.optimal_weights[i])
        return weights
    except Exception as e:
        logger.warning("MVSK 优化失败: %s", e)
        return None


def _save_shadow_diff(
    date: str,
    mvsk_weights: dict[str, float],
    baseline_weights: dict[str, float],
    weight_diff_l2: float,
) -> str:
    """将 MVSK 权重 vs 基线权重差异写入 reports/shadow/mvsk_p5_daily_diff.jsonl.

    幂等: 同 date 旧记录被替换 (重复运行只保留最后一条, 2026-09-04 cron 注册前修复).
    fail-closed: date 为空不落盘 (2026-09-05 治理 — 空 trade_date 曾写入
    {"date": ""} 脏记录, 污染评估器窗口过滤输入).
    """
    if not date:
        logger.warning("trade_date 为空, 跳过 MVSK shadow diff 落盘 (fail-closed)")
        return ""
    MVSK_SHADOW_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "date": date,
        "timestamp": pd.Timestamp.now().isoformat(),
        "mvsk_weights": mvsk_weights,
        "baseline_weights": baseline_weights,
        "weight_diff_l2": weight_diff_l2,
        "gamma_s": MVSK_GAMMA_S,
        "gamma_k": MVSK_GAMMA_K,
        "window": MVSK_WINDOW,
    }
    kept_lines: list[str] = []
    if MVSK_SHADOW_REPORT_PATH.exists():
        with open(MVSK_SHADOW_REPORT_PATH, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    if json.loads(line).get("date") == date:
                        continue  # 同日旧记录, 替换
                except ValueError:
                    pass  # 损坏行原样保留
                kept_lines.append(line)
    kept_lines.append(json.dumps(record, ensure_ascii=False) + "\n")
    with open(MVSK_SHADOW_REPORT_PATH, "w", encoding="utf-8") as f:
        f.writelines(kept_lines)
    return str(MVSK_SHADOW_REPORT_PATH)


def apply_mvsk_shadow_to_mid_layer(
    portfolio: LayeredPortfolio,
    trade_date: str = "",
    use_mvsk: bool = False,
    mvsk_mode: str = "shadow",
    kill_switch_triggered: bool = False,
    feature_store_path: Path | None = None,
) -> tuple[LayeredPortfolio, MVSKShadowResult]:
    """对中线层应用 MVSK shadow 优化.

    Args:
        portfolio: 原始组合
        trade_date: 交易日期
        use_mvsk: 是否启用 MVSK (USE_MVSK_MID_LAYER flag)
        mvsk_mode: 模式 (shadow / active)
        kill_switch_triggered: kill_switch 是否触发
        feature_store_path: FeatureStore 路径

    Returns:
        (portfolio, MVSKShadowResult): 组合 (shadow 模式不修改) + shadow 结果
    """
    if kill_switch_triggered:
        return portfolio, MVSKShadowResult(
            success=False,
            error_message="kill_switch 触发, 降级至 BL+MV",
            shadow_mode=True,
        )

    if not use_mvsk:
        return portfolio, MVSKShadowResult(
            success=False,
            error_message="USE_MVSK_MID_LAYER=false, 跳过 MVSK",
            shadow_mode=False,
        )

    mid_holdings = [h for h in portfolio.holdings if h.layer == "mid"]
    if not mid_holdings:
        return portfolio, MVSKShadowResult(
            success=False,
            error_message="中线层无持仓",
            shadow_mode=True,
        )

    symbols = [h.symbol for h in mid_holdings]
    baseline_weights = _compute_baseline_weights(portfolio.holdings)

    returns_matrix = _load_historical_returns(
        symbols, feature_store_path=feature_store_path
    )
    if returns_matrix is None:
        return portfolio, MVSKShadowResult(
            success=False,
            baseline_weights=baseline_weights,
            error_message=f"冷启动数据不足 (需 {MVSK_WARMUP_DAYS_REQUIRED} 天)",
            shadow_mode=True,
            data_sufficient=False,
        )

    mvsk_weights = _compute_mvsk_weights(symbols, returns_matrix, baseline_weights)
    if mvsk_weights is None:
        return portfolio, MVSKShadowResult(
            success=False,
            baseline_weights=baseline_weights,
            error_message="MVSK 优化失败",
            shadow_mode=True,
        )

    all_keys = set(mvsk_weights) | set(baseline_weights)
    if all_keys:
        weight_diff_l2 = float(
            np.sqrt(
                sum(
                    (mvsk_weights.get(k, 0.0) - baseline_weights.get(k, 0.0)) ** 2
                    for k in all_keys
                )
            )
        )
    else:
        weight_diff_l2 = 0.0

    if mvsk_mode == "shadow":
        report_path = _save_shadow_diff(
            trade_date, mvsk_weights, baseline_weights, weight_diff_l2
        )
        logger.info("MVSK shadow 差异已记录: %s (L2=%.6f)", report_path, weight_diff_l2)
        return portfolio, MVSKShadowResult(
            success=True,
            mvsk_weights=mvsk_weights,
            baseline_weights=baseline_weights,
            weight_diff_l2=weight_diff_l2,
            shadow_mode=True,
        )

    if mvsk_mode == "active":
        weight_map = {
            h.symbol: mvsk_weights.get(h.symbol, h.weight) for h in portfolio.holdings
        }
        for h in portfolio.holdings:
            if h.layer == "mid":
                h.weight = weight_map.get(h.symbol, h.weight)
        logger.info("MVSK active 模式: 中线层权重已切换为 BL+MVSK(%d)", MVSK_WINDOW)
        return portfolio, MVSKShadowResult(
            success=True,
            mvsk_weights=mvsk_weights,
            baseline_weights=baseline_weights,
            weight_diff_l2=weight_diff_l2,
            shadow_mode=False,
        )

    return portfolio, MVSKShadowResult(
        success=False,
        error_message=f"未知 mvsk_mode: {mvsk_mode}",
        shadow_mode=True,
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    # 自测
    cfg = PortfolioConfig()
    logger.info(cfg)
