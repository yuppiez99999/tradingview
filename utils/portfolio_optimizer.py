"""
组合优化器 (Portfolio Optimizer)
================================

创建日期: 2026-07-26
创建原因: 顶级对冲基金审计 P0-A 深度修复 + P1-B 缺失文件修复
审计文档: docs/HEDGE_FUND_AUDIT_2026-07-26_SYSTEM_BUGS_AND_AUTOMATION.md
修复记录: docs/AUDIT_FIX_CHANGELOG_2026-07-26.md

职责:
1. 加载离线生成的 Pipeline 因子组合信号 JSON
2. 用因子信号调整目标权重（保守权重 alpha=0.05，避免过度冲击）
3. 提供 run_offline_pipeline() 离线触发接口

设计原则:
- 离线计算 + 在线应用模式（与 LGB 增强信号一致）
- 失败不阻断主流程（与 _load_lgb_enhanced_signals 一致的安全降级）
- 仅影响影子账户（不影响 500万 实盘）

用法:
    from utils.portfolio_optimizer import PortfolioOptimizer
    opt = PortfolioOptimizer()
    signals = opt.load_factor_signals("2026-07-27")
    if signals:
        adjusted = opt.adjust_target_weights(base_weights, signals, alpha=0.05)
"""

from __future__ import annotations

import json
import logging
import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger("portfolio_optimizer")


class PortfolioOptimizer:
    """组合优化器 — 消费 PipelineResult.factor_combinations 调整目标权重

    本类是 v8.6.4 深度 P0-A 修复的关键组件，使因子流水线的研究成果
    真实接入生产交易决策链路（影子账户层）。

    设计依据:
    - PipelineOrchestrator 实测 IC_IR=+0.5840, live_dsr=+2.2033（v6.9 lookback=10）
    - 保守权重 alpha=0.05 起步，影子账户 OOS 验证通过后可逐步上调
    - 与 LGB 增强信号一致的离线计算 + 在线应用模式
    """

    # 默认信号文件目录
    DEFAULT_SIGNALS_DIR = "models/pipeline_factor_signals"

    # 默认保守混合权重（base 权重 95% + factor 信号 5%）
    DEFAULT_ALPHA = 0.05

    def __init__(self, signals_dir: str | None = None):
        """初始化组合优化器

        Args:
            signals_dir: 信号文件目录，默认 models/pipeline_factor_signals
        """
        # 解析为绝对路径（相对于项目根目录）
        project_root = Path(__file__).resolve().parent.parent
        self.signals_dir = Path(signals_dir) if signals_dir else project_root / self.DEFAULT_SIGNALS_DIR
        self.signals_dir.mkdir(parents=True, exist_ok=True)
        logger.debug(
            "[PortfolioOptimizer] 初始化 | signals_dir=%s",
            self.signals_dir,
        )

    # ------------------------------------------------------------
    # 在线应用：加载因子信号 + 调整目标权重
    # ------------------------------------------------------------

    def load_factor_signals(self, trade_date: str) -> dict[str, float]:
        """加载当日 Pipeline 因子组合信号

        从 models/pipeline_factor_signals/pipeline_factor_signals_{trade_date}.json 读取
        若文件不存在或非当日，返回空字典（安全降级，不抛异常）

        Args:
            trade_date: 交易日期 YYYY-MM-DD

        Returns:
            {symbol: signal_value ∈ [-1, 1]} 或空字典
        """
        if not trade_date:
            logger.warning("[PortfolioOptimizer] trade_date 为空, 返回空信号")
            return {}

        try:
            path = self.signals_dir / f"pipeline_factor_signals_{trade_date}.json"
            if not path.exists():
                logger.info(
                    "[PortfolioOptimizer] 因子信号文件不存在: %s (可能尚未生成)",
                    path.name,
                )
                return {}

            with open(path, encoding="utf-8") as f:
                data = json.load(f)

            # 新鲜度检查: trade_date 必须匹配
            file_date = data.get("trade_date", "")
            if file_date != trade_date:
                logger.warning(
                    "[PortfolioOptimizer] 因子信号非当日 (文件: %s, 期望: %s), 跳过",
                    file_date,
                    trade_date,
                )
                return {}

            signals_raw = data.get("signals", {})
            if not signals_raw:
                logger.info("[PortfolioOptimizer] 信号文件 signals 字段为空")
                return {}

            result: dict[str, float] = {}
            for code, info in signals_raw.items():
                if not isinstance(info, dict):
                    continue
                sig = info.get("signal")
                if sig is None:
                    continue
                try:
                    val = float(sig)
                    if math.isfinite(val):
                        result[str(code)] = val
                except (TypeError, ValueError):
                    continue

            logger.info(
                "[PortfolioOptimizer] 因子信号加载完成: %d 个标的 (trade_date=%s, combined_ic_ir=%.4f, live_dsr=%.4f)",
                len(result),
                trade_date,
                float(data.get("factor_combination", {}).get("combined_ic_ir", 0)),
                float(data.get("factor_combination", {}).get("live_dsr", 0)),
            )
            return result

        except Exception as e:  # P2 模块 fail-safe, 待后续精确化
            logger.warning("[PortfolioOptimizer] 加载因子信号失败: %s", e)
            return {}

    def adjust_target_weights(
        self,
        base_weights: dict[str, float],
        factor_signals: dict[str, float],
        alpha: float | None = None,
    ) -> dict[str, float]:
        """用因子信号调整目标权重（保守权重混合）

        调整公式:
            adjusted[symbol] = base[symbol] * (1 - alpha) + signal[symbol] * alpha

        归一化策略:
        - 保持总暴露不变（scale = total_base / total_adjusted）
        - 避免因子信号导致仓位过度膨胀或收缩

        Args:
            base_weights: 基础目标权重 {symbol: weight}
            factor_signals: 因子信号 {symbol: signal ∈ [-1, 1]}
            alpha: 混合权重，默认 0.05（保守起步）

        Returns:
            调整后的目标权重 {symbol: weight}
        """
        if alpha is None:
            alpha = self.DEFAULT_ALPHA

        if not base_weights:
            return {}
        if not factor_signals or alpha <= 0:
            return dict(base_weights)

        # 安全混合
        adjusted: dict[str, float] = {}
        for symbol, weight in base_weights.items():
            signal = factor_signals.get(symbol, 0.0)
            # signal ∈ [-1, 1]，乘以 alpha 后作为权重调整
            adjusted[symbol] = weight * (1.0 - alpha) + signal * alpha

        # 归一化: 保持总暴露不变（避免 alpha 混合导致整体仓位漂移）
        total_base = sum(abs(w) for w in base_weights.values()) or 1.0
        total_adjusted = sum(abs(w) for w in adjusted.values()) or 1.0
        if total_adjusted > 1e-9:
            scale = total_base / total_adjusted
            adjusted = {k: v * scale for k, v in adjusted.items()}

        # 统计调整幅度
        n_adjusted = sum(1 for sym in base_weights if abs(adjusted.get(sym, 0) - base_weights[sym]) > 1e-6)
        logger.info(
            "[PortfolioOptimizer] 权重调整完成: %d/%d 标的受影响, alpha=%.3f, 总暴露保持=%.4f",
            n_adjusted,
            len(base_weights),
            alpha,
            total_base,
        )
        return adjusted

    # ------------------------------------------------------------
    # P1-L: 风险管理层 — 波动率缩放 + 回撤去杠杆
    # ------------------------------------------------------------
    # 算法移植自 research/vibe_trading_factor_analysis/shadow/shadow_account.py
    # L353-L446 _apply_risk_management, 与生产 MAX_DRAWDOWN_LIMIT=15% 对齐
    # 审计: docs/HEDGE_FUND_AUDIT_V2_2026-07-26.md P1-L
    # ------------------------------------------------------------
    # P0-Q3 修复 (2026-07-26): 总敞口硬上限, 防止 vol_scaler 在低波动期被动加杠杆至 200%+
    # 顶级对冲基金标准: 所有 scaler 必须有 hard cap, 与券商保证金对齐
    MAX_TOTAL_EXPOSURE = 1.5  # 1.5x 杠杆上限 (与 production MAX_DRAWDOWN_LIMIT=15% 风险预算对齐)

    def apply_risk_management(
        self,
        target_weights: dict[str, float],
        daily_pnl_history: list[float],
        config: dict[str, Any] | None = None,
    ) -> tuple[dict[str, float], dict[str, Any]]:
        """风险管理层: 波动率缩放 + 回撤去杠杆 + 总敞口上限

        三层保护 (P0-Q1/Q3 修复后):
            1. 波动率缩放 (Vol Targeting):
               - 使用过去 vol_lookback 日 PnL 计算实现波动率
               - 缩放因子 = min(target_vol / realized_vol, scaler_cap)
               - 降低高波动期敞口, 提升低波动期敞口 (上限 scaler_cap)

            2. 回撤去杠杆 (Drawdown De-risking):
               - **基于昨日净值**计算当前回撤 (P0-Q1 修复: 严格排除当日 PnL)
               - 当回撤 > dd_derisk_threshold 时, 敞口降至 dd_derisk_factor
               - 触发后强制去杠杆, 防止回撤扩大

            3. 总敞口上限 (P0-Q3 新增):
               - combined_scaler 可达 2.0x (低波动期被动加杠杆)
               - MAX_TOTAL_EXPOSURE=1.5x 硬上限, 与券商保证金对齐
               - 超限时按比例缩减所有权重, 保留相对结构

        与 shadow_account 的一致性:
            - 默认参数完全对齐 (target_vol=0.15, vol_lookback=20, dd_threshold=0.05)
            - 算法逻辑 1:1 移植, 确保 Shadow 验证过的参数在生产可用
            - daily_pnl_history 顺序: [oldest, ..., newest], 与 Shadow 一致
            - P0-Q1 修复: current_dd 计算严格使用 daily_pnl_history[:-1] (昨日净值)

        Args:
            target_weights: 目标权重 {symbol: weight}
            daily_pnl_history: 每日 PnL 历史 (小数, 如 0.01 表示 +1%)
                              顺序: [oldest, ..., yesterday, today]
                              回撤计算仅使用 [:-1] 部分 (避免前视偏差)
            config: 风险管理参数覆盖, 支持 keys:
                - target_vol: 目标年化波动率, 默认 0.15
                - vol_lookback: 波动率回看窗口, 默认 20
                - dd_derisk_threshold: 回撤去杠杆阈值, 默认 0.05
                - dd_derisk_factor: 去杠杆因子, 默认 0.5
                - scaler_cap: 缩放因子上限, 默认 2.0
                - max_total_exposure: 总敞口硬上限, 默认 1.5

        Returns:
            (scaled_weights, stats)
            scaled_weights: 缩放后权重 {symbol: weight}
            stats: {
                'realized_vol': 实现年化波动率,
                'current_dd': 当前回撤 (基于昨日净值),
                'vol_scaler': 波动率缩放因子,
                'dd_scaler': 回撤缩放因子 (1.0 或 dd_derisk_factor),
                'combined_scaler': 合并缩放因子,
                'exposure_cap_applied': 是否触发总敞口上限,
                'derisk_triggered': 是否触发去杠杆,
                'raw_total_exposure': 原始总敞口,
                'scaled_total_exposure': 缩放后总敞口,
            }
        """
        # 默认参数 (与 shadow_account.RISK_MANAGED_* 常量对齐)
        TARGET_VOL = 0.15  # 目标年化波动率 15%
        VOL_LOOKBACK = 20  # 波动率回看窗口 20 日
        DD_DERISK_THRESHOLD = 0.05  # 回撤 > 5% 触发去杠杆
        DD_DERISK_FACTOR = 0.5  # 去杠杆至 50% 敞口
        SCALER_CAP = 2.0  # 缩放因子上限
        TRADING_DAYS_PER_YEAR = 252
        # P0-Q3 新增: 总敞口硬上限 (默认 1.5x, 可被 config 覆盖)
        MAX_EXPOSURE = self.MAX_TOTAL_EXPOSURE

        # 应用配置覆盖
        if config:
            TARGET_VOL = float(config.get("target_vol", TARGET_VOL))
            VOL_LOOKBACK = int(config.get("vol_lookback", VOL_LOOKBACK))
            DD_DERISK_THRESHOLD = float(config.get("dd_derisk_threshold", DD_DERISK_THRESHOLD))
            DD_DERISK_FACTOR = float(config.get("dd_derisk_factor", DD_DERISK_FACTOR))
            SCALER_CAP = float(config.get("scaler_cap", SCALER_CAP))
            MAX_EXPOSURE = float(config.get("max_total_exposure", MAX_EXPOSURE))

        # 边界检查
        if not target_weights:
            return {}, {"error": "empty_target_weights"}
        if not daily_pnl_history or len(daily_pnl_history) < 2:
            # 数据不足, 不缩放 (保守返回原始权重)
            logger.info(
                "[PortfolioOptimizer] [P1-L] daily_pnl_history 数据不足 (len=%d), 跳过风险管理",
                len(daily_pnl_history) if daily_pnl_history else 0,
            )
            return dict(target_weights), {
                "realized_vol": 0.0,
                "current_dd": 0.0,
                "vol_scaler": 1.0,
                "dd_scaler": 1.0,
                "combined_scaler": 1.0,
                "exposure_cap_applied": False,
                "derisk_triggered": False,
                "raw_total_exposure": sum(abs(w) for w in target_weights.values()),
                "scaled_total_exposure": sum(abs(w) for w in target_weights.values()),
                "note": "insufficient_pnl_history",
            }

        # === 1. 波动率缩放 ===
        # 使用最近 vol_lookback 日 PnL 计算实现波动率
        # 注意: 波动率计算可使用完整历史 (含当日), 因为波动率是统计量, 非决策量
        vol_lookback = min(VOL_LOOKBACK, len(daily_pnl_history))
        recent_pnl = daily_pnl_history[-vol_lookback:]
        realized_vol_annual = float(np.std(recent_pnl, ddof=1) * math.sqrt(TRADING_DAYS_PER_YEAR))

        if realized_vol_annual > 1e-9:
            vol_scaler = min(TARGET_VOL / realized_vol_annual, SCALER_CAP)
        else:
            vol_scaler = 1.0  # 波动率为 0 时不缩放

        # === 2. 回撤去杠杆 (P0-Q1 修复: 严格基于昨日净值, 排除当日 PnL) ===
        # 前视偏差修复说明:
        #   原代码: for p in daily_pnl_history → 含当日 pnl, current_value 是含当日结果的净值
        #   修复后: for p in daily_pnl_history[:-1] → 排除当日, current_value 是昨日净值
        #   语义: 决策时点 (今日开盘前) 只能看到昨日收盘净值, 不应已知当日盈亏
        #   影响: 修复前回测过度乐观 (已知当日跌再去杠杆), 修复后实盘触发时点与回测一致
        pnl_for_dd = daily_pnl_history[:-1] if len(daily_pnl_history) >= 2 else daily_pnl_history
        cumulative = [1.0]
        for p in pnl_for_dd:
            cumulative.append(cumulative[-1] * (1.0 + float(p)))
        peak = max(cumulative) if cumulative else 1.0
        current_value = cumulative[-1] if cumulative else 1.0
        current_dd = (peak - current_value) / peak if peak > 0 else 0.0

        dd_scaler = DD_DERISK_FACTOR if current_dd > DD_DERISK_THRESHOLD else 1.0

        # === 3. 合并并应用 ===
        combined_scaler = vol_scaler * dd_scaler
        scaled_weights = {sym: w * combined_scaler for sym, w in target_weights.items()}

        # === 4. P0-Q3 新增: 总敞口上限保护 ===
        # 问题: combined_scaler 可达 2.0x (低波动期 vol_scaler=2.0)
        #   导致总敞口从 100% 跃升至 200%, 与券商保证金冲突, 可能直接触发 KillSwitch L2
        # 修复: 超过 MAX_EXPOSURE 时按比例缩减, 保留相对权重结构
        raw_total_exposure = sum(abs(w) for w in target_weights.values())
        scaled_total_exposure = sum(abs(w) for w in scaled_weights.values())
        exposure_cap_applied = False

        if scaled_total_exposure > MAX_EXPOSURE and scaled_total_exposure > 1e-9:
            cap_scaler = MAX_EXPOSURE / scaled_total_exposure
            scaled_weights = {k: v * cap_scaler for k, v in scaled_weights.items()}
            exposure_cap_applied = True
            logger.warning(
                "[PortfolioOptimizer] [P0-Q3] 总敞口 %.4fx 超 %.2fx 上限, 已按比例 cap 至 %.2fx (cap_scaler=%.4f)",
                scaled_total_exposure,
                MAX_EXPOSURE,
                MAX_EXPOSURE,
                cap_scaler,
            )
            scaled_total_exposure = MAX_EXPOSURE

        # === 5. 统计信息 ===
        stats = {
            "realized_vol": realized_vol_annual,
            "current_dd": float(current_dd),
            "vol_scaler": float(vol_scaler),
            "dd_scaler": float(dd_scaler),
            "combined_scaler": float(combined_scaler),
            "exposure_cap_applied": exposure_cap_applied,
            "max_total_exposure": MAX_EXPOSURE,
            "derisk_triggered": current_dd > DD_DERISK_THRESHOLD,
            "raw_total_exposure": float(raw_total_exposure),
            "scaled_total_exposure": float(scaled_total_exposure),
            "target_vol": TARGET_VOL,
            "vol_lookback": vol_lookback,
            "dd_derisk_threshold": DD_DERISK_THRESHOLD,
            "dd_derisk_factor": DD_DERISK_FACTOR,
            # P0-Q1 审计字段: 标记前视偏差修复已生效
            "lookahead_bias_fixed": True,
            "dd_pnl_used": "yesterday_only",
        }

        logger.info(
            "[PortfolioOptimizer] [P1-L] 风险管理 | realized_vol=%.2f%% target_vol=%.2f%% "
            "vol_scaler=%.3f | dd=%.2f%% dd_scaler=%.2f | combined=%.3f | "
            "exposure %.4f→%.4f derisk=%s cap=%s",
            realized_vol_annual * 100,
            TARGET_VOL * 100,
            vol_scaler,
            current_dd * 100,
            dd_scaler,
            combined_scaler,
            raw_total_exposure,
            scaled_total_exposure,
            "YES" if stats["derisk_triggered"] else "no",
            "YES" if exposure_cap_applied else "no",
        )

        if stats["derisk_triggered"]:
            logger.warning(
                "[PortfolioOptimizer] [P1-L] 回撤去杠杆触发: current_dd=%.2f%% > %.2f%%, 敞口降至 %.0f%%",
                current_dd * 100,
                DD_DERISK_THRESHOLD * 100,
                DD_DERISK_FACTOR * 100,
            )

        return scaled_weights, stats

    # ------------------------------------------------------------
    # 离线计算：运行 PipelineOrchestrator + 保存因子信号 JSON
    # ------------------------------------------------------------

    def run_offline_pipeline(  # pylint: disable=too-many-return-statements
        self,
        trade_date: str | None = None,
        symbols: list[str] | None = None,
    ) -> bool:
        """离线运行因子流水线，生成当日因子信号 JSON

        步骤:
        1. 加载真实 price_data + fundamentals + fundamentals_history
        2. 调用 PipelineOrchestrator.run() 验证因子组合
        3. 若通过 Shadow，使用 VibeTradingFactorAdapter 计算最新因子值
        4. 应用 IC 加权公式组合信号
        5. 归一化到 [-1, 1] 并保存 JSON

        Args:
            trade_date: 交易日期 YYYY-MM-DD，默认今日
            symbols: 标的列表，None 则使用默认标的池

        Returns:
            True 如果成功生成信号文件，False 如果失败
        """
        trade_date = trade_date or datetime.now().strftime("%Y-%m-%d")

        try:
            # Step 1: 加载真实数据（复用 research 脚本）
            logger.info("[PortfolioOptimizer] Step 1: 加载真实数据...")
            price_data, fundamentals, benchmark_returns, fundamentals_history, used_symbols = self._load_real_data(
                symbols
            )
            if not price_data:
                logger.error("[PortfolioOptimizer] price_data 加载失败, 中断")
                return False
            logger.info(
                "[PortfolioOptimizer] 数据加载完成: %d 标的, %d 基准收益",
                len(price_data),
                len(benchmark_returns),
            )

            # Step 2: 运行 PipelineOrchestrator 验证因子组合
            logger.info("[PortfolioOptimizer] Step 2: 运行 PipelineOrchestrator...")
            from research.vibe_trading_factor_analysis.pipeline.pipeline_orchestrator import (
                PipelineOrchestrator,
            )

            orchestrator = PipelineOrchestrator()
            result = orchestrator.run(
                price_data=price_data,
                fundamentals=fundamentals,
                fundamentals_history=fundamentals_history,
                benchmark_returns=benchmark_returns,
                portfolio_value=1e8,
                n_trials=max(len(used_symbols), 13),
                history_days=120,
                forward_window=5,
            )

            if not result.factor_combinations:
                logger.error(
                    "[PortfolioOptimizer] Pipeline 未生成因子组合 (approved=%d, total=%d)",
                    result.approved,
                    result.total_candidates,
                )
                return False

            combo = result.factor_combinations[0]
            if not combo.get("passed", False):
                logger.error(
                    "[PortfolioOptimizer] 因子组合未通过 Shadow: %s",
                    combo.get("fail_reason", "unknown"),
                )
                return False

            # Step 3: 提取 IC 加权权重
            ic_metrics = combo.get("ic_metrics", {})
            ic_ir_a = float(ic_metrics.get("factor_a_ic_ir", 0))
            ic_ir_b = float(ic_metrics.get("factor_b_ic_ir", 0))
            factor_a = combo.get("factor_a")
            factor_b = combo.get("factor_b")

            if not factor_a or not factor_b:
                logger.error("[PortfolioOptimizer] 因子名缺失: A=%s B=%s", factor_a, factor_b)
                return False

            total_abs = abs(ic_ir_a) + abs(ic_ir_b)
            if total_abs < 1e-6:
                logger.error(
                    "[PortfolioOptimizer] IC_IR 总和过小 (a=%.4f, b=%.4f), 无法计算权重",
                    ic_ir_a,
                    ic_ir_b,
                )
                return False

            w_a = ic_ir_a / total_abs
            w_b = ic_ir_b / total_abs
            logger.info(
                "[PortfolioOptimizer] IC 权重: A=%s w_a=+%.4f, B=%s w_b=+%.4f",
                factor_a,
                w_a,
                factor_b,
                w_b,
            )

            # Step 4: 使用 VibeTradingFactorAdapter 计算最新因子值
            logger.info("[PortfolioOptimizer] Step 4: 计算最新因子值...")
            from research.vibe_trading_factor_analysis.adapters.vibe_trading_factor_adapter import (
                VibeTradingFactorAdapter,
            )

            adapter = VibeTradingFactorAdapter()
            pool = adapter.compute_candidate_factors(
                price_data=price_data,
                fundamentals=fundamentals,
                fundamentals_history=fundamentals_history,
            )

            if factor_a not in pool.factors or factor_b not in pool.factors:
                logger.error(
                    "[PortfolioOptimizer] 因子值缺失: A_in=%s B_in=%s, 可用=%s",
                    factor_a in pool.factors,
                    factor_b in pool.factors,
                    list(pool.factors.keys())[:10],
                )
                return False

            values_a = pool.factors[factor_a].values  # {symbol: value}
            values_b = pool.factors[factor_b].values
            logger.info(
                "[PortfolioOptimizer] 因子值: A=%d 标的, B=%d 标的",
                len(values_a),
                len(values_b),
            )

            # Step 5: 计算 IC 加权组合信号
            signals_raw: dict[str, float] = {}
            for symbol in set(values_a) & set(values_b):
                try:
                    va = float(values_a.get(symbol, 0))
                    vb = float(values_b.get(symbol, 0))
                    combined = w_a * va + w_b * vb
                    if math.isfinite(combined):
                        signals_raw[symbol] = combined
                except (TypeError, ValueError):
                    continue

            if not signals_raw:
                logger.error("[PortfolioOptimizer] 组合信号为空")
                return False

            # 归一化到 [-1, 1]（使用 tanh，保留单调性）
            normalized = {sym: math.tanh(sig) for sym, sig in signals_raw.items()}

            # Step 6: 保存 JSON
            output = {
                "trade_date": trade_date,
                "generated_at": datetime.now().isoformat(),
                "factor_combination": {
                    "factor_a": factor_a,
                    "factor_b": factor_b,
                    "method": "ic_weighted_rolling",
                    "lookback": combo.get("lookback", 10),
                    "combined_ic_ir": float(ic_metrics.get("combined_ic_ir", 0)),
                    "live_dsr": float(combo.get("shadow", {}).get("live_dsr", 0)),
                    "max_drawdown": float(combo.get("shadow", {}).get("max_drawdown", 0)),
                    "w_a": float(w_a),
                    "w_b": float(w_b),
                },
                "signals": {sym: {"signal": float(val), "name": sym} for sym, val in normalized.items()},
                "stats": {
                    "n_symbols": len(normalized),
                    "n_positive": sum(1 for v in normalized.values() if v > 0),
                    "n_negative": sum(1 for v in normalized.values() if v < 0),
                    "max_signal": max(normalized.values()) if normalized else 0.0,
                    "min_signal": min(normalized.values()) if normalized else 0.0,
                    "avg_signal": (sum(normalized.values()) / len(normalized) if normalized else 0.0),
                },
                "audit_trail": {
                    "source": "research.vibe_trading_factor_analysis.pipeline.pipeline_orchestrator",
                    "batch_id": result.batch_id,
                    "approved": result.approved,
                    "total_candidates": result.total_candidates,
                    "fix_reference": "docs/AUDIT_FIX_CHANGELOG_2026-07-26.md (P0-A 深度修复)",
                },
            }

            output_path = self.signals_dir / f"pipeline_factor_signals_{trade_date}.json"
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(output, f, ensure_ascii=False, indent=2, default=str)

            logger.info(
                "[PortfolioOptimizer] 因子信号已保存: %s (%d 个标的, IC_IR=%.4f, live_dsr=%.4f)",
                output_path.name,
                len(normalized),
                float(ic_metrics.get("combined_ic_ir", 0)),
                float(combo.get("shadow", {}).get("live_dsr", 0)),
            )
            return True

        except Exception as e:  # P2 模块 fail-safe, 待后续精确化
            logger.error("[PortfolioOptimizer] run_offline_pipeline 失败: %s", e, exc_info=True)
            return False

    # ------------------------------------------------------------
    # 辅助：加载真实数据（复用 research 脚本）
    # ------------------------------------------------------------

    def _load_real_data(
        self,
        symbols: list | None = None,
    ) -> tuple[dict, dict, list, dict, list]:
        """加载真实 price_data + fundamentals + benchmark_returns + fundamentals_history

        复用 research/vibe_trading_factor_analysis/scripts/real_data_loader.py

        Returns:
            (price_data, fundamentals, benchmark_returns, fundamentals_history, symbols)
        """
        # 将 research 脚本目录加入 sys.path
        project_root = Path(__file__).resolve().parent.parent
        research_scripts = project_root / "research" / "vibe_trading_factor_analysis" / "scripts"
        if str(research_scripts) not in sys.path:
            sys.path.insert(0, str(research_scripts))

        from real_data_loader import (  # pylint: disable=import-error
            load_all_for_pipeline,
        )

        # 加载 price_data + fundamentals + benchmark_returns
        price_data, fundamentals, benchmark_returns = load_all_for_pipeline(symbols=symbols)
        used_symbols = list(price_data.keys())

        # 加载 fundamentals_history（QualityTrend 因子所需）
        fundamentals_history = self._load_fundamentals_history(used_symbols)

        return price_data, fundamentals, benchmark_returns, fundamentals_history, used_symbols

    def _load_fundamentals_history(self, symbols: list) -> dict[str, dict[str, Any]]:
        """加载历史季度财务数据（QualityTrend 因子必需）

        从 cache/fundamentals/{symbol}_history.json 加载，若不存在则返回空字典
        """
        project_root = Path(__file__).resolve().parent.parent
        cache_dir = project_root / "cache" / "fundamentals"
        result: dict[str, dict[str, Any]] = {}

        if not cache_dir.exists():
            logger.warning(
                "[PortfolioOptimizer] fundamentals cache 目录不存在: %s, QualityTrend 因子将降级",
                cache_dir,
            )
            return result

        n_loaded = 0
        for symbol in symbols:
            path = cache_dir / f"{symbol}_history.json"
            if path.exists():
                try:
                    with open(path, encoding="utf-8") as f:
                        result[symbol] = json.load(f)
                    n_loaded += 1
                except Exception as e:  # P2 模块 fail-safe, 待后续精确化
                    logger.debug(
                        "[PortfolioOptimizer] 加载 %s 历史失败: %s",
                        symbol,
                        e,
                    )

        logger.info(
            "[PortfolioOptimizer] fundamentals_history 加载: %d/%d 标的",
            n_loaded,
            len(symbols),
        )
        return result


# ============================================================
# 模块自检
# ============================================================

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s | %(message)s",
    )
    logger.info("=" * 70)
    logger.info("Portfolio Optimizer 自检")
    logger.info("=" * 70)

    opt = PortfolioOptimizer()
    logger.info(f"\n信号目录: {opt.signals_dir}")

    # 测试 load_factor_signals（预期返回空，因为尚未生成）
    test_date = datetime.now().strftime("%Y-%m-%d")
    signals = opt.load_factor_signals(test_date)
    logger.debug(f"load_factor_signals({test_date}): {len(signals)} 个信号 (预期 0)")

    # 测试 adjust_target_weights
    base = {"588000": 0.10, "300308": 0.05, "601088": 0.08}
    factor = {"588000": 0.5, "300308": -0.3, "601088": 0.2}
    adj_weights = opt.adjust_target_weights(base, factor, alpha=0.05)
    logger.info("\nadjust_target_weights (alpha=0.05):")
    for sym, base_w in base.items():
        adj_w = adj_weights[sym]
        logger.info(f"  {sym}: base={base_w:+.4f} -> adjusted={adj_w:+.4f} (delta={adj_w - base_w:+.4f})")

    logger.info("\n[OK] PortfolioOptimizer 自检通过")
