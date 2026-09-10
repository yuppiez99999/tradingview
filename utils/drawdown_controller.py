"""
组合回撤控制器 (Drawdown Controller)
====================================

基于 v10.0 投资计划手册第四章风控规则实现:
    Level 1 (5%):  预警审查 + 期货对冲比例 40% → 50%
    Level 2 (8%):  股票减仓 20% + 对冲至 60% + 暂停备兑 + 尾部加码
    Level 3 (12%): 股票再减 20%(累计40%) + 对冲至 80% + 量化中性减半
    Level 4 (15%): 股票减仓 60% + 现金 60% + 国债 40% + 退出评估

与 KillSwitch (保证金流动性) 并行运行, 形成双轨风控:
    - KillSwitch: 管保证金占用率 (流动性维度)
    - DrawdownController: 管组合回撤 (风险维度)

用法:
    from utils.drawdown_controller import DrawdownController
    dc = DrawdownController()
    level = dc.check_drawdown(peak_value=5_000_000, current_value=4_600_000)
    if level["level"] >= 2:
        actions = dc.execute_response(level["level"])
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from utils.datetime_utils import now_bj

logger = logging.getLogger("drawdown_controller")

BASE_DIR = Path(__file__).resolve().parent.parent
LOG_FILE = BASE_DIR / "logs" / "drawdown_events.jsonl"


class DrawdownController:
    """组合回撤四级响应控制器"""

    # 回撤阈值 (百分比)
    LEVEL_1_THRESHOLD = 0.05  # 5%: 预警
    LEVEL_2_THRESHOLD = 0.08  # 8%: 一级防御
    LEVEL_3_THRESHOLD = 0.12  # 12%: 二级防御
    LEVEL_4_THRESHOLD = 0.15  # 15%: 极限防御

    def __init__(self):
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

    def check_drawdown(
        self,
        peak_value: float,
        current_value: float,
        high_water_mark: float | None = None,
    ) -> dict[str, Any]:
        """计算当前回撤级别

        Args:
            peak_value: 历史峰值 (或用 high_water_mark)
            current_value: 当前组合净值
            high_water_mark: 历史最高水位标记 (优先于 peak_value)

        Returns:
            {
                "timestamp": "...",
                "peak_value": 5000000,
                "current_value": 4600000,
                "drawdown_amount": -400000,
                "drawdown_pct": -0.08,
                "level": 2,
                "level_name": "一级防御",
                "actions": [...],
                "build_allowed": False,  # L2+ 禁止新开仓
                "spot_reduce_pct": 0.20,  # 需减仓比例
                "hedge_ratio_target": 0.60,  # 对冲比例目标
            }
        """
        ref_peak = high_water_mark if high_water_mark is not None else peak_value
        if ref_peak <= 0:
            # v8.6.13 P0 FIX (2026-08-01 AI 扫描):
            # 原代码 fail-open 返回 level=0 "正常", 数据异常时回撤防护完全失效.
            # 风控模块必须 fail-closed: 数据不可信时按最严重场景处理.
            # 改为返回 Level 4 "极限防御", 强制停止建仓.
            logger.critical(
                "ref_peak <= 0 (peak=%s, high_water_mark=%s), 数据异常, "
                "fail-closed 返回 Level 4 极限防御",
                peak_value,
                high_water_mark,
            )
            return self._build_result(
                peak_value, current_value, 0, 0, "极限防御", level=4
            )

        drawdown_amount = current_value - ref_peak
        drawdown_pct = drawdown_amount / ref_peak if ref_peak > 0 else 0

        # 回撤为正数表示亏损 (current < peak)
        dd_abs = abs(min(drawdown_pct, 0))

        if dd_abs >= self.LEVEL_4_THRESHOLD:
            return self._build_result(
                ref_peak,
                current_value,
                drawdown_amount,
                drawdown_pct,
                "极限防御",
                level=4,
            )
        if dd_abs >= self.LEVEL_3_THRESHOLD:
            return self._build_result(
                ref_peak,
                current_value,
                drawdown_amount,
                drawdown_pct,
                "二级防御",
                level=3,
            )
        if dd_abs >= self.LEVEL_2_THRESHOLD:
            return self._build_result(
                ref_peak,
                current_value,
                drawdown_amount,
                drawdown_pct,
                "一级防御",
                level=2,
            )
        if dd_abs >= self.LEVEL_1_THRESHOLD:
            return self._build_result(
                ref_peak,
                current_value,
                drawdown_amount,
                drawdown_pct,
                "预警审查",
                level=1,
            )
        return self._build_result(
            ref_peak, current_value, drawdown_amount, drawdown_pct, "正常", level=0
        )

    def _build_result(
        self,
        peak: float,
        current: float,
        dd_amount: float,
        dd_pct: float,
        level_name: str,
        level: int = 0,
    ) -> dict[str, Any]:
        """构建回撤检测结果"""
        actions: list[str] = []
        spot_reduce = 0.0
        hedge_ratio = 0.40  # 默认正常对冲比例
        build_allowed = True
        options_selling_allowed = True
        quant_neutral_scale = 1.0  # 量化中性策略缩放因子
        cash_target_pct = 0.26  # 默认现金占比

        if level == 1:
            actions = [
                "生成风控报告, 列出主要亏损来源",
                "人工审查主要亏损标的/策略/行业",
                "期货对冲比例 40% → 50%",
            ]
            hedge_ratio = 0.50
        elif level == 2:
            actions = [
                "股票多头减仓 20% (优先卖出最大亏损标的)",
                "期货对冲比例提升至 60%",
                "暂停备兑看涨卖出 (避免上行风险)",
                "期权尾部保护加码 (权利金 0.25% → 0.5%/季)",
                "现金占比 26% → 35%",
            ]
            spot_reduce = 0.20
            hedge_ratio = 0.60
            build_allowed = False
            options_selling_allowed = False
            cash_target_pct = 0.35
        elif level == 3:
            actions = [
                "股票多头再减仓 20% (累计减 40%)",
                "期货对冲比例提升至 80%",
                "量化中性策略仓位减半",
                "全部期权仅保留尾部看跌 (平仓备兑)",
                "现金占比提升至 45%",
                "触发策略暂停评估: 连续3月回撤超限暂停1月",
            ]
            spot_reduce = 0.40
            hedge_ratio = 0.80
            build_allowed = False
            options_selling_allowed = False
            quant_neutral_scale = 0.5
            cash_target_pct = 0.45
        elif level == 4:
            actions = [
                "转入全面防御模式",
                "股票多头减仓 60% (累计)",
                "期货对冲至 80% 然后逐步平仓",
                "量化中性全部平仓",
                "现金占比达 60%",
                "剩余资金转入短期国债 (40%)",
                "启动退出计划评估",
            ]
            spot_reduce = 0.60
            hedge_ratio = 0.80
            build_allowed = False
            options_selling_allowed = False
            quant_neutral_scale = 0.0
            cash_target_pct = 0.60

        result = {
            "timestamp": now_bj().isoformat(),
            "peak_value": peak,
            "current_value": current,
            "drawdown_amount": dd_amount,
            "drawdown_pct": dd_pct,
            "level": level,
            "level_name": level_name,
            "actions": actions,
            "build_allowed": build_allowed,
            "spot_reduce_pct": spot_reduce,
            "hedge_ratio_target": hedge_ratio,
            "options_selling_allowed": options_selling_allowed,
            "quant_neutral_scale": quant_neutral_scale,
            "cash_target_pct": cash_target_pct,
        }

        if level > 0:
            self._log_event(result)

        return result

    def _log_event(self, event: dict) -> None:
        """记录回撤事件"""
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ) as e:  # P2 模块 fail-safe, 待后续精确化
            logger.error(f"写入回撤日志失败: {e}")

    def execute_response(self, level: int) -> dict[str, Any]:
        """执行回撤响应动作 (返回动作清单, 实际执行需对接交易接口)

        Args:
            level: 回撤级别 (1/2/3/4)

        Returns:
            {
                "executed": True,
                "level": 2,
                "actions_taken": [...],
                "note": "..."
            }
        """
        if level not in (1, 2, 3, 4):
            return {"executed": False, "reason": "invalid_level"}

        actions_taken: list[dict] = []

        if level == 1:
            actions_taken.append(
                {
                    "action": "increase_hedge_ratio",
                    "from": 0.40,
                    "to": 0.50,
                    "status": "pending_execute",
                }
            )
        elif level == 2:
            actions_taken.extend(
                [
                    {
                        "action": "reduce_stock_position",
                        "pct": 0.20,
                        "priority": "max_loss_first",
                        "status": "pending_execute",
                    },
                    {
                        "action": "increase_hedge_ratio",
                        "to": 0.60,
                        "status": "pending_execute",
                    },
                    {
                        "action": "pause_covered_call_selling",
                        "status": "pending_execute",
                    },
                    {
                        "action": "increase_tail_put_budget",
                        "from_pct": 0.0025,
                        "to_pct": 0.005,
                        "frequency": "quarterly",
                        "status": "pending_execute",
                    },
                ]
            )
        elif level == 3:
            actions_taken.extend(
                [
                    {
                        "action": "reduce_stock_position",
                        "pct": 0.40,
                        "cumulative": True,
                        "status": "pending_execute",
                    },
                    {
                        "action": "increase_hedge_ratio",
                        "to": 0.80,
                        "status": "pending_execute",
                    },
                    {"action": "halve_quant_neutral", "status": "pending_execute"},
                    {
                        "action": "close_all_covered_calls_keep_puts",
                        "status": "pending_execute",
                    },
                    {
                        "action": "trigger_strategy_pause_assessment",
                        "status": "pending_execute",
                    },
                ]
            )
        elif level == 4:
            actions_taken.extend(
                [
                    {
                        "action": "reduce_stock_position",
                        "pct": 0.60,
                        "cumulative": True,
                        "status": "pending_execute",
                    },
                    {"action": "close_all_quant_neutral", "status": "pending_execute"},
                    {
                        "action": "move_to_cash",
                        "target_pct": 0.60,
                        "status": "pending_execute",
                    },
                    {
                        "action": "move_to_short_term_bond",
                        "target_pct": 0.40,
                        "status": "pending_execute",
                    },
                    {"action": "launch_exit_assessment", "status": "pending_execute"},
                ]
            )

        result = {
            "executed": True,
            "timestamp": now_bj().isoformat(),
            "level": level,
            "actions_taken": actions_taken,
            "note": "动作清单已生成, 实际执行需对接交易接口",
        }

        logger.warning(f"⚠️ 执行回撤响应 L{level}: 动作数={len(actions_taken)}")
        return result

    def get_event_history(self, days: int = 30) -> list[dict]:
        """获取最近 N 天的回撤事件历史"""
        if not LOG_FILE.exists():
            return []

        records: list[dict] = []
        cutoff = now_bj().timestamp() - days * 86400

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
                    except (
                        ValueError,
                        TypeError,
                        KeyError,
                        AttributeError,
                        RuntimeError,
                        OSError,
                        TimeoutError,
                        ConnectionError,
                    ):  # P2 模块 fail-safe, 待后续精确化
                        continue
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ):  # P2 模块 fail-safe, 待后续精确化
            pass

        return records


# ============================================================
# CLI 入口
# ============================================================
if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(description="组合回撤四级响应控制器")
    parser.add_argument("--peak", type=float, default=5_000_000, help="历史峰值")
    parser.add_argument("--current", type=float, required=True, help="当前净值")
    parser.add_argument(
        "--execute", type=int, choices=[1, 2, 3, 4], help="执行响应动作"
    )
    parser.add_argument("--history", type=int, default=30, help="查看历史(天)")
    args = parser.parse_args()

    dc = DrawdownController()

    if args.execute:
        result = dc.execute_response(args.execute)
        logger.info(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        result = dc.check_drawdown(args.peak, args.current)
        logger.info(json.dumps(result, ensure_ascii=False, indent=2))
        if result["level"] > 0:
            logger.info(f"\n⚠️ 回撤级别: L{result['level']} - {result['level_name']}")
            for a in result["actions"]:
                logger.info(f"  - {a}")

    if args.history:
        history = dc.get_event_history(args.history)
        logger.info(f"\n最近 {args.history} 天回撤事件: {len(history)} 次")
        for r in history:
            logger.info(
                f"  {r.get('timestamp', 'N/A')} - L{r.get('level', 0)} "
                f"{r.get('level_name', '')} 回撤={r.get('drawdown_pct', 0):.2%}"
            )
