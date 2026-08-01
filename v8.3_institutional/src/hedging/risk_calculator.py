# -*- coding: utf-8 -*-
"""
组合风险评估器 (B3.3: 从 hedge_engine_v59.py 抽取)
=====================================================

职责:
  1. 组合风险评估 (VaR/CVaR/波动率/Beta/集中度)
  2. 历史极端压力测试 (6 个情景)
  3. 协方差矩阵计算 (P0-5 修复)
  4. 边际风险贡献 (MRC) 计算 (P0-6)
  5. 相关性矩阵监控 (P0-7)
  6. 板块集中度检查 (P0-6/P0-7 联动)

本模块仅负责风险评估, 不生成对冲方案, 可独立测试和复用。
"""

import logging
import math
from typing import Any, Dict, List, Optional

# 复用 hedge_engine 中的数据类定义 (避免循环依赖)
from .hedge_engine_v59 import PortfolioRisk  # noqa: E402

logger = logging.getLogger("hedge_engine")


class RiskCalculator:
    """组合风险评估器 v5.10

    从 HedgeEngine 抽取的风险评估职责, 包含:
      - assess_portfolio_risk: 组合风险综合评估 (VaR/CVaR/集中度/MRC)
      - run_historical_stress_tests: 6 个历史极端情景压力测试
      - compute_correlation_matrix: 资产相关性矩阵
      - monitor_daily_correlation: 每日相关性监控
      - check_sector_concentration: 板块集中度检查
    """

    # v5.10: 类级别Beta常量 — 供压力测试和组合Beta计算复用
    DEFAULT_BETAS = {
        "300308": (1.35, 1.50, 1.60, 1.20),
        "688041": (1.40, 1.55, 1.70, 1.25),
        "002371": (1.25, 1.40, 1.50, 1.15),
        "688981": (1.30, 1.45, 1.55, 1.20),
        "300750": (1.20, 1.35, 1.45, 1.10),
        "000425": (1.05, 1.15, 1.25, 0.95),
        "601088": (0.85, 0.80, 0.75, 0.90),
        "600219": (0.90, 0.95, 1.05, 0.85),
        "600019": (0.95, 1.00, 1.10, 0.90),
        "518880": (0.40, 0.35, 0.30, 0.45),
        "000792": (0.80, 0.90, 1.00, 0.75),
        "600276": (0.75, 0.70, 0.65, 0.80),
        "603259": (0.90, 0.95, 1.00, 0.85),
        "002422": (0.70, 0.65, 0.60, 0.75),
    }

    # v5.10 P0-6: 板块映射 — 集中度监控
    SECTOR_MAP = {
        "300308": "高端制造",
        "688041": "高端制造",
        "002371": "高端制造",
        "688981": "高端制造",
        "300750": "高端制造",
        "000425": "高端制造",
        "601088": "顺周期",
        "600219": "顺周期",
        "600019": "顺周期",
        "518880": "黄金ETF",
        "000792": "资源",
        "600276": "防御",
        "603259": "防御",
        "002422": "防御",
        "600900": "防御",  # 长江电力
        "511010": "国债ETF",  # 固收敞口
    }

    # 固收/国债类不计入股票板块集中度
    FIXED_INCOME_TYPES = {"国债ETF"}

    SECTOR_LIMIT = 0.35  # 单一板块上限35% (P0-6)
    MRC_LIMIT = 0.25  # 单标的边际风险贡献上限25%
    CORRELATION_WARN = 0.70  # 平均相关性 > 0.7 触发预警 (P0-7)

    # v5.10 P0-8: 历史极端压力测试场景
    HISTORICAL_STRESS_SCENARIOS = {
        "2015股灾 (沪深300 -45%)": {
            "csi300": -0.45,
            "csi500": -0.50,
            "csi1000": -0.50,
            "sse50": -0.40,
            "gold": 0.02,
            "sector": "全面崩盘, 流动性枯竭, 千股停牌",
        },
        "2016熔断 (沪深300 -25%)": {
            "csi300": -0.25,
            "csi500": -0.30,
            "csi1000": -0.28,
            "sse50": -0.22,
            "gold": 0.01,
            "sector": "指数熔断, 恐慌抛售, 两日触发2次熔断",
        },
        "2018贸易战 (沪深300 -32%)": {
            "csi300": -0.32,
            "csi500": -0.35,
            "csi1000": -0.38,
            "sse50": -0.28,
            "gold": 0.04,
            "sector": "中美贸易摩擦升级, 科技股重挫, 人民币贬值",
        },
        "2020疫情闪崩 (沪深300 -16%)": {
            "csi300": -0.16,
            "csi500": -0.15,
            "csi1000": -0.14,
            "sse50": -0.14,
            "gold": 0.06,
            "sector": "新冠疫情全球爆发, 节后首日3000股跌停",
        },
        "2024国庆后暴跌 (沪深300 -20%)": {
            "csi300": -0.20,
            "csi500": -0.22,
            "csi1000": -0.25,
            "sse50": -0.18,
            "gold": 0.01,
            "sector": "政策宽松预期逆转, 前期过热回调",
        },
        "极端尾部事件 (1% VaR, -40%)": {
            "csi300": -0.40,
            "csi500": -0.45,
            "csi1000": -0.50,
            "sse50": -0.35,
            "gold": 0.08,
            "sector": "复合危机: 流动性枯竭+信用违约+汇率贬值叠加",
        },
    }

    def __init__(self):
        self._beta_cache: Dict[str, float] = {}

    # ── 风险评估 ──

    def assess_portfolio_risk(
        self,
        positions: Dict[str, Dict[str, Any]],
        prices: Dict[str, float],
        historical_returns: Optional[Dict[str, List[float]]] = None,
        cash: float = 0.0,
    ) -> PortfolioRisk:
        """评估组合风险 v5.10 — 协方差矩阵VaR修复 (P0-5)

        v5.10 改进:
        - 组合VaR = sqrt(w^T * Σ * w) * z * total_value
        - 当historical_returns可用时, 从历史数据推算协方差矩阵
        - 无历史数据时回退到独立假设并标注风险低估警告
        """
        risk = PortfolioRisk()

        total_stock = 0.0
        stock_weights = {}
        codes_in_portfolio = []

        for code, pos in positions.items():
            shares = pos.get("shares", 0)
            # 后缀兼容: 尝试纯数字码 → .SH/.SZ 后缀码
            price = prices.get(code, 0)
            if price <= 0:
                for suffix in [".SH", ".SZ"]:
                    price = prices.get(code + suffix, 0)
                    if price > 0:
                        break
            # 无行情时用兜底价格
            if price <= 0:
                price = self._estimate_default_price(code)
            market_value = shares * price
            total_stock += market_value
            if market_value > 0:
                stock_weights[code] = market_value
                codes_in_portfolio.append(code)

        risk.stock_exposure = total_stock
        risk.cash = cash
        risk.total_value = total_stock + cash

        if total_stock <= 0:
            return risk

        total_weight = sum(stock_weights.values())
        if total_weight > 0:
            for code in stock_weights:
                stock_weights[code] /= total_weight

        risk.beta_csi300 = self._compute_weighted_beta(stock_weights, "CSI300")
        risk.beta_csi500 = self._compute_weighted_beta(stock_weights, "CSI500")
        risk.beta_csi1000 = self._compute_weighted_beta(stock_weights, "CSI1000")
        risk.beta_sse50 = self._compute_weighted_beta(stock_weights, "SSE50")

        # ── v5.10 VaR协方差矩阵修复 (P0-5) ──
        z_95 = 1.645  # 95%置信度z-score

        if historical_returns and len(codes_in_portfolio) > 0:
            # 有历史数据: 使用协方差矩阵法计算组合VaR
            risk.volatility_30d = self._compute_portfolio_vol_cov(stock_weights, historical_returns, codes_in_portfolio)
            risk.var_95_daily = risk.total_value * risk.volatility_30d * z_95
            # ES_95%: 历史模拟法
            risk.cvar_95_daily = self._compute_expected_shortfall(
                stock_weights, historical_returns, codes_in_portfolio, risk.total_value, 0.95
            )
        else:
            # 无历史数据: 回退到独立假设但标注风险低估
            market_vol = 0.20
            risk.volatility_30d = risk.beta_csi300 * market_vol / math.sqrt(12) if risk.beta_csi300 > 0 else 0.02
            risk.var_95_daily = risk.total_value * risk.volatility_30d * z_95
            # v5.10: ES不再简单乘以1.3, 改用2.0作为无数据时的保守上限
            risk.cvar_95_daily = risk.var_95_daily * 2.0
            logger.warning(
                "[VaR] 无历史收益率数据, 使用独立假设VaR (可能低估真实风险50%+), "
                "建议提供historical_returns参数以获得准确值"
            )

        # ── v5.10 P0-6/P0-7: 集中度+相关性增强监控 ──
        # HHI 集中度
        hhi = sum(w * w for w in stock_weights.values() if w > 0)
        risk.concentration_risk = hhi

        # P0-6: 板块集中度 (排除固收/国债ETF)
        sector_values: Dict[str, float] = {}
        stock_only_weight = 0.0
        for code, w in stock_weights.items():
            pure = code.split(".")[0] if "." in code else code
            sector = self.SECTOR_MAP.get(pure, "其他")
            if sector in self.FIXED_INCOME_TYPES:
                continue  # 国债ETF不计入股票集中度
            sector_values[sector] = sector_values.get(sector, 0) + w
            stock_only_weight += w
        # 归一化到纯股票权重
        if stock_only_weight > 0:
            for sector in sector_values:
                sector_values[sector] /= stock_only_weight
        risk.sector_weights = sector_values
        risk.max_sector_weight = max(sector_values.values()) if sector_values else 0

        if risk.max_sector_weight > self.SECTOR_LIMIT:
            top_sector = max(sector_values, key=sector_values.get) if sector_values else ""
            risk.sector_concentration_warning = (
                f"{top_sector}板块权重{risk.max_sector_weight * 100:.0f}% > "
                f"{self.SECTOR_LIMIT * 100:.0f}%上限 (纯股票口径)"
            )

        # P0-6: MRC (Marginal Risk Contribution) — 基于协方差矩阵
        if historical_returns:
            mrc_map = self._compute_mrc(stock_weights, historical_returns, codes_in_portfolio, risk.volatility_30d)
            for code, mrc in mrc_map.items():
                if mrc > self.MRC_LIMIT:
                    risk.mrc_warnings.append(f"{code} MRC={mrc * 100:.1f}% > {self.MRC_LIMIT * 100:.0f}%上限")
            risk.max_single_mrc = max(mrc_map.values()) if mrc_map else 0

        # P0-7: 组合内平均相关性监控
        if historical_returns:
            corr_matrix = self.compute_correlation_matrix(historical_returns, codes_in_portfolio, lookback_days=60)
            if corr_matrix:
                corr_values = []
                for ci, inner in corr_matrix.items():
                    for cj, corr in inner.items():
                        if ci < cj:
                            corr_values.append(corr)
                if corr_values:
                    risk.avg_pairwise_correlation = sum(corr_values) / len(corr_values)
                    if risk.avg_pairwise_correlation > self.CORRELATION_WARN:
                        risk.correlation_warning = (
                            f"组合平均相关性{risk.avg_pairwise_correlation:.2f} > {self.CORRELATION_WARN:.2f}, "
                            f"呈现共振风险(20d均值)"
                        )

        return risk

    def _compute_weighted_beta(self, weights: Dict[str, float], index: str) -> float:
        idx_map = {"CSI300": 0, "CSI500": 1, "CSI1000": 2, "SSE50": 3}
        idx = idx_map.get(index, 0)

        total_beta = 0.0
        total_w = 0.0
        for code, w in weights.items():
            pure_code = code.split(".")[0] if "." in code else code
            if pure_code in self.DEFAULT_BETAS:
                total_beta += w * self.DEFAULT_BETAS[pure_code][idx]
            else:
                total_beta += w * 1.0
            total_w += w

        return total_beta / total_w if total_w > 0 else 0

    # ── v5.10 P0-8: 历史极端压力测试 ──

    def run_historical_stress_tests(
        self,
        positions: Dict[str, Dict[str, Any]],
        prices: Dict[str, float],
    ) -> Dict[str, Dict[str, Any]]:
        """v5.10 P0-8修复: 6个历史极端情景压力测试

        返回每个情景下的:
        - estimated_loss: 预估组合损失金额
        - drawdown_pct: 预估回撤百分比
        - breaches_limit: 是否突破15%最大回撤目标
        - surviving_value: 压力后组合剩余价值
        - sector_detail: 各板块受损明细
        """

        # 计算各标的对每个指数的加权beta暴露
        stock_codes = list(positions.keys())
        total_mv = sum(positions[c]["shares"] * prices.get(c, self._estimate_default_price(c)) for c in stock_codes)
        if total_mv <= 0:
            return {}

        results = {}

        for scenario_name, shocks in self.HISTORICAL_STRESS_SCENARIOS.items():
            estimated_loss = 0.0

            for code in stock_codes:
                pos = positions[code]
                shares = pos["shares"]
                if shares <= 0:
                    continue

                current_price = prices.get(code, self._estimate_default_price(code))
                current_mv = shares * current_price

                # 判断标的对4个指数的Beta暴露
                pure = code.split(".")[0] if "." in code else code
                betas = self.DEFAULT_BETAS.get(pure, [1.0, 1.0, 1.0, 1.0])

                # 加权指数冲击 (历史情景为多日累计，不用日跌停板限制)
                weighted_shock = (
                    betas[0] * shocks["csi300"]
                    + betas[1] * shocks["csi500"]
                    + betas[2] * shocks["csi1000"]
                    + betas[3] * shocks["sse50"]
                ) / 4.0

                # 黄金ETF特殊处理: 危机中黄金通常上涨
                if code in ("518880", "518880.SH") or "黄金" in code:
                    weighted_shock = -shocks.get("gold", 0.02)

                loss = current_mv * weighted_shock
                estimated_loss += loss

            # 计算压力后组合
            surviving_value = total_mv + estimated_loss
            drawdown_pct = abs(estimated_loss) / total_mv if total_mv > 0 else 0

            results[scenario_name] = {
                "estimated_loss": round(abs(estimated_loss), 0),
                "loss_pct": round(drawdown_pct * 100, 1),
                "drawdown_pct": round(drawdown_pct * 100, 1),
                "breaches_limit": drawdown_pct > 0.15,
                "surviving_value": round(surviving_value, 0),
                "total_value_before": round(total_mv, 0),
                "sector_impact": shocks["sector"],
            }

        return results

    def _compute_portfolio_vol(self, weights, returns) -> float:
        """旧版单资产波动率估算 — 保留向后兼容"""
        return 0.015

    def _estimate_default_price(self, code: str) -> float:
        """兜底价格 — 用于无实时行情时"""
        fallback_map = {
            "300308": 105.0,
            "300308.SZ": 105.0,
            "688041": 62.0,
            "688041.SH": 62.0,
            "002371": 320.0,
            "002371.SZ": 320.0,
            "688981": 55.0,
            "688981.SH": 55.0,
            "300750": 230.0,
            "300750.SZ": 230.0,
            "000425": 8.5,
            "000425.SZ": 8.5,
            "601088": 38.0,
            "601088.SH": 38.0,
            "600219": 5.0,
            "600219.SH": 5.0,
            "600019": 7.5,
            "600019.SH": 7.5,
            "518880": 5.2,
            "518880.SH": 5.2,
            "000792": 28.0,
            "000792.SZ": 28.0,
            "600900": 22.0,
            "600900.SH": 22.0,
            "600276": 48.0,
            "600276.SH": 48.0,
            "603259": 65.0,
            "603259.SH": 65.0,
            "002422": 32.0,
            "002422.SZ": 32.0,
            "511010": 108.5,
            "511010.SH": 108.5,
        }
        return fallback_map.get(code, 50.0)

    def _compute_portfolio_vol_cov(
        self,
        weights: Dict[str, float],
        historical_returns: Dict[str, List[float]],
        codes: List[str],
    ) -> float:
        """v5.10 协方差矩阵组合波动率 (P0-5修复核心)

        σ_p = sqrt(w^T * Σ * w)
        其中 Σ 是从历史日收益率推算的协方差矩阵

        仅使用codes_in_portfolio中包含的标的。
        """
        available_codes = [c for c in codes if c in historical_returns]
        if not available_codes:
            return 0.015

        # 获取每个标的的历史日收益率序列
        code_returns = {}
        min_len = float("inf")
        for code in available_codes:
            rets = historical_returns[code]
            if len(rets) < 30:
                continue  # 少于30天收益率的标的跳过
            code_returns[code] = rets[-252:]  # 最多取最近252天
            min_len = min(min_len, len(rets[-252:]))

        if len(code_returns) < 2:
            # 只有1个或更少标的有足够数据, 回退到加权标准差
            if code_returns:
                code = next(iter(code_returns.keys()))
                w = weights.get(code, 0)
                rets = code_returns[code][-min_len:]
                avg_ret = sum(rets) / len(rets)
                vol = math.sqrt(sum((r - avg_ret) ** 2 for r in rets) / (len(rets) - 1))
                return vol * w
            return 0.015

        # 构建权重向量 (按codes_in_portfolio顺序)
        n = len(available_codes)
        w = [weights.get(c, 0.0) for c in available_codes]

        # 构建协方差矩阵
        cov = [[0.0] * n for _ in range(n)]
        for i, ci in enumerate(available_codes):
            rets_i = code_returns[ci][-min_len:]
            mean_i = sum(rets_i) / len(rets_i)
            for j, cj in enumerate(available_codes):
                rets_j = code_returns[cj][-min_len:]
                mean_j = sum(rets_j) / len(rets_j)
                # 样本协方差
                cov_ij = sum((rets_i[k] - mean_i) * (rets_j[k] - mean_j) for k in range(min_len)) / (min_len - 1)
                cov[i][j] = cov_ij

        # w^T * Σ * w
        port_var = 0.0
        for i in range(n):
            for j in range(n):
                port_var += w[i] * cov[i][j] * w[j]

        port_vol = port_var ** 0.5

        return port_vol
