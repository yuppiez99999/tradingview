"""压力测试模块 — 极端情景回测 + 蒙特卡洛 + 硬止损

提供压力测试常量和报告生成功能。
与 project_memory 约束对齐:
  - MAX_DRAWDOWN_LIMIT = 15% (生产硬止损线)
  - TAIL_HEDGE_THRESHOLD = 5% (尾部对冲触发)
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

# ============================================================
# 压力测试常量 (与 project_memory 约束对齐)
# ============================================================

# 硬止损最大回撤线 — 超过即触发 Kill Switch
# 对齐 ShadowAccount fail-fast: 单日回撤>3% 或 3日累计回撤>5%
# 生产 MAX_DRAWDOWN_LIMIT=15%
HARD_STOP_MAX_DRAWDOWN: float = 0.15

# 尾部对冲触发阈值 — 组合回撤超过此值启动期权保护
# 对齐 VIX 触发阈值 (iVIX>20 启动期权保护)
TAIL_HEDGE_THRESHOLD: float = 0.05

# 历史压力情景定义 (6 大极端情景)
STRESS_SCENARIOS: dict[str, dict[str, Any]] = {
    '2008金融危机': {'equity_shock': -0.45, 'volatility_spike': 2.5, 'correlation_increase': 0.3},
    '2015股灾': {'equity_shock': -0.40, 'volatility_spike': 2.0, 'correlation_increase': 0.25},
    '2020疫情': {'equity_shock': -0.35, 'volatility_spike': 1.8, 'correlation_increase': 0.2},
    '欧债危机': {'equity_shock': -0.25, 'volatility_spike': 1.5, 'correlation_increase': 0.15},
    '中美贸易战': {'equity_shock': -0.20, 'volatility_spike': 1.3, 'correlation_increase': 0.1},
    '流动性危机': {'equity_shock': -0.15, 'volatility_spike': 1.6, 'correlation_increase': 0.4},
}


def generate_stress_report(portfolio: dict[str, Any] | None = None,
                           scenarios: dict[str, Any] | None = None,
                           monte_carlo_runs: int = 1000) -> str:
    """生成压力测试报告。

    Args:
        portfolio: 持仓字典 {'code': {'weight': w, ...}}, None 时用占位
        scenarios: 自定义情景, None 时用 STRESS_SCENARIOS
        monte_carlo_runs: 蒙特卡洛模拟次数

    Returns:
        Markdown 格式的压力测试报告
    """
    if scenarios is None:
        scenarios = STRESS_SCENARIOS
    if portfolio is None:
        portfolio = {}

    lines = []
    lines.append('# 极端压力测试报告')
    lines.append(f'**生成时间**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    lines.append(f'**硬止损线**: {HARD_STOP_MAX_DRAWDOWN:.0%} | **尾部对冲阈值**: {TAIL_HEDGE_THRESHOLD:.0%}')
    lines.append('')

    # 历史情景回测
    lines.append('## 一、历史极端情景回测')
    lines.append('')
    lines.append('| 情景 | 权益冲击 | 波动率倍增 | 相关性增加 | 预估回撤 | 是否触发硬止损 |')
    lines.append('|------|----------|-----------|-----------|---------|--------------|')
    for name, params in scenarios.items():
        eq_shock = params.get('equity_shock', 0)
        vol_spike = params.get('volatility_spike', 1)
        corr_inc = params.get('correlation_increase', 0)
        # 简化估算: 回撤 ≈ 权益冲击 × (1 + 相关性增加)
        est_drawdown = abs(eq_shock) * (1 + corr_inc)
        trigger = '🔴 是' if est_drawdown > HARD_STOP_MAX_DRAWDOWN else '🟢 否'
        lines.append(f'| {name} | {eq_shock:.0%} | {vol_spike:.1f}x | +{corr_inc:.0%} | {est_drawdown:.1%} | {trigger} |')
    lines.append('')

    # 蒙特卡洛模拟
    lines.append(f'## 二、蒙特卡洛模拟 ({monte_carlo_runs} 次)')
    lines.append('')
    try:
        import random
        # 简化: 正态分布模拟组合收益
        returns = [random.gauss(0.08, 0.20) for _ in range(monte_carlo_runs)]
        returns.sort()
        var_95 = returns[int(0.05 * monte_carlo_runs)]
        var_99 = returns[int(0.01 * monte_carlo_runs)]
        max_loss = min(returns)
        lines.append(f'- **95% VaR**: {var_95:.1%}')
        lines.append(f'- **99% VaR**: {var_99:.1%}')
        lines.append(f'- **最差情景**: {max_loss:.1%}')
        lines.append(f'- **触发硬止损概率**: {sum(1 for r in returns if r < -HARD_STOP_MAX_DRAWDOWN) / monte_carlo_runs:.1%}')
    except (ImportError, AttributeError) as e:
        lines.append(f'- 蒙特卡洛模拟失败: {e}')
    lines.append('')

    # 硬止损检查
    lines.append('## 三、硬止损检查')
    lines.append('')
    lines.append(f'- **硬止损线**: {HARD_STOP_MAX_DRAWDOWN:.0%} (回撤超过此值触发 Kill Switch)')
    lines.append(f'- **尾部对冲阈值**: {TAIL_HEDGE_THRESHOLD:.0%} (回撤超过此值启动期权保护)')
    lines.append('')

    return '\n'.join(lines)
