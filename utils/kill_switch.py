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
    """三级熔断协议"""

    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path or CONFIG_PATH
        self.config = self._load_config()
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

        实际环境应通过券商API获取, 此处提供模拟接口

        优先级:
            1. 环境变量 KILL_SWITCH_MARGIN_RATIO (用于测试/模拟)
            2. 环境变量 KILL_SWITCH_SIM_MODE=normal 强制返回正常状态
            3. 默认模拟值 (margin_usage_ratio=0.20, 低于 L1 阈值)

        Returns:
            {
                "total_margin": ...,
                "available_margin": ...,
                "margin_usage_ratio": 0.20,
                "margin_call": false,
                "extreme_margin_call": false
            }
        """
        # TODO: 对接 QMT/券商 API 获取真实保证金数据
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
            # 默认: 正常运行期模拟值 (20% 占用, 远低于 L1 阈值 50%)
            ratio = 0.20

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

    def check_margin_status(self) -> Dict:
        """检查保证金状态, 判断熔断级别

        Returns:
            {
                "timestamp": "...",
                "margin_usage_ratio": 0.60,
                "margin_call": false,
                "level": 1,  # 0=正常, 1=一级, 2=二级, 3=三级
                "level_name": "一级警戒线",
                "actions": [...],
                "auto_execute": true
            }
        """
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

        result = {
            "timestamp": datetime.now().isoformat(),
            "margin_usage_ratio": ratio,
            "margin_call": margin.get("margin_call", False),
            "extreme_margin_call": extreme_call,
            "level": level,
            "level_name": level_name,
            "actions": actions,
            "auto_execute": auto_execute,
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
                "note": "..."
            }
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
                "note": "API 中控切断所有新开仓权限",
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
                "note": "API 中控强平最深虚值期权空头",
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

        result = {
            "executed": True,
            "timestamp": datetime.now().isoformat(),
            "level": level,
            "level_name": level_cfg.get("name", f"Level {level}"),
            "actions_taken": actions_taken,
            "note": "实际执行需对接 QMT/券商 API, 此处为逻辑框架",
        }

        logger.warning(
            f"⚠️ 执行熔断协议 L{level}: {level_cfg.get('name', '')}, "
            f"动作数={len(actions_taken)}"
        )

        return result

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
