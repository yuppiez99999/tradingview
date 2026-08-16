"""
波动率目标控制器 (Volatility Target Controller)
================================================
修改原因: P2 波动率目标控制实质化 - vol_scale信号反馈到建仓预算
修改日期: 2026-07-21

核心问题:
    v76_enhanced_report 已计算出 vol_scale=0.386（应缩仓至39%），
    但这个信号没有反馈到建仓计划。本模块作为建仓预算的前置过滤器。

功能:
    1. 计算当前组合已实现波动率 (realized vol)
    2. 与目标波动率对比, 计算 vol_scale 因子
    3. 当 vol_scale < 0.8 时, 按比例缩减当日建仓预算
    4. 输出调整后的每日建仓金额

参数:
    目标年化波动率: 12% (年化收益8% / 最大回撤15% 对应的合理波动率)
    回看窗口: 20个交易日 (约1个月)
    衰减因子: EWMA lambda=0.94

用法:
    from utils.vol_target_controller import VolTargetController
    vtc = VolTargetController()
    adjusted_budget = vtc.adjust_daily_budget(original_budget=150000)
    # 如果 vol_scale=0.386, 则 adjusted_budget = 150000 * 0.386 = 57900
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, TypedDict, cast

import numpy as np

logger = logging.getLogger("vol_target_controller")

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = BASE_DIR / "config"
# v8.6.9 P0 FIX (2026-07-26): 报告路径修正
# 原始 bug: REPORTS_DIR 指向 v7.5_institutional/reports/, 但该目录在 v8.4 中已不存在,
#          实际报告在 每日报告归档/{date}/daily_pnl_report_{date}.json
# 影响: _extract_daily_returns() 永远找不到报告 → 返回空列表 → realized_vol=None
#       → vol_scale=None (视为正常) → 波动率缩仓实际未生效
# 修复: 优先查找 每日报告归档/{date}/ 路径, 旧路径作为回退
REPORTS_DIR = BASE_DIR / "v7.5_institutional" / "reports"
DAILY_REPORT_DIR = BASE_DIR / "每日报告归档"
CACHE_DIR = BASE_DIR / "cache"


class VolBudgetResult(TypedDict, total=False):
    """波动率预算调整结果 (混合类型字段: 数值/布尔/时间)"""
    original_budget: float
    vol_scale: float
    threshold_active: bool
    adjusted_budget: float
    reduction_pct: float
    realized_vol: float
    target_vol: float
    recommendation: str
    timestamp: str


class VolTargetController:
    """波动率目标控制器 - 建仓预算的前置过滤器

    设计原则 (参考 AQR/Man Group 的 Vol Targeting):
        1. target_vol: 目标年化波动率 (12%)
        2. realized_vol: 已实现波动率 (20日EWMA)
        3. vol_scale = target_vol / realized_vol
        4. 当 vol_scale > 1.0, cap at 1.0 (不加杠杆)
        5. 当 vol_scale < 0.3, floor at 0.3 (保留最低建仓)
        6. adjusted_budget = original_budget * vol_scale
    """

    # 波动率参数
    TARGET_ANNUAL_VOL = 0.12  # 目标年化波动率 12%
    LOOKBACK_DAYS = 20  # 回看窗口 20个交易日
    EWMA_LAMBDA = 0.94  # EWMA衰减因子
    VOL_SCALE_CAP = 1.0  # vol_scale上限 (不加杠杆)
    VOL_SCALE_FLOOR = 0.30  # vol_scale下限 (保留30%建仓)
    VOL_SCALE_THRESHOLD = 0.80  # 低于此值开始缩仓
    ANNUALIZATION_FACTOR = math.sqrt(252)  # 年化因子

    def __init__(self, target_vol: float | None = None):
        if target_vol is not None:
            self.TARGET_ANNUAL_VOL = target_vol
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def calc_realized_vol(self, daily_returns: list[float] | None = None) -> float:
        """计算已实现波动率 (EWMA)

        Args:
            daily_returns: 日收益率序列 (如 [0.01, -0.02, 0.005, ...])
                          如果为None, 尝试从报告文件读取

        Returns:
            年化已实现波动率 (如 0.25 表示 25%)
        """
        if daily_returns is None:
            daily_returns = self._load_portfolio_returns()

        if not daily_returns or len(daily_returns) < 5:
            logger.warning("日收益率数据不足, 使用默认波动率 20%")
            return 0.20

        # EWMA 波动率计算
        returns = np.array(daily_returns[-self.LOOKBACK_DAYS :])
        n = len(returns)

        # EWMA 权重
        weights = np.array([(1 - self.EWMA_LAMBDA) * (self.EWMA_LAMBDA**i) for i in range(n - 1, -1, -1)])
        weights /= weights.sum()

        # EWMA 方差
        mean_return = np.average(returns, weights=weights)
        variance = np.average((returns - mean_return) ** 2, weights=weights)
        daily_vol = math.sqrt(variance)

        # 年化
        annual_vol = daily_vol * self.ANNUALIZATION_FACTOR

        logger.info(f"已实现波动率: 日{daily_vol * 100:.2f}% → 年化{annual_vol * 100:.2f}%")
        return annual_vol

    def calc_vol_scale(self, realized_vol: float | None = None) -> float:
        """计算波动率缩放因子

        vol_scale = target_vol / realized_vol
        Capped at [VOL_SCALE_FLOOR, VOL_SCALE_CAP]

        Returns:
            vol_scale 因子 (0.3 ~ 1.0)
        """
        if realized_vol is None:
            realized_vol = self.calc_realized_vol()

        if realized_vol <= 0:
            return self.VOL_SCALE_CAP

        vol_scale = self.TARGET_ANNUAL_VOL / realized_vol
        vol_scale = max(self.VOL_SCALE_FLOOR, min(self.VOL_SCALE_CAP, vol_scale))

        logger.info(
            f"Vol Scale: target={self.TARGET_ANNUAL_VOL * 100:.1f}% / "
            f"realized={realized_vol * 100:.1f}% = {vol_scale:.3f}"
        )
        return vol_scale

    def adjust_daily_budget(
        self,
        original_budget: float,
        realized_vol: float | None = None,
        force_scale: float | None = None,
    ) -> VolBudgetResult:
        """调整当日建仓预算

        Args:
            original_budget: 原始每日建仓金额 (如 150000)
            realized_vol: 已实现波动率 (如果已有, 避免重复计算)
            force_scale: 强制指定 vol_scale (用于测试)

        Returns:
            {
                "original_budget": 150000,
                "vol_scale": 0.386,
                "threshold_active": True,  # vol_scale < 0.8 触发
                "adjusted_budget": 57900,
                "reduction_pct": 0.614,  # 缩减比例
                "realized_vol": 0.31,
                "target_vol": 0.12,
                "recommendation": "缩仓至39%: 波动率过高(31% vs 目标12%)",
            }
        """
        if force_scale is not None:
            vol_scale = force_scale
            realized_vol = self.TARGET_ANNUAL_VOL / vol_scale if vol_scale > 0 else 0.20
        else:
            if realized_vol is None:
                realized_vol = self.calc_realized_vol()
            vol_scale = self.calc_vol_scale(realized_vol)

        threshold_active = vol_scale < self.VOL_SCALE_THRESHOLD

        if threshold_active:
            adjusted_budget = original_budget * vol_scale
        else:
            adjusted_budget = original_budget  # 波动率正常, 不缩减

        reduction_pct = 1.0 - (adjusted_budget / original_budget) if original_budget > 0 else 0

        # 生成建议文本
        if vol_scale >= self.VOL_SCALE_THRESHOLD:
            recommendation = f"正常建仓: 波动率({realized_vol * 100:.1f}%)在合理范围内"
        elif vol_scale >= 0.5:
            recommendation = f"适度缩仓至{vol_scale * 100:.0f}%: 波动率偏高({realized_vol * 100:.1f}% vs 目标{self.TARGET_ANNUAL_VOL * 100:.0f}%)"
        else:
            recommendation = f"大幅缩仓至{vol_scale * 100:.0f}%: 波动率过高({realized_vol * 100:.1f}% vs 目标{self.TARGET_ANNUAL_VOL * 100:.0f}%)"

        result = {
            "original_budget": original_budget,
            "vol_scale": round(vol_scale, 4),
            "threshold_active": threshold_active,
            "adjusted_budget": round(adjusted_budget, 2),
            "reduction_pct": round(reduction_pct, 4),
            "realized_vol": round(realized_vol, 4),
            "target_vol": self.TARGET_ANNUAL_VOL,
            "recommendation": recommendation,
            "timestamp": datetime.now().isoformat(),
        }

        # 保存缓存 (供其他模块读取)
        self._save_cache(result)

        return cast(VolBudgetResult, result)

    def _load_portfolio_returns(self) -> list[float]:
        """从盘后报告中加载组合日收益率

        数据源: v7.5_institutional/reports/daily_pnl_report_*.json
        """
        returns = []

        # 扫描最近30天的报告
        today = datetime.now()
        for i in range(60):
            date = today - timedelta(days=i)
            date_str = date.strftime("%Y-%m-%d")
            date_no_dash = date_str.replace("-", "")

            # v8.6.9 P0 FIX: 多路径查找报告
            candidates = [
                DAILY_REPORT_DIR / date_str / f"daily_pnl_report_{date_str}.json",
                DAILY_REPORT_DIR / date_str / f"daily_pnl_report_{date_no_dash}.json",
                REPORTS_DIR / f"daily_pnl_report_{date_str}.json",
                REPORTS_DIR / f"daily_pnl_report_{date_no_dash}.json",
            ]

            for report_path in candidates:
                if not report_path.exists():
                    continue
                try:
                    with open(report_path, encoding="utf-8") as f:
                        report = json.load(f)
                    # 尝试提取日收益率
                    pnl = report.get("portfolio_summary", {})
                    daily_return = pnl.get("daily_return_pct", pnl.get("total_return_pct", 0))
                    if isinstance(daily_return, (int, float)):
                        returns.append(daily_return / 100.0 if abs(daily_return) > 1 else daily_return)
                    break  # 找到一份即可, 跳出候选路径循环
                except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError):
                    continue

        # 如果报告数据不足, 尝试从持仓成本与当前价估算
        if len(returns) < 5:
            returns = self._estimate_returns_from_positions()

        returns.reverse()  # 从旧到新排列
        return returns

    def _estimate_returns_from_positions(self) -> list[float]:
        """从持仓成本与当前价推断隐含波动率"""
        try:
            with open(CONFIG_DIR / "positions.json", encoding="utf-8") as f:
                positions = json.load(f)
        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError):
            return [0.01, -0.01, 0.005, -0.008, 0.012]  # 默认值

        # 计算各标的的成本→当前价的年化波动率
        vols = []
        for _code, pos in positions.get("positions", {}).items():
            cost = pos.get("avg_cost", pos.get("est_price", 0))
            current = pos.get("est_price", 0)
            if cost > 0 and current > 0:
                return_pct = (current - cost) / cost
                # 假设持仓12天, 年化波动率
                daily_vol = abs(return_pct) / math.sqrt(12)
                vols.append(daily_vol)

        if not vols:
            return [0.01, -0.01, 0.005, -0.008, 0.012]

        # 用组合波动率模拟日收益序列
        # N-2 修复 (2026-08-09): 移除全局 np.random.seed (污染进程级 RNG, 影响下游随机性);
        # 改用局部 np.random.default_rng(seed) 隔离, 不影响全局状态。
        avg_vol = np.mean(vols)
        rng = np.random.default_rng(42)
        simulated_returns = rng.normal(0, avg_vol, 20).tolist()
        return cast(List[float], simulated_returns)

    def _save_cache(self, result: dict) -> None:
        """缓存结果供其他模块读取"""
        cache_path = CACHE_DIR / "vol_target_latest.json"
        try:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as e:
            logger.warning(f"保存vol缓存失败: {e}")

    @classmethod
    def load_latest_scale(cls) -> float | None:
        """静态方法: 读取最新 vol_scale (供 generate_daily_trade_plan 调用)"""
        cache_path = CACHE_DIR / "vol_target_latest.json"
        if not cache_path.exists():
            return None
        try:
            with open(cache_path, encoding="utf-8") as f:
                data = json.load(f)
            vol_scale_val = cast(Dict[str, Any], data).get("vol_scale")
            return float(vol_scale_val) if isinstance(vol_scale_val, (int, float)) else None
        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError):
            return None


# ============================================================
# CLI 入口
# ============================================================
if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(description="波动率目标控制器")
    parser.add_argument("--budget", type=float, default=150000, help="原始每日建仓金额")
    parser.add_argument("--target-vol", type=float, default=0.12, help="目标年化波动率")
    parser.add_argument("--force-vol", type=float, default=None, help="强制指定realized_vol (测试)")
    args = parser.parse_args()

    vtc = VolTargetController(target_vol=args.target_vol)
    result = vtc.adjust_daily_budget(
        original_budget=args.budget,
        realized_vol=args.force_vol,
    )

    logger.info(json.dumps(result, ensure_ascii=False, indent=2))
    logger.info(f"\n{'=' * 50}")
    logger.info(f"原始预算: ¥{result['original_budget']:,.0f}")
    logger.info(f"Vol Scale: {result['vol_scale']:.3f} ({'触发缩仓' if result['threshold_active'] else '正常'})")
    logger.info(f"调整后预算: ¥{result['adjusted_budget']:,.0f}")
    logger.info(f"建议: {result['recommendation']}")
