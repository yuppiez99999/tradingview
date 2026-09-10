"""风控守卫 Guard 9 — 相关性对冲 mixin

审计 item 11 (2026-09-10) 拆自 ``utils/risk_guard_integrator.py``: **零行为变更**,
仅搬移方法体与所属分隔注释, 逻辑/字段/落盘路径/日志文本逐字节保持原样。

契约: 路径与 logger 一律经 ``plan_context.XXX`` 属性式访问; 重量级引擎依赖保持方法体内惰性 import。
"""

from __future__ import annotations

import json
from typing import Any

from utils.risk.guards import plan_context
from utils.risk.guards.plan_context import PlanContextMixin


class CorrelationGuardMixin(PlanContextMixin):
    # ============================================================
    # Guard 9: 相关性对冲 (P1-K 新增, v8.6.6)
    # ============================================================
    def guard_correlation_hedge(self, pnl_report: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
        """相关性对冲 Guard - 集成 CorrelationHedger 到 EOD 链

        触发条件 (CorrelationHedger.compute_hedge):
            1. avg_corr > 0.85 且 jump > 0.15
            2. avg_corr > 0.95 (极端趋同)

        响应动作:
            - 生成黄金 ETF (518880) 买入订单
            - 生成国债逆回购 (GC001) 订单
            - 写入 plan['correlation_hedge_orders']
        """
        try:
            # 复用 v8.3_institutional 的 CorrelationHedger
            import sys as _sys

            _v83_src = plan_context.BASE_DIR / "v8.3_institutional" / "src"
            if str(_v83_src) not in _sys.path:
                _sys.path.insert(0, str(_v83_src))
            from hedging.correlation_hedger import CorrelationHedger
        except ImportError as e:
            self._log(f"[相关性对冲] CorrelationHedger 导入失败, 跳过: {e}")
            plan.setdefault("risk_guard", {})["correlation_hedge"] = {
                "status": "SKIP",
                "reason": "import_failed",
            }
            return plan

        try:
            # 构建持仓标的收益率 DataFrame
            returns_df = self._build_position_returns(pnl_report, lookback_days=60)
            if returns_df is None or returns_df.empty:
                self._log("[相关性对冲] 无可用收益率数据, 跳过")
                plan.setdefault("risk_guard", {})["correlation_hedge"] = {
                    "action": "NO_DATA",
                    "reason": "insufficient_returns_history",
                }
                return plan

            # 获取组合市值 (v8.6.6 修复: 使用兼容层支持两种报告格式)
            pnl_summary = self._extract_summary(pnl_report)
            portfolio_value = float(pnl_summary.get("total_market_value", 0)) or self.total_capital

            # 计算相关性对冲
            hedger = CorrelationHedger()
            hedge_result = hedger.compute_hedge(returns_df, portfolio_value)

            plan.setdefault("risk_guard", {})["correlation_hedge"] = {
                "action": hedge_result.get("action", "UNKNOWN"),
                "avg_corr": float(hedge_result.get("avg_corr", 0)),
                "baseline_corr": float(hedge_result.get("baseline_corr", 0)),
                "jump": float(hedge_result.get("jump", 0)),
            }

            if hedge_result.get("action") == "SAFE_HAVEN_ALLOC":
                # 生成避险资产配置订单
                hedge_orders = self._build_safe_haven_orders(hedge_result)
                plan["correlation_hedge_orders"] = hedge_orders
                plan.setdefault("risk_guard", {})["correlation_hedge"]["gold_weight"] = float(
                    hedge_result.get("gold_weight", 0)
                )
                plan.setdefault("risk_guard", {})["correlation_hedge"]["repo_weight"] = float(
                    hedge_result.get("repo_weight", 0)
                )
                self._log(
                    f"[相关性对冲] 触发避险配置: ρ̄={hedge_result.get('avg_corr', 0):.3f}, "
                    f"黄金ETF {hedge_result.get('gold_weight', 0):.2%}, "
                    f"逆回购 {hedge_result.get('repo_weight', 0):.2%}"
                )
            else:
                self._log(f"[相关性对冲] 无需对冲: {hedge_result.get('reason', '条件未满足')}")
        except (
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            OSError,
            RuntimeError,
        ) as e:
            self._log(f"[相关性对冲] 执行崩溃: {e}")
            plan.setdefault("risk_guard", {})["correlation_hedge_error"] = str(e)
            # 相关性对冲崩溃不阻断主流程 (仅记录错误)

        return plan

    def _build_position_returns(self, pnl_report: dict[str, Any], lookback_days: int = 60) -> Any:
        """从历史 daily_pnl_report 构建持仓标的收益率 DataFrame

        适配实际报告结构 (v8.6.6 修复):
            {
                "date": "2026-07-24",
                "summary": {"total_pnl": ..., "total_market_value": ..., "total_cost": ...},
                "positions": [{"code": "600519.SH", "name": "...", "pnl": 500.3, "market_value": 150000}, ...]
            }

        Args:
            pnl_report: 当日盈亏报告 (用于提取持仓代码)
            lookback_days: 回看天数

        Returns:
            pandas DataFrame (T×N 收益率) 或 None
        """
        try:
            import pandas as pd

            # v8.6.6 修复: 使用兼容层提取持仓 (支持完整格式和简化格式)
            positions = self._extract_positions(pnl_report)
            symbols = [
                str(p.get("code") or p.get("symbol") or "")
                for p in positions
                if isinstance(p, dict)
            ]
            symbols = [s for s in symbols if s]

            if not symbols:
                self._log("[相关性对冲] 当日持仓为空, 无法构建收益率序列")
                return None

            # 加载历史报告
            report_files = sorted(
            plan_context.REPORTS_DIR.glob("daily_pnl_report_*.json")
        )[-lookback_days:]
            if len(report_files) < 10:
                self._log(f"[相关性对冲] 历史报告不足 10 份 (实际 {len(report_files)}), 跳过")
                return None

            # 构建收益率序列 — 显式注解消除 dict comprehension 的 [assignment] 漂移
            returns_data: dict[str, list[float]] = {sym: [] for sym in symbols}
            valid_days = 0
            for rf in report_files:
                try:
                    with open(rf, encoding="utf-8") as f:
                        data = json.load(f)
                    # v8.6.6 修复: 使用兼容层提取历史持仓
                    pos_list = self._extract_positions(data)
                    if not isinstance(pos_list, list):
                        continue

                    # 构建 {code: daily_return} 映射
                    day_returns = {}
                    for pos in pos_list:
                        if not isinstance(pos, dict):
                            continue
                        sym = pos.get("code", pos.get("symbol", ""))
                        if not sym or sym not in symbols:
                            continue
                        # v8.6.6 修复: 优先使用 daily_pnl_pct (完整格式, 百分比)
                        daily_pnl_pct = pos.get("daily_pnl_pct")
                        if daily_pnl_pct is not None:
                            daily_ret = float(daily_pnl_pct) / 100.0
                        else:
                            # fallback: pnl / market_value (简化格式)
                            pnl = float(pos.get("pnl", 0))
                            market_value = float(pos.get("market_value", 0))
                            daily_ret = pnl / market_value if market_value > 0 else 0.0
                        day_returns[sym] = daily_ret

                    # 所有标的都填充 (缺失的填 0)
                    for sym in symbols:
                        returns_data[sym].append(day_returns.get(sym, 0.0))
                    valid_days += 1
                except (
                    ValueError,
                    KeyError,
                    TypeError,
                    AttributeError,
                    OSError,
                    RuntimeError,
                ):
                    continue

            if valid_days < 10:
                self._log(f"[相关性对冲] 有效历史数据不足 ({valid_days} 天), 跳过")
                return None

            df = pd.DataFrame(returns_data)
            self._log(f"[相关性对冲] 构建收益率矩阵: {df.shape[0]} 天 × {df.shape[1]} 标的")
            return df

        except ImportError:
            self._log("[相关性对冲] pandas 未安装, 无法构建收益率矩阵")
            return None
        except (
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            OSError,
            RuntimeError,
        ) as e:
            self._log(f"[相关性对冲] 构建收益率失败: {e}")
            return None

    def _build_safe_haven_orders(self, hedge_result: dict[str, Any]) -> list[Any]:
        """生成避险资产配置订单

        Args:
            hedge_result: CorrelationHedger.compute_hedge() 返回值

        Returns:
            订单列表 [{symbol, direction, weight, value, ...}]
        """
        orders = []
        gold_weight = float(hedge_result.get("gold_weight", 0))
        repo_weight = float(hedge_result.get("repo_weight", 0))
        gold_value = float(hedge_result.get("gold_value", 0))
        repo_value = float(hedge_result.get("repo_value", 0))

        if gold_weight > 0:
            orders.append(
                {
                    "symbol": hedge_result.get("gold_etf", "518880"),
                    "direction": "BUY",
                    "order_type": "SAFE_HAVEN",
                    "weight": gold_weight,
                    "est_amount": gold_value,
                    "reason": "correlation_hedge_gold",
                    "note": f"相关性对冲: 黄金ETF {gold_weight:.2%}",
                }
            )

        if repo_weight > 0:
            orders.append(
                {
                    "symbol": hedge_result.get("repo_symbol", "GC001"),
                    "direction": "BUY",
                    "order_type": "SAFE_HAVEN",
                    "weight": repo_weight,
                    "est_amount": repo_value,
                    "reason": "correlation_hedge_repo",
                    "note": f"相关性对冲: 国债逆回购 {repo_weight:.2%}",
                }
            )

        return orders
