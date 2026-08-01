# -*- coding: utf-8 -*-
"""
回测重跑脚本 — 用修复后的代码重新验证年化收益率和回撤
=====================================================
强制 resume=False, 全量重跑 24 个月 (2024-01 ~ 2025-12),
确保结果反映修复后代码的真实表现, 不复用任何旧缓存。
"""
from __future__ import annotations

import json
import logging
import sys
import time
import traceback
from pathlib import Path

# 确保项目路径
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "v8.3_institutional"))
sys.path.insert(0, str(ROOT / "utils"))

from research.backtest_runner import run_backtest, MIN_ANNUAL_RETURN, MAX_DRAWDOWN_LIMIT  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(ROOT / "logs" / f"backtest_rerun_{time.strftime('%Y%m%d_%H%M%S')}.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("backtest_rerun")


def main() -> int:
    symbols = ["600519", "000858", "601318", "000001", "600036", "601398", "600276", "000063"]
    start, end = "2024-01-01", "2025-12-31"

    logger.info("=" * 80)
    logger.info("回测重跑启动: 用修复后代码重新验证 (resume=False, 全量重跑)")
    logger.info("  标的: %s", symbols)
    logger.info("  期间: %s ~ %s", start, end)
    logger.info("  验收: 年化 >= %.0f%% AND 最大回撤 <= %.0f%%",
                MIN_ANNUAL_RETURN * 100, MAX_DRAWDOWN_LIMIT * 100)
    logger.info("=" * 80)

    t0 = time.time()
    try:
        result = run_backtest(symbols, start=start, end=end, resume=False)
    except Exception:
        logger.error("回测异常崩溃:\n%s", traceback.format_exc())
        return 2

    elapsed = time.time() - t0
    logger.info("=" * 80)
    logger.info("回测完成, 耗时 %.1f 分钟", elapsed / 60.0)
    if "error" in result:
        logger.error("回测返回错误: %s", result["error"])
        return 3

    annual = result["annual_return"]
    max_dd = result["max_drawdown"]
    win_rate = result["win_rate"]
    months = result["months"]
    acc = result.get("acceptance", {})

    logger.info("-" * 80)
    logger.info("【回测结果汇总】")
    logger.info("  月份样本数:    %d", months)
    logger.info("  年化收益率:    %.4f  (%.2f%%)   阈值 >= %.0f%%   %s",
                annual, annual * 100, MIN_ANNUAL_RETURN * 100,
                "✓" if annual >= MIN_ANNUAL_RETURN else "✗")
    logger.info("  最大回撤:      %.4f  (%.2f%%)   阈值 <= %.0f%%   %s",
                max_dd, max_dd * 100, MAX_DRAWDOWN_LIMIT * 100,
                "✓" if max_dd <= MAX_DRAWDOWN_LIMIT else "✗")
    logger.info("  月度胜率:      %.2f%%", win_rate * 100)
    logger.info("  最终权益曲线:  %s",
                " → ".join(f"{r['portfolio_return']*100:+.2f}%" for r in result["records"]))
    logger.info("-" * 80)
    logger.info("验收结论: %s", "PASSED ✓" if acc.get("passed") else "FAILED ✗")

    if not acc.get("passed"):
        # 未达标分析 (用户要求: 不回退修复, 而是优化策略)
        logger.warning("=" * 80)
        logger.warning("【未达标分析】(用户要求: 优化策略而非回退修复)")
        if annual < MIN_ANNUAL_RETURN:
            logger.warning("  年化不达标 (%.2f%% < %.0f%%):", annual * 100, MIN_ANNUAL_RETURN * 100)
            # 分析月度收益分布
            rets = [r["portfolio_return"] for r in result["records"]]
            pos = [r for r in rets if r > 0]
            neg = [r for r in rets if r < 0]
            logger.warning("    月度收益: 正 %d 个月 (均值 +%.2f%%), 负 %d 个月 (均值 %.2f%%)",
                           len(pos), sum(pos)/len(pos)*100 if pos else 0,
                           len(neg), sum(neg)/len(neg)*100 if neg else 0)
            logger.warning("    最大单月盈利: +%.2f%%, 最大单月亏损: %.2f%%",
                           max(rets)*100 if rets else 0, min(rets)*100 if rets else 0)
            # 分析 regime 分布
            regimes = {}
            for r in result["records"]:
                rg = r.get("market_regime", {}).get("regime", "unknown")
                regimes[rg] = regimes.get(rg, 0) + 1
            logger.warning("    市场状态分布: %s", regimes)
            # 分析成本占比
            total_cost = sum(r.get("cost", {}).get("total_cost", 0) for r in result["records"])
            gross_rets = [r.get("cost", {}).get("gross_return", r["portfolio_return"]) for r in result["records"]]
            gross_total = sum(gross_rets)
            logger.warning("    累计成本: %.4f%%, 累计毛收益: %.2f%%, 成本占比: %.1f%%",
                           total_cost*100, gross_total*100,
                           abs(total_cost/gross_total*100) if gross_total else 0)
        if max_dd > MAX_DRAWDOWN_LIMIT:
            logger.warning("  回撤不达标 (%.2f%% > %.0f%%): 需强化回撤熔断器", max_dd*100, MAX_DRAWDOWN_LIMIT*100)
        logger.warning("=" * 80)
        logger.warning("【策略优化方向建议 (不回退修复)】")
        logger.warning("  1. 若年化过低且胜率<55%%: 信号质量不足 → 增强 alpha 来源 (LGB + 多因子融合)")
        logger.warning("  2. 若成本占比>30%%: 换手率过高 → 引入换手率约束 (HOLD_DAYS_MIN 或 turnover_penalty)")
        logger.warning("  3. 若熊市月份集中亏损: regime 切换不及时 → 强化 MA60 斜率/波动率 regime 识别")
        logger.warning("  4. 若单标的集中亏损: 个股风险预算 → 单标的 VaR + 行业分散")
        return 1

    logger.info("【达标确认】修复后代码回测验证通过: 年化 %.2f%% >= 8%%, 回撤 %.2f%% <= 15%%",
                annual*100, max_dd*100)
    return 0


if __name__ == "__main__":
    sys.exit(main())
