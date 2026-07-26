# -*- coding: utf-8 -*-
"""
三级熔断协议 (Kill Switch Protocol)
==================================

对冲基金视角风控核心:
    L1 一级警戒: 保证金占用率达50% → 停止开仓, 进入防守模式
    L2 二级熔断: 保证金占用率达75% → 强平深虚值期权空头, 释放流动性
    L3 三级互盲: 极端Margin Call → 变现10%红利ETF, 跨品种清算注入

与 portfolio.yaml kill_switch 配置对齐。

用法:
    from utils.kill_switch import KillSwitch
    ks = KillSwitch()
    status = ks.check_margin_status()
    if status["level"] >= 1:
        ks.execute_kill_switch(status["level"])
"""
from __future__ import annotations

import os
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

import yaml

logger = logging.getLogger("kill_switch")

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "configs" / "portfolio.yaml"
KILL_SWITCH_LOG = BASE_DIR / "logs" / "kill_switch_events.jsonl"


class KillSwitch:
    """三级熔断协议

    支持两种使用模式:
        1. 独立模式（CLI）: 从环境变量读取模拟保证金数据
        2. 集成模式: 外部调用方传入真实 margin_usage 参数
    """

    def __init__(self, config_path: Optional[Path] = None,
                 margin_limit: float = 0.50):
        """
        Args:
            config_path: 配置文件路径
            margin_limit: 兼容 alpha_hedge_engine 传入的保证金限额（仅存储，不改变熔断阈值）
        """
        self.config_path = config_path or CONFIG_PATH
        self.margin_limit = margin_limit
        self.config = self._load_config()
        self._broker_callback = None  # 实盘执行回调函数
        KILL_SWITCH_LOG.parent.mkdir(parents=True, exist_ok=True)

    def _load_config(self) -> Dict:
        """加载 kill_switch 配置"""
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
            return cfg.get("kill_switch", {})
        except Exception as e:
            logger.error(f"加载配置失败: {e}")
            return {}

    def _get_margin_status(self) -> Dict:
        """获取保证金占用情况

        优先级:
            1. 环境变量 KILL_SWITCH_MARGIN_RATIO (用于测试/模拟)
            2. 环境变量 KILL_SWITCH_SIM_MODE=l1/l2/l3 (模拟熔断场景)
            3. 读取 config/positions.json 估算真实保证金占用 (v8.6.1 修复)
            4. 保守默认值 0.50 (L1 阈值, 持仓文件不存在时使用)

        Returns:
            {
                "total_margin": ...,
                "available_margin": ...,
                "margin_usage_ratio": float,
                "margin_call": bool,
                "extreme_margin_call": bool
            }
        """
        # 优先从环境变量读取 (用于模拟不同场景)
        sim_mode = os.environ.get("KILL_SWITCH_SIM_MODE", "").lower()
        env_ratio = os.environ.get("KILL_SWITCH_MARGIN_RATIO")

        if env_ratio is not None:
            try:
                ratio = float(env_ratio)
                ratio = max(0.0, min(1.0, ratio))
            except ValueError:
                ratio = 0.20
        elif sim_mode == "l1":
            # 模拟 L1 触发场景 (保证金占用 50%+)
            ratio = 0.55
        elif sim_mode == "l2":
            # 模拟 L2 熔断场景 (保证金占用 75%+)
            ratio = 0.78
        elif sim_mode == "l3":
            # 模拟 L3 极端场景
            ratio = 0.95
        else:
            # v8.6.1 修复: 读取 config/positions.json 估算真实保证金占用
            ratio = self._estimate_margin_from_positions()

        total_margin = 5_000_000  # 500万账户总保证金
        used_margin = total_margin * ratio
        available_margin = total_margin - used_margin

        return {
            "total_margin": total_margin,
            "available_margin": available_margin,
            "margin_usage_ratio": ratio,
            "margin_call": ratio >= 0.90,
            "extreme_margin_call": ratio >= 0.95,
        }

    def _estimate_margin_from_positions(self) -> float:
        """从 config/positions.json 读取持仓估算保证金占用率 (v8.6.1 修复)

        v8.6.4 修复: 股票/ETF 是全额交易, 不应计入保证金占用.
        只有期货/期权才需要保证金.

        计算逻辑:
            - type=STOCK/ETF: 不计入保证金占用 (全额交易)
            - type=FUTURE: 按合约价值的 12% 估算保证金
            - type=OPTION: 按权利金价值的 100% 估算保证金

        Returns:
            保证金占用率 (0.0-1.0), 文件不存在时返回保守值 0.50
        """
        import json
        from pathlib import Path

        project_root = Path(__file__).resolve().parent.parent
        positions_file = project_root / "config" / "positions.json"

        if not positions_file.exists():
            logger.warning(
                f"持仓文件不存在: {positions_file}, 使用保守保证金占用率 0.50"
            )
            return 0.50

        try:
            with open(positions_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            total_capital = float(data.get("meta", {}).get("total_capital", 5_000_000))
            hedge_capital = float(data.get("meta", {}).get("hedge_capital", 2_000_000))

            positions = data.get("positions", {})
            hedge_positions = data.get("hedge_positions", {})
            budget_summary = hedge_positions.get("budget_summary", {})

            total_position_value = 0.0
            estimated_margin_usage = 0.0

            # v8.6.7 CRITICAL FIX (2026-07-26): 期权买方模式下, 权利金已现金扣减,
            # 不构成保证金占用. 此前将 budget_summary.usage_pct (预算消耗进度)
            # 误当作 margin_usage_ratio, 导致每次启动都触发 L2 熔断.
            #
            # 概念区分:
            #   - budget_summary.usage_pct = 已花权利金 / 对冲预算 (预算消耗进度, 正常 50-90%)
            #   - margin_usage_ratio       = 实际保证金占用 / 总资金 (风险指标, ≥75% 才熔断)
            #
            # 纯期权对冲模式 (OPTIONS_ONLY) 下, 买方不付保证金, 仅卖方才需保证金.
            # 因此遇到 budget_summary.usage_pct 时, 应跳过此分支, 落到下方真实持仓估算.
            hedge_mode = data.get("meta", {}).get("hedge_mode", "")
            if hedge_mode == "OPTIONS_ONLY" and budget_summary:
                logger.info(
                    "[KillSwitch] OPTIONS_ONLY 模式: 预算消耗 %.1f%% (非保证金占用), 跳过预算估算, 落入实际持仓估算",
                    float(budget_summary.get("usage_pct", 0.0)),
                )
                # 不返回, 落到下方真实持仓循环 (OPTIONS_ONLY 通常无 FUTURE 持仓)
            elif budget_summary:
                # 非纯期权模式 (含期货): 尝试用预算汇总估算
                usage_pct = budget_summary.get("usage_pct")
                if usage_pct is not None:
                    try:
                        ratio = float(usage_pct) / 100.0
                        ratio = max(0.0, min(1.0, ratio))
                        logger.info(
                            "[KillSwitch] 对冲预算估算保证金占用: usage_pct=%.1f%%, ratio=%.1f%%",
                            float(usage_pct), ratio * 100,
                        )
                        return ratio
                    except (TypeError, ValueError):
                        pass

                total_put_premium = budget_summary.get("total_put_premium")
                total_hedge_capital = budget_summary.get("total_hedge_capital", hedge_capital)
                if total_put_premium is not None and total_hedge_capital:
                    try:
                        ratio = float(total_put_premium) / float(total_hedge_capital)
                        ratio = max(0.0, min(1.0, ratio))
                        logger.info(
                            "[KillSwitch] 对冲预算估算保证金占用: total_put_premium=¥%,.0f, total_hedge_capital=¥%,.0f, ratio=%.1f%%",
                            float(total_put_premium), float(total_hedge_capital), ratio * 100,
                        )
                        return ratio
                    except (TypeError, ValueError):
                        pass

            for code, pos in positions.items():
                amount = pos.get("amount", 0)
                pos_type = pos.get("type", "").upper()

                if amount and isinstance(amount, (int, float)):
                    amount_val = float(amount)
                    total_position_value += amount_val

                    if pos_type == "FUTURE":
                        estimated_margin_usage += amount_val * 0.12
                    elif pos_type == "OPTION":
                        estimated_margin_usage += amount_val

            if total_capital <= 0:
                logger.warning("total_capital <= 0, 使用保守保证金占用率 0.50")
                return 0.50

            ratio = estimated_margin_usage / hedge_capital if hedge_capital > 0 else 0.0
            ratio = max(0.0, min(1.0, ratio))

            logger.info(
                f"[KillSwitch] 持仓估算保证金: "
                f"总持仓市值=¥{total_position_value:,.0f}, "
                f"估算保证金占用=¥{estimated_margin_usage:,.0f}, "
                f"对冲资本=¥{hedge_capital:,.0f}, "
                f"占用率={ratio:.1%}"
            )
            return ratio

        except Exception as e:
            logger.error(f"读取持仓文件估算保证金失败: {e}, 使用保守值 0.50")
            return 0.50

    def set_broker_callback(self, callback) -> None:
        """注册实盘执行回调函数

        当回调存在时, execute_kill_switch 将通过回调执行真实交易操作,
        而非抛出 RuntimeError.

        Args:
            callback: 可调用对象, 签名为 callback(level: int, actions: list) -> Dict
        """
        self._broker_callback = callback

    def check_margin_status(self, margin_usage: Optional[float] = None) -> Dict:
        """检查保证金状态, 判断熔断级别

        Args:
            margin_usage: 外部传入的真实保证金占用率(0-1).
                          若为 None, 则从 _get_margin_status() 获取(环境变量/模拟).

        Returns:
            {
                "timestamp": "...",
                "margin_usage_ratio": 0.60,
                "margin_call": false,
                "level": 1,  # 0=正常, 1=一级, 2=二级, 3=三级
                "level_name": "一级警戒线",
                "actions": [...],
                "auto_execute": true,
                "can_trade": true,   # level < 2 时为 True
                "can_open": true,    # level == 0 时为 True
                "action": "..."      # 首条 action 文本, 兼容 alpha_hedge_engine
            }
        """
        if margin_usage is not None:
            ratio = max(0.0, min(1.0, float(margin_usage)))
            margin = {
                "total_margin": 5_000_000,
                "available_margin": 5_000_000 * (1 - ratio),
                "margin_usage_ratio": ratio,
                "margin_call": ratio >= 0.90,
                "extreme_margin_call": ratio >= 0.95,
            }
        else:
            # 修复 BUG-K1: 生产环境 fail-closed, 数据不可用时视为满仓熔断
            trading_env = os.environ.get("TRADING_ENV", "dev").lower()
            if trading_env == "production":
                logger.critical(
                    "保证金数据不可用 (未传入 margin_usage 且券商API未对接)! "
                    "Kill Switch 进入 FAIL-CLOSED 模式, 阻止一切交易"
                )
                return {
                    "timestamp": datetime.now().isoformat(),
                    "margin_usage_ratio": 1.0,
                    "margin_call": True,
                    "extreme_margin_call": True,
                    "level": 3,
                    "level_name": "数据不可用-强制熔断",
                    "actions": ["数据不可用, 强制停止一切交易"],
                    "auto_execute": True,
                    "can_trade": False,
                    "can_open": False,
                    "action": "FAIL_CLOSED_DATA_UNAVAILABLE",
                }
            # 开发/测试环境: 使用模拟值
            margin = self._get_margin_status()
            ratio = margin.get("margin_usage_ratio", 0)

        extreme_call = margin.get("extreme_margin_call", False)

        level = 0
        level_name = "正常"
        actions = []
        auto_execute = False

        if extreme_call:
            # 三级互盲机制
            level = 3
            level_name = self.config.get("level_3", {}).get("name", "三级互盲机制")
            actions = self.config.get("level_3", {}).get("action", [])
            auto_execute = self.config.get("level_3", {}).get("auto_execute", True)
        elif ratio >= 0.75:
            # 二级熔断线
            level = 2
            level_name = self.config.get("level_2", {}).get("name", "二级熔断线")
            actions = self.config.get("level_2", {}).get("action", [])
            auto_execute = self.config.get("level_2", {}).get("auto_execute", True)
        elif ratio >= 0.50:
            # 一级警戒线
            level = 1
            level_name = self.config.get("level_1", {}).get("name", "一级警戒线")
            actions = self.config.get("level_1", {}).get("action", [])
            auto_execute = self.config.get("level_1", {}).get("auto_execute", True)

        # 兼容字段: can_trade / can_open / action
        can_trade = level < 2   # L0/L1 可交易(但不可开仓), L2+ 不可交易
        can_open = level == 0  # 仅正常状态可开仓
        action_str = actions[0] if actions else ("正常" if level == 0 else f"L{level}熔断")

        result = {
            "timestamp": datetime.now().isoformat(),
            "margin_usage_ratio": ratio,
            "margin_call": margin.get("margin_call", False),
            "extreme_margin_call": extreme_call,
            "level": level,
            "level_name": level_name,
            "actions": actions,
            "auto_execute": auto_execute,
            "can_trade": can_trade,
            "can_open": can_open,
            "action": action_str,
        }

        if level > 0:
            self._log_event(result)

        return result

    def _log_event(self, event: Dict) -> None:
        """记录熔断事件"""
        try:
            with open(KILL_SWITCH_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error(f"写入熔断日志失败: {e}")

    def execute_kill_switch(self, level: int) -> Dict:
        """执行熔断协议

        Args:
            level: 熔断级别 (1/2/3)

        Returns:
            {
                "executed": true,
                "level": 2,
                "actions_taken": [...],
            }

        Raises:
            RuntimeError: 当未注册 broker_callback 时触发熔断级别 >= 1,
                          防止在无真实执行通道下静默通过。
        """
        if level not in (1, 2, 3):
            return {"executed": False, "reason": "invalid_level"}

        level_cfg = self.config.get(f"level_{level}", {})
        actions = level_cfg.get("action", [])

        actions_taken = []

        if level == 1:
            # L1: 切断开仓权限, 进入防守模式
            actions_taken.append({
                "action": "disable_new_positions",
                "status": "executed",
                "note": "中控切断所有新开仓权限",
            })
            actions_taken.append({
                "action": "enter_defensive_mode",
                "status": "executed",
                "note": "进入'只平仓不计数'防守模式",
            })

        elif level == 2:
            # L2: 强平深虚值期权空头
            actions_taken.append({
                "action": "force_close_deep_otm_short",
                "status": "executed",
                "note": "中控强平最深虚值期权空头",
                "positions_closed": "deepest_otm_short_calls",
            })
            actions_taken.append({
                "action": "release_liquidity",
                "status": "executed",
                "note": "瞬间释放账户流动性",
            })

        elif level == 3:
            # L3: 变现10%红利ETF, 跨品种注入
            source_etfs = level_cfg.get("source_etfs", ["512890", "515180"])
            actions_taken.append({
                "action": "liquidate_red_etf",
                "status": "executed",
                "note": f"日内闪电变现 10% 红利ETF: {source_etfs}",
                "amount_liquidated": "10% of source_etfs",
            })
            actions_taken.append({
                "action": "cross_asset_inject",
                "status": "executed",
                "note": "跨品种清算注入期权账户",
            })

        # 修复 BUG-K3: callback 失败时返回 executed=False, 而非静默通过
        # 先检查执行通道是否可用
        if self._broker_callback is None:
            # fail-fast: 无实盘执行通道时, 不允许静默通过
            raise RuntimeError(
                f"Kill Switch L{level} 已触发但未注册 broker_callback. "
                f"请先调用 ks.set_broker_callback(callback) 注册实盘执行函数. "
                f"拒绝在无执行通道下静默通过熔断协议."
            )

        # 执行真实交易操作
        try:
            broker_result = self._broker_callback(level, actions)
            actions_taken.append({
                "action": "broker_callback_executed",
                "status": "executed",
                "detail": broker_result,
            })
            executed = True
        except Exception as e:
            logger.critical(
                f"Kill Switch L{level} broker callback 执行失败! "
                f"熔断协议未真正执行: {e}"
            )
            actions_taken.append({
                "action": "broker_callback_failed",
                "status": "failed",
                "error": str(e),
            })
            # 关键修复: callback 失败时返回 executed=False
            return {
                "executed": False,
                "timestamp": datetime.now().isoformat(),
                "level": level,
                "level_name": level_cfg.get("name", f"Level {level}"),
                "actions_taken": actions_taken,
                "error": f"broker_callback_failed: {e}",
                "critical_note": "熔断协议未真正执行, 需人工介入!",
            }

        result = {
            "executed": executed,
            "timestamp": datetime.now().isoformat(),
            "level": level,
            "level_name": level_cfg.get("name", f"Level {level}"),
            "actions_taken": actions_taken,
        }

        logger.warning(
            f"⚠️ 执行熔断协议 L{level}: {level_cfg.get('name', '')}, "
            f"executed={executed}, 动作数={len(actions_taken)}"
        )

        return result

    def check_concentration(self, positions: Dict) -> Dict:
        """检查持仓集中度

        单票集中度风控阈值:
            L1 预警:  单票 >= 25% → 禁止加仓
            L2 熔断:  单票 >= 35% → 禁止开仓
            L3 强平:  单票 >= 50% → 强制减仓

        Args:
            positions: {code: {'market_value': float, ...}} 或 {code: float}

        Returns:
            {
                "level": "OK" / "L1" / "L2" / "L3",
                "max_concentration": float,
                "max_concentration_code": str,
                "action": str,
            }
        """
        CONCENTRATION_L1 = 0.25
        CONCENTRATION_L2 = 0.35
        CONCENTRATION_L3 = 0.50

        # 计算总市值
        total_value = 0.0
        pos_values = {}
        for code, pos in positions.items():
            if isinstance(pos, dict):
                mv = pos.get('market_value', pos.get('est_market_value', 0))
            else:
                mv = float(pos)
            pos_values[code] = mv
            total_value += mv

        if total_value <= 0:
            return {"level": "OK", "max_concentration": 0,
                    "max_concentration_code": "", "action": "无持仓"}

        # 找最大集中度
        max_code = max(pos_values, key=pos_values.get)
        max_conc = pos_values[max_code] / total_value

        if max_conc >= CONCENTRATION_L3:
            level = "L3"
            action = f"单票 {max_code} 集中度 {max_conc:.1%} >= 50%, 强制减仓"
        elif max_conc >= CONCENTRATION_L2:
            level = "L2"
            action = f"单票 {max_code} 集中度 {max_conc:.1%} >= 35%, 禁止开仓"
        elif max_conc >= CONCENTRATION_L1:
            level = "L1"
            action = f"单票 {max_code} 集中度 {max_conc:.1%} >= 25%, 禁止加仓"
        else:
            level = "OK"
            action = "正常"

        return {
            "level": level,
            "max_concentration": round(max_conc, 4),
            "max_concentration_code": max_code,
            "action": action,
        }

    def get_event_history(self, days: int = 30) -> List[Dict]:
        """获取最近 N 天的熔断事件历史

        Args:
            days: 回看天数

        Returns:
            事件列表
        """
        if not KILL_SWITCH_LOG.exists():
            return []

        records = []
        cutoff = datetime.now().timestamp() - days * 86400

        try:
            with open(KILL_SWITCH_LOG, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        record = json.loads(line.strip())
                        ts = record.get("timestamp", "")
                        if ts:
                            dt = datetime.fromisoformat(ts)
                            if dt.timestamp() >= cutoff:
                                records.append(record)
                    except Exception:
                        continue
        except Exception:
            pass

        return records


# ============================================================
# CLI 入口
# ============================================================
if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(description="三级熔断协议")
    parser.add_argument("--check", action="store_true", help="检查保证金状态")
    parser.add_argument("--execute", type=int, choices=[1, 2, 3], help="执行熔断")
    parser.add_argument("--history", type=int, default=30, help="查看历史(天)")
    args = parser.parse_args()

    ks = KillSwitch()

    if args.check or (not args.execute and not args.history):
        status = ks.check_margin_status()
        print(json.dumps(status, ensure_ascii=False, indent=2))
        if status["level"] > 0:
            print(f"\n⚠️ 熔断级别: L{status['level']} - {status['level_name']}")
            for a in status["actions"]:
                print(f"  - {a}")

    if args.execute:
        result = ks.execute_kill_switch(args.execute)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    if args.history:
        history = ks.get_event_history(args.history)
        print(f"\n最近 {args.history} 天熔断事件: {len(history)} 次")
        for r in history:
            print(
                f"  {r.get('timestamp', 'N/A')} - L{r.get('level', 0)} "
                f"{r.get('level_name', '')}"
            )
