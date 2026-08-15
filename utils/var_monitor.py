"""
VaR (Value at Risk) 监控模块
=============================

基于 v10.0 投资计划手册第四章风控规则:
    95% VaR 日限额: -3% (15万 / 500万)
    99% VaR 日限额: -5% (25万 / 500万)

计算方法: 历史模拟法 (252 日)
超限动作: 减仓 10% (95%) / 减仓 20% + 加对冲 (99%)

用法:
    from utils.var_monitor import VaRMonitor
    vm = VaRMonitor()
    result = vm.calculate_var(returns_history, portfolio_value=5_000_000)
    if result["var_95_breach"]:
        actions = vm.execute_breach_response("var_95")
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger("var_monitor")

BASE_DIR = Path(__file__).resolve().parent.parent
LOG_FILE = BASE_DIR / "logs" / "var_events.jsonl"


class VaRMonitor:
    """VaR 风险价值监控"""

    # VaR 限额 (v10.0 配置)
    VAR_95_LIMIT_PCT = -0.03  # 95% VaR 日限额: -3%
    VAR_99_LIMIT_PCT = -0.05  # 99% VaR 日限额: -5%
    LOOKBACK_DAYS = 252  # 历史模拟法窗口

    def __init__(self, lookback_days: int | None = None):  # type: ignore
        self.lookback_days = lookback_days or self.LOOKBACK_DAYS
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

    def calculate_var(
        self,
        returns_history: list[float] | np.ndarray,
        portfolio_value: float,
        confidence_levels: list[float] | None = None,
    ) -> dict[str, Any]:
        """计算 VaR (历史模拟法)

        Args:
            returns_history: 历史日收益率序列 (至少 30 日)
            portfolio_value: 当前组合净值
            confidence_levels: 置信水平列表 (默认 [0.95, 0.99])

        Returns:
            {
                "timestamp": "...",
                "method": "historical_simulation",
                "lookback_days": 252,
                "portfolio_value": 5000000,
                "var_95_pct": -0.025,
                "var_95_amount": -125000,
                "var_95_breach": False,
                "var_99_pct": -0.045,
                "var_99_amount": -225000,
                "var_99_breach": False,
                "actions": [...],
            }
        """
        confidence_levels = confidence_levels or [0.95, 0.99]

        returns = np.array(returns_history, dtype=float)
        if len(returns) < 30:
            logger.warning(f"历史数据不足: {len(returns)} < 30, 使用可用数据计算")
        if len(returns) == 0:
            return self._empty_result(portfolio_value)

        # 取最近 N 日
        if len(returns) > self.lookback_days:
            returns = returns[-self.lookback_days :]

        result: dict[str, Any] = {
            "timestamp": datetime.now().isoformat(),
            "method": "historical_simulation",
            "lookback_days": len(returns),
            "portfolio_value": portfolio_value,
        }

        actions: list[str] = []

        for cl in confidence_levels:
            # 历史模拟法: 取分位数
            var_pct = float(np.percentile(returns, (1 - cl) * 100))
            var_amount = var_pct * portfolio_value
            key = f"var_{int(cl * 100)}"
            result[f"{key}_pct"] = var_pct
            result[f"{key}_amount"] = var_amount

            # 检查超限
            limit = self.VAR_95_LIMIT_PCT if cl == 0.95 else self.VAR_99_LIMIT_PCT
            breach = var_pct < limit
            result[f"{key}_breach"] = breach
            result[f"{key}_limit"] = limit

            if breach:
                if cl == 0.95:
                    actions.append("95% VaR 超限: 减仓 10%")
                else:
                    actions.append("99% VaR 超限: 减仓 20% + 加对冲")

        result["actions"] = actions
        result["any_breach"] = any(result.get(f"var_{int(cl * 100)}_breach", False) for cl in confidence_levels)

        if result["any_breach"]:
            self._log_event(result)

        return result

    def calculate_var_from_positions(
        self, positions: list[dict[str, Any]], returns_matrix: dict[str, list[float]], portfolio_value: float
    ) -> dict[str, Any]:
        """从持仓明细和各标的收益率序列计算组合 VaR

        Args:
            positions: 持仓列表 [{"code": "600276", "weight": 0.10, ...}, ...]
            returns_matrix: 各标的日收益率 {"600276": [0.01, -0.02, ...], ...}
            portfolio_value: 组合净值

        Returns:
            VaR 计算结果
        """
        # 构建组合日收益率序列
        dates = None
        for _symbol, rets in returns_matrix.items():
            if dates is None or len(rets) < len(dates):
                dates = list(range(len(rets)))

        portfolio_returns: list[float] = []
        for i in dates:  # type: ignore
            daily_ret = 0.0
            for pos in positions:
                symbol = pos.get("code", "")
                weight = pos.get("weight", 0)
                rets = returns_matrix.get(symbol, [])
                if i < len(rets):
                    daily_ret += weight * rets[i]
            portfolio_returns.append(daily_ret)

        return self.calculate_var(portfolio_returns, portfolio_value)

    def execute_breach_response(self, var_type: str) -> dict[str, Any]:
        """执行 VaR 超限响应

        Args:
            var_type: "var_95" 或 "var_99"

        Returns:
            响应动作清单
        """
        actions: list[dict] = []

        if var_type == "var_95":
            actions.append(
                {
                    "action": "reduce_position",
                    "pct": 0.10,
                    "reason": "95% VaR 超限",
                    "status": "pending_execute",
                }
            )
        elif var_type == "var_99":
            actions.extend(
                [
                    {
                        "action": "reduce_position",
                        "pct": 0.20,
                        "reason": "99% VaR 超限",
                        "status": "pending_execute",
                    },
                    {
                        "action": "increase_hedge",
                        "reason": "99% VaR 超限, 加大对冲",
                        "status": "pending_execute",
                    },
                ]
            )

        result = {
            "executed": True,
            "timestamp": datetime.now().isoformat(),
            "var_type": var_type,
            "actions": actions,
            "note": "动作清单已生成, 实际执行需对接交易接口",
        }

        logger.warning(f"⚠️ VaR 超限响应: {var_type}, 动作数={len(actions)}")
        return result

    def _empty_result(self, portfolio_value: float) -> dict[str, Any]:
        """空结果"""
        return {
            "timestamp": datetime.now().isoformat(),
            "method": "historical_simulation",
            "lookback_days": 0,
            "portfolio_value": portfolio_value,
            "var_95_pct": 0,
            "var_95_amount": 0,
            "var_95_breach": False,
            "var_99_pct": 0,
            "var_99_amount": 0,
            "var_99_breach": False,
            "actions": [],
            "any_breach": False,
            "error": "insufficient_data",
        }

    def _log_event(self, event: dict) -> None:
        """记录 VaR 事件"""
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as e:
            logger.error(f"写入 VaR 日志失败: {e}")

    def get_event_history(self, days: int = 30) -> list[dict]:
        """获取最近 N 天的 VaR 事件历史"""
        if not LOG_FILE.exists():
            return []

        records: list[dict] = []
        cutoff = datetime.now().timestamp() - days * 86400

        try:
            with open(LOG_FILE, encoding="utf-8") as f:
                for line in f:
                    try:
                        record = json.loads(line.strip())
                        ts = record.get("timestamp", "")
                        if ts:
                            dt = datetime.fromisoformat(ts)
                            if dt.timestamp() >= cutoff:
                                records.append(record)
                    except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError):
                        continue
        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError):
            pass

        return records


# ============================================================
# CLI 入口
# ============================================================
if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(description="VaR 风险价值监控")
    parser.add_argument("--portfolio", type=float, default=5_000_000, help="组合净值")
    parser.add_argument("--simulate", action="store_true", help="使用模拟数据测试")
    args = parser.parse_args()

    vm = VaRMonitor()

    if args.simulate:
        # 生成模拟收益率 (均值 0.0003, 标准差 0.012)
        np.random.seed(42)
        returns = np.random.normal(0.0003, 0.012, 252).tolist()
        result = vm.calculate_var(returns, args.portfolio)
        logger.info(json.dumps(result, ensure_ascii=False, indent=2))

        if result.get("any_breach"):
            logger.info("\n⚠️ VaR 超限!")
            for a in result["actions"]:
                logger.info(f"  - {a}")
        else:
            logger.info("\n✅ VaR 在限额内")

    logger.info(f"\n历史模拟法窗口: {vm.lookback_days} 日")
    logger.info(f"95% VaR 限额: {vm.VAR_95_LIMIT_PCT:.0%} ({vm.VAR_95_LIMIT_PCT * args.portfolio:.0f} 元)")
    logger.info(f"99% VaR 限额: {vm.VAR_99_LIMIT_PCT:.0%} ({vm.VAR_99_LIMIT_PCT * args.portfolio:.0f} 元)")
