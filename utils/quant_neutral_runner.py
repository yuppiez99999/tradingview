"""
量化市场中性策略执行器 v1.0
================================

实现 v10.0 投资计划的 quant_neutral_account (70 万资金, 140 万名义敞口):

策略:
    - 多空市场中性 (long_short_market_neutral)
    - 目标 beta: 0.05, 最大净敞口: 0.10
    - 月度调仓, 换手率 150%/月
    - 做多 25 只因子 top 20% 股票
    - 做空 IC 期货对冲, 最多 3 张合约

7 因子模型:
    momentum        20%  过去 20 日收益率
    reversal        15%  过去 5 日反转
    volatility      15%  低波动优先
    liquidity       10%  日均成交额
    earnings_quality 15%  ROE + 现金流
    growth          15%  营收/利润增速
    valuation       10%  PE/PB 分位数

止损规则:
    - 策略月度回撤 > 历史 95% 分位 → 仓位减半
    - 连续 3 月回撤超限 → 暂停 1 个月
    - IC 基差 > 1.5% → 减仓 30%, 转向 ETF + 个股直接组合
    - 策略最大回撤: 8%
    - 夏普目标: 1.2

集成路径:
    daily_workflow.py phase_quant_neutral() → 本模块 run_monthly_rebalance()
    输出: reports/quant_neutral_{date}.json + 指令 json

用法:
    from utils.quant_neutral_runner import QuantNeutralRunner
    runner = QuantNeutralRunner()
    result = runner.run_monthly_rebalance(
        candidate_universe=[...],
        returns_history={...},
        current_holdings=[...],
        current_ic_contracts=2,
        ic_price=5500.0,
    )
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

logger = logging.getLogger("quant_neutral")

try:
    from utils.ic_hedge_calculator import ICHedgeCalculator, ICHedgeResult
    from utils.v10_config_loader import V10ConfigLoader

    _HAS_DEPS = True
except ImportError as e:
    logger.warning(f"量化中性依赖缺失 (降级模式): {e}")
    ICHedgeCalculator = None  # type: ignore[assignment,misc]
    ICHedgeResult = None  # type: ignore[assignment,misc]
    V10ConfigLoader = None  # type: ignore[assignment,misc]
    _HAS_DEPS = False

try:
    import numpy as np  # noqa: F401
    import pandas as pd

    _HAS_PANDAS = True
except ImportError:
    _HAS_PANDAS = False

BASE_DIR = Path(__file__).resolve().parent.parent
REPORTS_DIR = BASE_DIR / "reports"
LOGS_DIR = BASE_DIR / "logs"

# ============================================================
# 默认因子权重 (来自 v10.0 配置)
# ============================================================
DEFAULT_FACTOR_WEIGHTS = {
    "momentum": 0.20,
    "reversal": 0.15,
    "volatility": 0.15,
    "liquidity": 0.10,
    "earnings_quality": 0.15,
    "growth": 0.15,
    "valuation": 0.10,
}

DEFAULT_LONG_COUNT = 25
DEFAULT_TURNOVER_TARGET = 1.5  # 月换手率 150%
DEFAULT_MAX_NET_EXPOSURE = 0.10
DEFAULT_TARGET_BETA = 0.05
# B1.3: 中性策略专属阈值 (策略文档: "策略最大回撤: 8%")
# 从 config/risk_params.yaml::quant_neutral_max_drawdown 读取 (fail-safe 兜底 0.08)
try:
    from utils.risk_params import get_quant_neutral_max_drawdown as _get_qn_max_drawdown
    DEFAULT_MAX_DRAWDOWN = _get_qn_max_drawdown()
except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
    # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
    DEFAULT_MAX_DRAWDOWN = 0.08
DEFAULT_SHARPE_TARGET = 1.2
DEFAULT_BASIS_THRESHOLD = 0.015


@dataclass
class StockFactorScore:
    """单只股票的因子打分结果"""

    code: str
    name: str = ""
    factors: dict[str, float] = field(default_factory=dict)
    composite: float = 0.0  # 综合得分 [-1, 1]
    rank: int = 0  # 排名
    selected: bool = False  # 是否选入多头组合
    beta: float = 1.0  # 个股 beta (用于组合 beta 加权计算)


@dataclass
class QuantNeutralResult:
    """月度调仓结果"""

    trade_date: str = ""
    action: str = ""  # rebalance / skip / pause
    reason: str = ""
    long_count: int = 0
    long_market_value: float = 0.0
    portfolio_beta: float = 0.0
    target_beta: float = DEFAULT_TARGET_BETA
    net_exposure: float = 0.0
    # 多头标的列表
    long_positions: list[dict] = field(default_factory=list)
    # IC 对冲指令
    ic_hedge: dict[str, Any] = field(default_factory=dict)
    # 风控状态
    drawdown_pct: float = 0.0
    drawdown_action: str = ""  # normal / reduce_half / pause
    basis_warning: bool = False
    turnover_achieved: float = 0.0  # 实际换手率
    # 元数据
    factors_used: dict[str, float] = field(default_factory=dict)
    candidate_count: int = 0


class QuantNeutralRunner:
    """量化市场中性策略执行器

    依赖:
        - utils.ic_hedge_calculator.ICHedgeCalculator (IC 对冲量计算)
        - utils.v10_config_loader.V10ConfigLoader (配置加载)
        - utils.factor_model.FactorModel (可选, 复用现有因子库)

    资金配置 (v10.0):
        - 资金: 70 万
        - 总名义敞口: 140 万 (2x 杠杆)
        - 多头市值: ~140 万
        - IC 空头名义: ~140 万
        - 做多股票: 25 只, 单只 ≤ 5.6 万
    """

    def __init__(
        self,
        capital: float = 700_000,
        gross_exposure_ratio: float = 2.0,
        long_count: int = DEFAULT_LONG_COUNT,
        target_beta: float = DEFAULT_TARGET_BETA,
        max_net_exposure: float = DEFAULT_MAX_NET_EXPOSURE,
        factor_weights: dict[str, float] | None = None,
        max_drawdown: float = DEFAULT_MAX_DRAWDOWN,
        sharpe_target: float = DEFAULT_SHARPE_TARGET,
        turnover_target: float = DEFAULT_TURNOVER_TARGET,
    ):
        self.capital = capital
        self.gross_exposure_ratio = gross_exposure_ratio
        self.target_long_value = capital * gross_exposure_ratio  # 140 万
        self.long_count = long_count
        self.target_beta = target_beta
        self.max_net_exposure = max_net_exposure
        self.factor_weights = factor_weights or DEFAULT_FACTOR_WEIGHTS.copy()
        self.max_drawdown = max_drawdown
        self.sharpe_target = sharpe_target
        self.turnover_target = turnover_target

        # IC 对冲计算器
        if ICHedgeCalculator is not None:
            self.ic_calc = ICHedgeCalculator()
        else:
            self.ic_calc = None  # type: ignore[misc]
        # v10.0 配置覆盖 (若可用)
        if V10ConfigLoader is not None:
            try:
                loader = V10ConfigLoader()
                cfg = loader.get_quant_neutral_config()
                if cfg:
                    self.capital = float(cfg.get("capital", self.capital))
                    self.gross_exposure_ratio = float(cfg.get("gross_exposure", self.capital * 2)) / self.capital
                    self.target_long_value = float(cfg.get("gross_exposure", self.target_long_value))
                    self.target_beta = float(cfg.get("target_beta", self.target_beta))
                    self.max_net_exposure = float(cfg.get("max_net_exposure", self.max_net_exposure))
                    self.long_count = int(cfg.get("long_count", self.long_count))
                    self.max_drawdown = float(cfg.get("max_drawdown_pct", self.max_drawdown))
                    self.sharpe_target = float(cfg.get("sharpe_target", self.sharpe_target))
                    self.turnover_target = float(cfg.get("turnover_target_monthly", self.turnover_target))
                    if "factors" in cfg:
                        self.factor_weights = cfg["factors"]
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # P2 模块 fail-safe, 待后续精确化
                logger.warning(f"v10.0 配置加载失败, 使用默认值: {e}")

        logger.info(
            f"[QuantNeutral] 初始化: 资金 ¥{self.capital:,.0f}, "
            f"目标多头 ¥{self.target_long_value:,.0f}, "
            f"目标 beta {self.target_beta}, 做多 {self.long_count} 只"
        )

    # ------------------------------------------------------------
    # 因子打分 (7 因子)
    # ------------------------------------------------------------
    def score_factors(
        self,
        candidate_universe: list[dict],
        returns_history: dict[str, list[float]] | None = None,
    ) -> list[StockFactorScore]:
        """对候选股票池进行 7 因子打分

        Args:
            candidate_universe: 候选股票列表, 每只股票包含:
                {
                    "code": "600000.SH",
                    "name": "浦发银行",
                    "returns_20d": 0.05,         # 过去 20 日收益率 (momentum)
                    "returns_5d": -0.02,         # 过去 5 日收益率 (reversal)
                    "volatility_60d": 0.25,      # 60 日年化波动率
                    "avg_turnover_amount": 50_000_000,  # 日均成交额
                    "roe": 0.15,                # ROE
                    "cashflow_ratio": 0.85,     # 经营现金流/净利润
                    "revenue_growth": 0.20,     # 营收增速
                    "profit_growth": 0.18,      # 利润增速
                    "pe_percentile": 0.45,      # PE 历史分位数
                    "pb_percentile": 0.30,      # PB 历史分位数
                }
            returns_history: 可选, 各股票的历史收益率序列 (用于精确计算)

        Returns:
            打分结果列表, 已按综合得分降序排列
        """
        if not candidate_universe:
            return []

        scores: list[StockFactorScore] = []

        # 1. 提取各因子值, 计算截面排名
        [s.get("code", "") for s in candidate_universe]
        factor_columns = list(self.factor_weights.keys())

        # 构建 DataFrame (若 pandas 可用)
        if _HAS_PANDAS:
            df = pd.DataFrame(candidate_universe)
            df = df.set_index("code") if "code" in df.columns else df
            # 去重: 相同 code 多次出现时取首条 (避免 df.loc[code] 返回 DataFrame)
            if hasattr(df.index, "duplicated") and df.index.duplicated().any():
                df = df[~df.index.duplicated(keep="first")]

            # 计算各因子的截面 z-score (跨股票标准化)
            z_scores = {}
            for factor in factor_columns:
                col = self._map_factor_to_column(factor)
                if col not in df.columns:
                    z_scores[factor] = pd.Series(0.0, index=df.index)
                    continue
                series = df[col].astype(float)
                if series.std() == 0:
                    z_scores[factor] = pd.Series(0.0, index=df.index)
                else:
                    # 反转因子处理: volatility 越低越好, pe/pb 越低越好
                    if factor in ("volatility", "valuation"):
                        series = -series
                    z_scores[factor] = (series - series.mean()) / series.std()

            # 计算综合得分
            for code in df.index:
                row = df.loc[code]
                factors = {}
                composite = 0.0
                for factor, weight in self.factor_weights.items():
                    z_raw = z_scores[factor].get(code, 0.0)
                    # 防止 pandas Series 误判（多索引匹配时 .get 可能返回 Series）
                    if isinstance(z_raw, pd.Series):
                        z_raw = z_raw.iloc[0] if len(z_raw) > 0 else 0.0
                    z = float(z_raw)
                    # 限制 z-score 到 [-3, 3] 防止极端值
                    z = max(-3.0, min(3.0, z))
                    factors[factor] = z
                    composite += z * weight
                # 提取个股 beta (用于组合 beta 计算)
                try:
                    stock_beta = float(row.get("beta", 1.0)) if hasattr(row, "get") else 1.0
                except (TypeError, ValueError):
                    stock_beta = 1.0
                scores.append(
                    StockFactorScore(
                        code=str(code),
                        name=str(row.get("name", "")) if hasattr(row, "get") else "",
                        factors=factors,
                        composite=float(composite),
                        beta=stock_beta,
                    )
                )
        else:
            # 无 pandas, 使用简单排名
            for stock in candidate_universe:
                code = stock.get("code", "")
                factors = {}
                composite = 0.0
                for factor, weight in self.factor_weights.items():
                    raw = self._extract_factor_value(stock, factor)
                    factors[factor] = float(raw)
                    composite += raw * weight
                # 提取个股 beta
                try:
                    stock_beta = float(stock.get("beta", 1.0))
                except (TypeError, ValueError):
                    stock_beta = 1.0
                scores.append(
                    StockFactorScore(
                        code=code,
                        name=stock.get("name", ""),
                        factors=factors,
                        composite=float(composite),
                        beta=stock_beta,
                    )
                )

        # 2. 排序
        scores.sort(key=lambda x: x.composite, reverse=True)

        # 3. 标记排名
        for i, s in enumerate(scores):
            s.rank = i + 1

        # 4. 选前 long_count 只
        top_n = min(self.long_count, len(scores))
        for i, s in enumerate(scores):
            s.selected = i < top_n

        return scores

    def _map_factor_to_column(self, factor: str) -> str:
        """映射因子名到数据列名"""
        mapping = {
            "momentum": "returns_20d",
            "reversal": "returns_5d",
            "volatility": "volatility_60d",
            "liquidity": "avg_turnover_amount",
            "earnings_quality": "roe",
            "growth": "revenue_growth",
            "valuation": "pe_percentile",
        }
        return mapping.get(factor, factor)

    def _extract_factor_value(self, stock: dict, factor: str) -> float:
        """从股票字典中提取因子原始值"""
        col = self._map_factor_to_column(factor)
        val = stock.get(col, 0.0)
        try:
            return float(val) if val is not None else 0.0
        except (TypeError, ValueError):
            return 0.0

    # ------------------------------------------------------------
    # 计算多头组合 beta
    # ------------------------------------------------------------
    def calculate_portfolio_beta(
        self,
        long_positions: list[dict],
        market_returns: list[float] | None = None,
    ) -> float:
        """计算多头组合的加权 beta

        Args:
            long_positions: 多头持仓列表, 每项包含:
                {"code": ..., "weight": ..., "beta": ...}
            market_returns: 市场历史收益率 (可选)

        Returns:
            组合加权 beta
        """
        if not long_positions:
            return 0.0

        # 简化: 使用持仓 beta 的加权平均
        total_weight = sum(p.get("weight", 0) for p in long_positions)
        if total_weight == 0:
            return 1.0  # 默认 1.0

        weighted_beta = sum(p.get("weight", 0) * p.get("beta", 1.0) for p in long_positions)
        return weighted_beta / total_weight  # type: ignore[misc]
    # ------------------------------------------------------------
    # 月度调仓主流程
    # ------------------------------------------------------------
    def run_monthly_rebalance(
        self,
        candidate_universe: list[dict],
        current_holdings: list[dict],
        current_ic_contracts: int = 0,
        ic_price: float = 5500.0,
        basis: float | None = None,
        strategy_drawdown_pct: float = 0.0,
        strategy_history_95pct_drawdown: float = 0.05,
        consecutive_overdrawdown_months: int = 0,
        market_returns: list[float] | None = None,
        trade_date: date | None = None,
    ) -> QuantNeutralResult:
        """月度调仓主流程

        Args:
            candidate_universe: 候选股票池 (含 7 因子数据)
            current_holdings: 当前多头持仓
            current_ic_contracts: 当前 IC 空头合约数
            ic_price: IC 期货价格
            basis: IC 基差 (正=贴水, 负=升水)
            strategy_drawdown_pct: 策略当前回撤百分比
            strategy_history_95pct_drawdown: 策略历史 95% 分位回撤
            consecutive_overdrawdown_months: 连续回撤超限月数
            market_returns: 市场收益率序列 (可选, 用于精确 beta 计算)
            trade_date: 调仓日期

        Returns:
            QuantNeutralResult: 调仓结果
        """
        trade_date = trade_date or date.today()
        result = QuantNeutralResult(
            trade_date=trade_date.isoformat(),
            target_beta=self.target_beta,
            factors_used=self.factor_weights.copy(),
            candidate_count=len(candidate_universe),
        )

        # 1. 风控检查: 连续 3 月回撤超限 → 暂停 1 月
        if consecutive_overdrawdown_months >= 3:
            result.action = "pause"
            result.reason = (
                f"策略连续 {consecutive_overdrawdown_months} 月回撤超限, 暂停 1 个月, 平仓所有多头和 IC 空头"
            )
            result.drawdown_action = "pause"
            result.drawdown_pct = strategy_drawdown_pct
            logger.warning(f"[QuantNeutral] {result.reason}")
            return self._generate_pause_order(result, current_holdings, current_ic_contracts, ic_price, trade_date)

        # 2. 风控检查: 单月回撤超 95% 分位 → 仓位减半
        if strategy_drawdown_pct > strategy_history_95pct_drawdown:
            result.drawdown_action = "reduce_half"
            result.drawdown_pct = strategy_drawdown_pct
            logger.warning(
                f"[QuantNeutral] 月度回撤 {strategy_drawdown_pct * 100:.2f}% "
                f"> 95% 分位 {strategy_history_95pct_drawdown * 100:.2f}%, 仓位减半"
            )
            # 仓位减半: 多头目标市值减半
            self.target_long_value_adjusted = self.target_long_value * 0.5
        else:
            result.drawdown_action = "normal"
            result.drawdown_pct = strategy_drawdown_pct
            self.target_long_value_adjusted = self.target_long_value

        # 3. 极端回撤检查: 超过策略最大回撤 8% → 暂停
        if strategy_drawdown_pct > self.max_drawdown:
            result.action = "pause"
            result.reason = (
                f"策略回撤 {strategy_drawdown_pct * 100:.2f}% > 最大回撤 {self.max_drawdown * 100:.0f}%, "
                "强制暂停, 平仓所有头寸"
            )
            return self._generate_pause_order(result, current_holdings, current_ic_contracts, ic_price, trade_date)

        # 4. 基差警告
        if basis is not None and basis > DEFAULT_BASIS_THRESHOLD:
            result.basis_warning = True
            logger.warning(
                f"[QuantNeutral] IC 基差贴水 {basis * 100:.2f}%, 减少中性策略仓位 30%, 转向 ETF + 个股直接组合"
            )
            # 减少 30% 多头目标
            self.target_long_value_adjusted *= 0.7

        # 5. 因子打分, 选股
        scores = self.score_factors(candidate_universe)
        selected = [s for s in scores if s.selected]

        if not selected:
            result.action = "skip"
            result.reason = "候选股票池为空或因子打分失败"
            return result

        # 6. 构建多头组合 (等权或按综合得分加权)
        long_positions = self._build_long_positions(selected, self.target_long_value_adjusted)
        result.long_positions = long_positions
        result.long_count = len(long_positions)
        result.long_market_value = sum(p["amount"] for p in long_positions)

        # 7. 计算组合 beta
        portfolio_beta = self.calculate_portfolio_beta(long_positions, market_returns)
        result.portfolio_beta = portfolio_beta

        # 8. IC 对冲量计算
        if self.ic_calc is not None:
            ic_result = self.ic_calc.calculate(
                long_market_value=result.long_market_value,
                portfolio_beta=portfolio_beta,
                target_beta=self.target_beta,
                ic_price=ic_price,
                available_margin=self.capital * 0.8,  # 80% 资金可用作 IC 保证金 (保留 20% 应急)
                basis=basis,
            )
            result.ic_hedge = self.ic_calc.build_hedge_order(ic_result, trade_date)
            result.ic_hedge["summary"] = self.ic_calc.summary(ic_result)
            result.net_exposure = ic_result.net_exposure
        else:
            # 降级模式: 简单计算
            contracts = max(
                1, min(3, int(result.long_market_value * (portfolio_beta - self.target_beta) / (200 * ic_price)))
            )
            result.ic_hedge = {
                "action": "open_short",
                "symbol": "IC",
                "contracts": contracts,
                "price": ic_price,
                "fallback_mode": True,
            }
            result.net_exposure = portfolio_beta - contracts * 200 * ic_price / result.long_market_value

        # 9. 调仓指令: 当前 vs 目标
        rebalance_order = self._build_rebalance_orders(
            current_holdings, long_positions, current_ic_contracts, result.ic_hedge, trade_date
        )
        result.ic_hedge["rebalance"] = rebalance_order["ic_action"]
        result.action = "rebalance"
        result.turnover_achieved = rebalance_order["turnover"]

        # 10. 持久化报告
        self._save_report(result, trade_date)

        logger.info(
            f"[QuantNeutral] 月度调仓完成: 做多 {result.long_count} 只, "
            f"市值 ¥{result.long_market_value:,.0f}, beta {portfolio_beta:.3f}, "
            f"IC 合约 {result.ic_hedge.get('contracts', 0)} 张, "
            f"净敞口 {result.net_exposure:.3f}, 换手率 {result.turnover_achieved:.1%}"
        )

        return result

    def _build_long_positions(
        self,
        selected: list[StockFactorScore],
        target_value: float,
    ) -> list[dict]:
        """构建多头持仓列表 (按综合得分加权)"""
        if not selected:
            return []

        # 按 composite 得分加权 (确保非负)
        min_score = min(s.composite for s in selected)
        adjusted_scores = [max(0.01, s.composite - min_score + 0.01) for s in selected]
        total_score = sum(adjusted_scores)

        positions = []
        for stock, score in zip(selected, adjusted_scores):
            weight = score / total_score
            amount = target_value * weight
            positions.append(
                {
                    "code": stock.code,
                    "name": stock.name,
                    "weight": float(weight),
                    "amount": float(amount),
                    "composite_score": float(stock.composite),
                    "rank": stock.rank,
                    "factors": stock.factors,
                    "beta": float(getattr(stock, "beta", 1.0)),
                }
            )

        return positions

    def _build_rebalance_orders(
        self,
        current_holdings: list[dict],
        target_positions: list[dict],
        current_ic_contracts: int,
        ic_hedge: dict,
        trade_date: date,
    ) -> dict[str, Any]:
        """生成调仓指令 (买入新标的, 卖出剔除标的, 调整 IC 合约)"""
        current_codes = {h.get("code") for h in current_holdings}
        target_codes = {p.get("code") for p in target_positions}

        to_buy = [p for p in target_positions if p["code"] not in current_codes]
        to_sell = [h for h in current_holdings if h.get("code") not in target_codes]
        to_adjust = []
        for target in target_positions:
            for current in current_holdings:
                if target["code"] == current.get("code"):
                    delta = target["amount"] - current.get("amount", 0)
                    if abs(delta) > 1000:
                        to_adjust.append(
                            {
                                "code": target["code"],
                                "name": target.get("name", ""),
                                "current_amount": current.get("amount", 0),
                                "target_amount": target["amount"],
                                "delta": delta,
                                "action": "buy" if delta > 0 else "sell",
                            }
                        )

        # IC 调仓
        target_ic = ic_hedge.get("contracts", 0)
        ic_delta = target_ic - current_ic_contracts
        if ic_delta > 0:
            ic_action = {"action": "add_short", "delta": ic_delta, "from": current_ic_contracts, "to": target_ic}
        elif ic_delta < 0:
            ic_action = {
                "action": "reduce_short",
                "delta": abs(ic_delta),
                "from": current_ic_contracts,
                "to": target_ic,
            }
        else:
            ic_action = {"action": "hold", "from": current_ic_contracts, "to": target_ic}

        # 换手率计算
        total_traded = sum(p["amount"] for p in to_buy) + sum(h.get("amount", 0) for h in to_sell)
        total_traded += sum(abs(a["delta"]) for a in to_adjust)
        portfolio_value = sum(p["amount"] for p in target_positions) or 1
        turnover = total_traded / portfolio_value

        return {
            "trade_date": trade_date.isoformat(),
            "buy_orders": to_buy,
            "sell_orders": to_sell,
            "adjust_orders": to_adjust,
            "ic_action": ic_action,
            "turnover": float(turnover),
        }

    def _generate_pause_order(
        self,
        result: QuantNeutralResult,
        current_holdings: list[dict],
        current_ic_contracts: int,
        ic_price: float,
        trade_date: date,
    ) -> QuantNeutralResult:
        """生成暂停策略的清仓指令"""
        result.long_positions = []
        for h in current_holdings:
            result.long_positions.append(
                {
                    "code": h.get("code"),
                    "name": h.get("name", ""),
                    "weight": 0,
                    "amount": 0,
                    "action": "sell_all",
                    "current_amount": h.get("amount", 0),
                }
            )
        result.ic_hedge = {
            "action": "close_all_short",
            "symbol": "IC",
            "current_contracts": current_ic_contracts,
            "target_contracts": 0,
            "price": ic_price,
            "trade_date": trade_date.isoformat(),
        }
        return result

    def _save_report(self, result: QuantNeutralResult, trade_date: date):
        """持久化月度调仓报告"""
        try:
            REPORTS_DIR.mkdir(parents=True, exist_ok=True)
            report_path = REPORTS_DIR / f"quant_neutral_{trade_date.isoformat()}.json"
            report_data = asdict(result)
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump(report_data, f, ensure_ascii=False, indent=2, default=str)
            logger.info(f"[QuantNeutral] 报告已保存: {report_path}")
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # P2 模块 fail-safe, 待后续精确化
            logger.error(f"[QuantNeutral] 报告保存失败: {e}")

    # ------------------------------------------------------------
    # 摘要
    # ------------------------------------------------------------
    def summary(self, result: QuantNeutralResult) -> str:
        """生成调仓结果摘要"""
        lines = [
            "=" * 60,
            f"量化市场中性策略月度调仓报告 ({result.trade_date})",
            "=" * 60,
            f"动作: {result.action}",
            f"原因: {result.reason or '正常调仓'}",
            "",
            f"候选股票池: {result.candidate_count} 只",
            f"做多数量: {result.long_count} 只",
            f"多头市值: ¥{result.long_market_value:,.0f}",
            f"组合 Beta: {result.portfolio_beta:.3f}",
            f"目标 Beta: {result.target_beta:.3f}",
            f"净敞口: {result.net_exposure:.3f} (上限 {self.max_net_exposure:.2f})",
            "",
            "IC 对冲指令:",
            f"  动作: {result.ic_hedge.get('action', 'N/A')}",
            f"  合约数: {result.ic_hedge.get('contracts', 0)} 张",
            f"  价格: {result.ic_hedge.get('price', 0):.1f} 点",
            "",
            f"实际换手率: {result.turnover_achieved:.1%} (目标 {self.turnover_target:.0%})",
            f"策略回撤: {result.drawdown_pct * 100:.2f}%",
            f"风控动作: {result.drawdown_action}",
        ]

        if result.basis_warning:
            lines.append("")
            lines.append("[警告] IC 基差贴水过高, 已减仓 30%")

        if result.long_positions:
            lines.append("")
            lines.append("前 5 大多头持仓:")
            for p in result.long_positions[:5]:
                lines.append(
                    f"  {p['code']} {p.get('name', '')} "
                    f"权重 {p['weight']:.2%} 金额 ¥{p['amount']:,.0f} "
                    f"得分 {p.get('composite_score', 0):.3f}"
                )

        lines.append("=" * 60)
        return "\n".join(lines)


# ============================================================
# CLI 入口
# ============================================================
if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(description="量化市场中性策略执行器")
    parser.add_argument("--demo", action="store_true", help="使用示例数据演示")
    parser.add_argument("--ic-price", type=float, default=5500.0, help="IC 期货价格")
    parser.add_argument("--basis", type=float, default=None, help="IC 基差")
    args = parser.parse_args()

    runner = QuantNeutralRunner()

    if args.demo:
        # 生成 50 只候选股票的示例数据
        import random

        random.seed(42)
        universe = []
        stock_names = [
            ("600000.SH", "浦发银行"),
            ("600036.SH", "招商银行"),
            ("601318.SH", "中国平安"),
            ("601398.SH", "工商银行"),
            ("600276.SH", "恒瑞医药"),
            ("600900.SH", "长江电力"),
            ("601088.SH", "中国神华"),
            ("600030.SH", "中信证券"),
            ("600519.SH", "贵州茅台"),
            ("000858.SZ", "五粮液"),
        ] * 5  # 50 只
        for _i, (code, name) in enumerate(stock_names):
            universe.append(
                {
                    "code": code,
                    "name": name,
                    "returns_20d": random.uniform(-0.1, 0.15),
                    "returns_5d": random.uniform(-0.05, 0.05),
                    "volatility_60d": random.uniform(0.15, 0.45),
                    "avg_turnover_amount": random.uniform(10_000_000, 200_000_000),
                    "roe": random.uniform(0.05, 0.25),
                    "cashflow_ratio": random.uniform(0.5, 1.2),
                    "revenue_growth": random.uniform(-0.1, 0.4),
                    "profit_growth": random.uniform(-0.15, 0.5),
                    "pe_percentile": random.uniform(0.1, 0.9),
                    "pb_percentile": random.uniform(0.1, 0.9),
                    "beta": random.uniform(0.6, 1.3),
                }
            )

        result = runner.run_monthly_rebalance(
            candidate_universe=universe,
            current_holdings=[],
            current_ic_contracts=0,
            ic_price=args.ic_price,
            basis=args.basis,
        )

        logger.info(runner.summary(result))
    else:
        logger.info("量化市场中性策略执行器已初始化")
        logger.info(f"资金: ¥{runner.capital:,.0f}")
        logger.info(f"目标多头: ¥{runner.target_long_value:,.0f}")
        logger.info(f"目标 beta: {runner.target_beta}")
        logger.info(f"做多股票数: {runner.long_count}")
        logger.info(f"因子权重: {runner.factor_weights}")
        logger.info("\n使用 --demo 运行示例调仓")
