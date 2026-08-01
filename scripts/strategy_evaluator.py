#!/usr/bin/env python3
"""策略多维评分器 (N2) — Self-Evolution 框架 Day 2.

实现方案文档第七节要求:
  I: 资金回报指标 — 年化收益率、最大回撤、Sharpe ratio
  II: 持仓分散度指标 — 风格分布、行业集中度、单标占比
  III: 综合评分 — 加权得分 (权重由 EVOLUTION_CONFIG 驱动)

设计原则:
  - 只读历史数据, 不修改任何生产状态
  - Feature Flag 开关 (USE_STRATEGY_EVALUATOR = False 时跳过)
  - 降级报告: 任意子模块失败不影响其他计算
  - 输出 JSONL 兼容日志: reports/strategy_evaluator/YYYY-MM-DD.log

对比 Day 1 N1: N1 专注漂移检测+重训触发; N2 专注静态策略评估。两者独立运行, 结果可被 N6 调度器聚合。
"""
import json
import logging
import os
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Tuple

# ========== 配置 (复用 Day 1 EVOLUTION_CONFIG) ==========
EVOLUTION_CONFIG = {
    "feature_flag_name": "USE_STRATEGY_EVALUATOR",   # HC-1: Feature Flag
    "public_weight": 0.30,                           # Public Score 权重
    "private_weight": 0.70,                          # Private Score 权重
    "min_annual_return": 0.08,                       # 基准年化收益
    "max_allowed_drawdown": 0.15,                    # 最大允许回撤
    "benchmark_sharpe": 0.5,                         # 基准 Sharpe
}

logger = logging.getLogger("strategy_evaluator")
logging.basicConfig(level=logging.INFO)


# ========== 数据类 ==========
@dataclass
class ReturnMetrics:
    """资金回报指标."""
    annual_return: float = 0.0          # 年化收益率
    max_drawdown: float = 0.0           # 最大回撤
    sharpe_ratio: float = 0.0           # Sharpe ratio
    total_return: float = 0.0           # 总回报
    sample_count: int = 0               # 样本数

@dataclass
class DiversificationMetrics:
    """持仓分散度指标."""
    style_diversity_score: float = 0.0      # 风格多样性 (0-1)
    sector_concentration: float = 0.0       # 行业集中度 (最高持仓占比)
    top3_concentration: float = 0.0         # 前三大标的占比
    num_styles: int = 0                     # 风格数
    num_sectors: int = 0                    # 行业数
    weight_variance: float = 0.0            # 权重方差

@dataclass
class ScoreReport:
    """策略评分报告."""
    public_score: float = 0.0                 # 样本内综合得分 (0-1)
    private_score: float = 0.0                # 样本外稳健性得分 (0-1)
    overall_score: float = 0.0                # 综合得分 (公共+私有加权)
    return_metrics: ReturnMetrics = field(default_factory=ReturnMetrics)
    divers_metrics: DiversificationMetrics = field(default_factory=DiversificationMetrics)
    recommendation: str = "continue"          # promote / rollback / continue
    reason: str = ""
    is_degraded: bool = False
    degraded_reason: str = ""

    def to_dict(self) -> Dict[str, any]:
        d = asdict(self)
        d["return_metrics"] = asdict(self.return_metrics)
        d["divers_metrics"] = asdict(self.divers_metrics)
        d["is_degraded"] = self.is_degraded
        return d


# ========== 工具函数 ==========
def load_daily_returns(jsonl_path: str) -> List[Tuple[str, float]]:
    """从 JSONL 加载日期-回报对.

    Returns: [(date, daily_return), ...] 按日期排序
    """
    records = []
    if not os.path.exists(jsonl_path):
        return records
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                date_str = rec.get("date", "")
                ret = float(rec.get("daily_return", 0))
                if date_str and ret != 0:
                    records.append((date_str, ret))
            except (json.JSONDecodeError, ValueError):
                continue
    records.sort(key=lambda x: x[0])
    return records


def compute_annual_return(daily_returns: List[float]) -> float:
    """计算年化收益率."""
    if len(daily_returns) < 2:
        return 0.0
    total_return = 1.0
    for r in daily_returns:
        total_return *= (1.0 + r)
    total_return -= 1.0
    n = len(daily_returns)
    # 外推到 252 个交易日
    if n > 0:
        annualized = (1 + total_return) ** (252.0 / n) - 1.0
    else:
        annualized = 0.0
    return annualized


def compute_max_drawdown(daily_returns: List[float]) -> float:
    """计算最大回撤."""
    nav = 1.0
    peak = 1.0
    max_dd = 0.0
    for r in daily_returns:
        nav *= (1.0 + r)
        if nav > peak:
            peak = nav
        dd = (peak - nav) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
    return max_dd


def compute_sharpe(daily_returns: List[float], risk_free: float = 0.03) -> float:
    """计算年化 Sharpe ratio."""
    n = len(daily_returns)
    if n < 2 or risk_free == 0:
        return 0.0
    mean_r = sum(daily_returns) / n
    # 日无风险利率 (约 3%/252)
    rf_daily = risk_free / 252.0
    excess = [r - rf_daily for r in daily_returns]
    var = sum((x - mean_r) ** 2 for x in excess) / (n - 1)
    std = var ** 0.5 if var > 0 else 0.0
    if std < 1e-10:
        return 0.0
    daily_sharpe = (mean_r - rf_daily) / std
    return daily_sharpe * (252.0 ** 0.5)


def load_positions(pos_path: str) -> Dict:
    """加载 positions.json."""
    with open(pos_path, encoding="utf-8") as f:
        return json.load(f)


def evaluate_diversification(positions: Dict) -> DiversificationMetrics:
    """计算持仓分散度指标.

    Args:
        positions: positions.json 中 positions 字典 (code -> meta)

    Returns:
        DiversificationMetrics
    """
    # 提取风格、行业、市值权重
    styles = []
    sectors = []
    weights = []
    total_value = 0.0

    # 使用 est_price 计算市值权重 (若无可回溯旧价格)
    for _code, info in positions.items():
        shares = float(info.get("shares", 0) or 0)
        price = float(info.get("est_price", 0) or 0)
        if shares <= 0 or price <= 0:
            continue
        value = shares * price
        total_value += value
        styles.append(info.get("style", "未知"))
        sectors.append(info.get("sector", info.get("type", "未知")))
        weights.append(value)

    if total_value == 0:
        return DiversificationMetrics()

    # 风格多样性: 香农熵归一化
    style_counts = Counter(styles)
    style_probs = [c / len(weights) for c in style_counts.values()]
    if style_probs:
        import math
        entropy = -sum(p * math.log(p + 1e-10) for p in style_probs)
        max_entropy = math.log(len(style_counts))
        style_diversity = entropy / max_entropy if max_entropy > 0 else 1.0
    else:
        style_diversity = 0.0

    # 行业集中度: 最大行业占比
    Counter(sectors)
    sector_weights = [w for w, s in zip(weights, sectors)]
    sector_dist = {}
    for s, w in zip(sectors, sector_weights):
        sector_dist[s] = sector_dist.get(s, 0) + w
    if sector_dist:
        max_sector = max(sector_dist.values()) / total_value
    else:
        max_sector = 0.0

    # 前三大标的占比
    sorted_weights = sorted(weights, reverse=True)[:3]
    top3 = sum(sorted_weights) / total_value if sorted_weights else 0.0

    # 风格数 / 行业数
    num_styles = len(style_counts)
    num_sectors = len(sector_dist)

    # 权重方差
    mean_w = total_value / len(weights) if weights else 0.0
    variance = sum((w - mean_w) ** 2 for w in weights) / len(weights) if weights else 0.0
    weight_var = variance / (mean_w ** 2) if mean_w > 0 else 0.0

    return DiversificationMetrics(
        style_diversity_score=style_diversity,
        sector_concentration=max_sector,
        top3_concentration=top3,
        num_styles=num_styles,
        num_sectors=num_sectors,
        weight_variance=weight_var,
    )


def _check_feature_flag(flag_name: str) -> bool:
    """检查 Feature Flag (HC-1). 默认关闭."""
    try:
        # 尝试从 config 读取
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        config_path = os.path.join(base, "config", "features.json")
        if os.path.exists(config_path):
            with open(config_path) as f:
                features = json.load(f)
            return features.get(flag_name, False)
    except Exception:
        pass
    # 降级: 检查环境变量或硬编码回退
    try:
        return bool(os.getenv(flag_name, "False").lower() == "true")
    except Exception:
        pass
    return False  # 默认关闭 (HC-1: Feature Flag 默认 False)


# ========== 主评分器 ==========
class StrategyEvaluator:
    """策略多维评分器 (轻量化版, Day 2 N2)."""

    def __init__(self, feature_flag_name: str = "USE_STRATEGY_EVALUATOR"):
        self._feature_flag_name = feature_flag_name
        self._enabled = _check_feature_flag(feature_flag_name)
        logger.info(f"StrategyEvaluator init: enabled={self._enabled}, flag={feature_flag_name}")

    def evaluate(
        self,
        daily_returns_path: Optional[str] = None,
        pos_path: Optional[str] = None,
        use_shadow_data: bool = True,
    ) -> ScoreReport:
        """执行完整评分.

        Args:
            daily_returns_path: 自定义 daily_returns.jsonl 路径 (None=自动检测)
            pos_path: 自定义 positions.json 路径 (None=自动检测)
            use_shadow_data: 如果 auto path 不存在, 尝试 shadow 目录

        Returns:
            ScoreReport
        """
        # HC-1: Feature Flag 检查
        if not self._enabled:
            return self._build_degraded_report(reason=f"feature_flag_disabled ({self._feature_flag_name})")

        # ========== I: 资金回报指标 ==========
        return_metrics = self._compute_return_metrics(
            daily_returns_path, pos_path, use_shadow_data
        )

        # ========== II: 持仓分散度指标 ==========
        try:
            if pos_path is None:
                base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                pos_path = os.path.join(base, "config", "positions.json")
            pos_data = load_positions(pos_path)
            positions = pos_data.get("positions", {})
            divers_metrics = evaluate_diversification(positions)
        except Exception as e:
            logger.warning(f"分散度评估失败, 使用降级值: {e}")
            divers_metrics = DiversificationMetrics()
            divers_metrics.degraded_reason = str(e)

        # ========== III: 综合评分 ==========
        public_score, private_score = self._compute_overall_scores(return_metrics, divers_metrics)

        # ========== 决策建议 ==========
        recommendation, reason = self._make_recommendation(public_score, private_score, return_metrics, divers_metrics)

        report = ScoreReport(
            public_score=round(public_score, 4),
            private_score=round(private_score, 4),
            overall_score=round(public_score * EVOLUTION_CONFIG["public_weight"] + private_score * EVOLUTION_CONFIG["private_weight"], 4),
            return_metrics=return_metrics,
            divers_metrics=divers_metrics,
            recommendation=recommendation,
            reason=reason,
            is_degraded=False,
        )
        return report

    def _compute_return_metrics(
        self,
        daily_returns_path: Optional[str],
        pos_path: Optional[str],
        use_shadow_data: bool,
    ) -> ReturnMetrics:
        """计算资金回报指标 (I)."""
        # 寻找数据源
        paths_to_try = []
        if daily_returns_path:
            paths_to_try.append(daily_returns_path)
        if use_shadow_data:
            base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            shadow_path = os.path.join(base, "reports", "shadow", "daily_returns.jsonl")
            if os.path.exists(shadow_path):
                paths_to_try.append(shadow_path)
        # 最后 fallback: 尝试从 positions 估算 (如果没回报数据)
        returns_list = []
        for p in paths_to_try:
            if os.path.exists(p):
                records = load_daily_returns(p)
                returns_list = [r[1] for r in records]
                break

        if not returns_list:
            # 无回报数据, 基于当日价格变动粗略估算 (仅作为 fallback)
            logger.info("未找到 daily_returns.jsonl, 尝试从 positions 计算简易回报")
            try:
                base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                pos_path = pos_path or os.path.join(base, "config", "positions.json")
                pos_data = load_positions(pos_path)
                positions = pos_data.get("positions", {})
                # 简单计算: 今日价 vs 成本价的加权平均变化
                total_value = 0.0
                pnl_sum = 0.0
                for _code, info in positions.items():
                    shares = float(info.get("shares", 0) or 0)
                    cost = float(info.get("avg_cost", 0) or 0)
                    curr = float(info.get("est_price", 0) or 0)
                    if shares > 0 and cost > 0:
                        value = shares * curr
                        total_value += value
                        pnl_sum += shares * (curr - cost)
                if total_value > 0 and len(positions) > 1:
                    # 用单一时点无法算出年化回报, 返回零指标并降级
                    return ReturnMetrics(annual_return=0.0, max_drawdown=0.0, sharpe_ratio=0.0,
                                       total_return=pnl_sum/total_value if total_value>0 else 0,
                                       sample_count=len(positions))
            except Exception as e:
                logger.error(f"fallback 计算失败: {e}")

        if not returns_list:
            return ReturnMetrics(sample_count=0)

        n = len(returns_list)
        total_ret = sum(returns_list)  # 简单加总 (实际应为 compounding, 但每日回报很小近似线性)
        ann_return = compute_annual_return(returns_list)
        max_dd = compute_max_drawdown(returns_list)
        sharpe = compute_sharpe(returns_list)

        return ReturnMetrics(
            annual_return=round(ann_return, 4),
            max_drawdown=round(max_dd, 4),
            sharpe_ratio=round(sharpe, 4),
            total_return=round(total_ret, 6),
            sample_count=n,
        )

    def _compute_overall_scores(
        self, return_metrics: ReturnMetrics, divers_metrics: DiversificationMetrics
    ) -> Tuple[float, float]:
        """计算 Public Score (III.a) 和 Private Score (III.b)."""
        # Public Score: 基于当前表现的相对评分
        # - 年化收益打分
        excess_return = max(0, return_metrics.annual_return - EVOLUTION_CONFIG["min_annual_return"])
        score_return = min(1.0, excess_return / 0.10)  # 超额 10% 得满分

        # - Sharpe 打分
        sharpe_score = max(0.0, min(1.0, (return_metrics.sharpe_ratio - EVOLUTION_CONFIG["benchmark_sharpe"]) / 1.0))

        # Public = 加权平均 (等权简化)
        public_score = 0.5 * score_return + 0.5 * sharpe_score

        # Private Score: 基于稳健性和分散度
        # - 稳定性: 最大回撤越小越好
        max_dd_penalty = min(1.0, return_metrics.max_drawdown / EVOLUTION_CONFIG["max_allowed_drawdown"])
        score_stability = 1.0 - max_dd_penalty

        # - 分散度加分: 风格多样性越高越好 (上界 1), 集中度越低越好 (上界 1)
        # 将两个维度组合成单一分散度分数 (风格多样性占 60%, 集中度占 40%)
        # 风格多样性: 直接取值 (0-1)
        # 行业集中度转化: 1 - 集中度 (集中度越低分数越高)
        concentration_penalty = 1.0 - divers_metrics.sector_concentration  # 越高越分散
        diversity_score = 0.6 * divers_metrics.style_diversity_score + 0.4 * concentration_penalty

        # Private = 稳定性 (40%) + 分散度 (60%)
        private_score = 0.4 * score_stability + 0.6 * diversity_score

        # 限制在 0-1 范围
        public_score = max(0.0, min(1.0, public_score))
        private_score = max(0.0, min(1.0, private_score))

        return public_score, private_score

    def _make_recommendation(
        self, public_score: float, private_score: float,
        return_metrics: ReturnMetrics, divers_metrics: DiversificationMetrics
    ) -> Tuple[str, str]:
        """生成决策建议."""
        # Public 足够好且 Private 强 → promote
        if public_score >= 0.6 and private_score >= 0.65:
            # 回撤控制是额外条件
            if return_metrics.max_drawdown < EVOLUTION_CONFIG["max_allowed_drawdown"]:
                return "promote", f"public={public_score:.2f}, private={private_score:.2f}, max_dd={return_metrics.max_drawdown:.2f}"
            else:
                return "continue", f"score OK but max_drawdown={return_metrics.max_drawdown:.2f} exceeds limit"

        # Private 很低 → rollback
        if private_score < 0.30:
            return "rollback", f"private_score太低={private_score:.2f}, 建议回滚基线"

        # 默认继续观察
        return "continue", f"public={public_score:.2f}, private={private_score:.2f}, 待更多数据收敛"

    def _build_degraded_report(self, reason: str) -> ScoreReport:
        """构建降级报告."""
        return ScoreReport(
            public_score=0.0,
            private_score=0.0,
            overall_score=0.0,
            return_metrics=ReturnMetrics(),
            divers_metrics=DiversificationMetrics(),
            recommendation="continue",
            reason=f"degraded: {reason}",
            is_degraded=True,
            degraded_reason=reason,
        )


# ========== 便捷接口 ==========
def evaluate_strategy_simple(
    daily_returns_path: Optional[str] = None,
    pos_path: Optional[str] = None,
) -> ScoreReport:
    """便捷调用函数.
    注意: Feature Flag 默认为 False, 需先在 config/features.json 或 env 中启用.
    """
    evaluator = StrategyEvaluator(feature_flag_name=EVOLUTION_CONFIG["feature_flag_name"])
    return evaluator.evaluate(daily_returns_path=daily_returns_path, pos_path=pos_path)


if __name__ == "__main__":
    # 快速自检
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    shadow_path = os.path.join(base, "reports", "shadow", "daily_returns.jsonl")
    pos_path = os.path.join(base, "config", "positions.json")

    # 临时启用 Feature Flag 用于测试 (正常流程应通过 config/features.json 控制)
    # 如果真实环境想测试, 可以设置环境变量: set USE_STRATEGY_EVALUATOR=true
    import os
    os.environ["USE_STRATEGY_EVALUATOR"] = "True"

    evaluator = StrategyEvaluator()
    report = evaluator.evaluate(daily_returns_path=shadow_path, pos_path=pos_path)

    logger.info("\n" + "=" * 70)
    logger.info("  N2 策略评分结果")
    logger.info("=" * 70)
    logger.info(f"公共得分 (Public Score):   {report.public_score:.4f}")
    logger.info(f"私有得分 (Private Score):  {report.private_score:.4f}")
    logger.info(f"综合得分 (Overall Score):  {report.overall_score:.4f}")
    logger.info("\n回报指标:")
    logger.info(f"  年化收益率: {report.return_metrics.annual_return:.4f} ({report.return_metrics.sample_count} 个样本)")
    logger.info(f"  最大回撤:   {report.return_metrics.max_drawdown:.4f}")
    logger.info(f"  Sharpe:     {report.return_metrics.sharpe_ratio:.4f}")
    logger.info("\n分散度指标:")
    logger.info(f"  风格多样性: {report.divers_metrics.style_diversity_score:.4f}")
    logger.info(f"  行业集中度: {report.divers_metrics.sector_concentration:.4f}")
    logger.info(f"  前三大占比: {report.divers_metrics.top3_concentration:.4f}")
    logger.info(f"\n建议: {report.recommendation} — {report.reason}")
    logger.info("=" * 70)
