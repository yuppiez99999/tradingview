# -*- coding: utf-8 -*-
"""
2030 清仓协议日程模块 (Liquidation Scheduler)
=============================================

对冲基金视角退出机制:
    Phase 1 (2030-Q3): 锁定科技利润, Delta中性化, TWAP变现
    Phase 2 (2030-11): 红利资产收尾, 持有至最后分红, VWAP变现
    Phase 3 (2030-12): 衍生品归零清盘, 500万本息回归现金池

与 portfolio.yaml liquidation_protocol 配置对齐。

用法:
    from utils.liquidation_scheduler import LiquidationScheduler
    sched = LiquidationScheduler()
    phase = sched.get_current_phase()
    if phase:
        print(phase["actions"])
"""
from __future__ import annotations

import os
import json
import logging
from datetime import datetime, date
from pathlib import Path
from typing import Dict, List, Optional, Any

import yaml

logger = logging.getLogger("liquidation")

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "configs" / "portfolio.yaml"
LOG_FILE = BASE_DIR / "logs" / "liquidation_events.jsonl"


class LiquidationScheduler:
    """2030 清仓协议日程"""

    # 关键时间节点
    PHASE_1_START = date(2030, 7, 1)    # 2030 Q3
    PHASE_2_START = date(2030, 11, 1)
    PHASE_3_START = date(2030, 12, 1)
    FINAL_DATE = date(2030, 12, 31)

    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path or CONFIG_PATH
        self.config = self._load_config()
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

    def _load_config(self) -> Dict:
        """加载清仓协议配置 (P1-Q8: 通过 ConfigManager 统一加载)

        优先级:
            1. 显式传入的 config_path (向后兼容测试场景)
            2. ConfigManager 自动解析 (v8.3 唯一事实源 > configs/ 历史回退)

        Returns:
            liquidation_protocol 配置字典, 加载失败返回空 dict (fail-safe)
        """
        # 路径 1: 调用方显式指定了 config_path (测试场景, 向后兼容)
        if self.config_path != CONFIG_PATH:
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    cfg = yaml.safe_load(f)
                return cfg.get("liquidation_protocol", {}) if isinstance(cfg, dict) else {}
            except Exception as e:
                logger.error(f"加载配置失败 (显式路径 {self.config_path}): {e}")
                return {}

        # 路径 2: 通过 ConfigManager 统一加载 (P1-Q8, 生产路径)
        try:
            from utils.config_manager import get_config
            portfolio_cfg = get_config("portfolio")
            cfg = portfolio_cfg.get("liquidation_protocol", {})
            if cfg:
                return cfg
            # ConfigManager 全部失败, 回退到旧路径 (保底)
            with open(self.config_path, "r", encoding="utf-8") as f:
                fallback_cfg = yaml.safe_load(f)
            return fallback_cfg.get("liquidation_protocol", {}) if isinstance(fallback_cfg, dict) else {}
        except Exception as e:
            logger.error(f"ConfigManager 加载失败, 回退到旧路径: {e}", exc_info=True)
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    cfg = yaml.safe_load(f)
                return cfg.get("liquidation_protocol", {}) if isinstance(cfg, dict) else {}
            except Exception as e2:
                logger.error(f"全部加载路径失败: {e2}")
                return {}

    def get_current_phase(self, today: Optional[date] = None) -> Optional[Dict]:
        """获取当前应执行的清仓阶段

        Args:
            today: 当前日期 (默认今天)

        Returns:
            {
                "phase": 1,
                "name": "锁定科技利润",
                "period": "2030-Q3",
                "actions": [...],
                "target": "...",
                "days_to_next_phase": 92
            }
        """
        today = today or date.today()

        if today >= self.FINAL_DATE:
            return {
                "phase": "complete",
                "name": "清盘完成",
                "period": "2030-12-31",
                "actions": ["专户已完成5年历史使命"],
                "target": "资金已全额回归现金池",
                "days_to_next_phase": 0,
            }

        if today >= self.PHASE_3_START:
            phase_cfg = self.config.get("phase_3", {})
            return {
                "phase": 3,
                "name": phase_cfg.get("name", "账户归零清盘"),
                "period": phase_cfg.get("period", "2030-12"),
                "actions": phase_cfg.get("actions", []),
                "target": phase_cfg.get("target", ""),
                "days_to_next_phase": (self.FINAL_DATE - today).days,
            }

        if today >= self.PHASE_2_START:
            phase_cfg = self.config.get("phase_2", {})
            return {
                "phase": 2,
                "name": phase_cfg.get("name", "红利资产收尾"),
                "period": phase_cfg.get("period", "2030-11"),
                "actions": phase_cfg.get("actions", []),
                "target": phase_cfg.get("target", ""),
                "days_to_next_phase": (self.PHASE_3_START - today).days,
            }

        if today >= self.PHASE_1_START:
            phase_cfg = self.config.get("phase_1", {})
            return {
                "phase": 1,
                "name": phase_cfg.get("name", "锁定科技利润"),
                "period": phase_cfg.get("period", "2030-Q3"),
                "actions": phase_cfg.get("actions", []),
                "target": phase_cfg.get("target", ""),
                "days_to_next_phase": (self.PHASE_2_START - today).days,
            }

        # 尚未进入清仓期
        days_to_phase_1 = (self.PHASE_1_START - today).days
        return {
            "phase": 0,
            "name": "正常运行期",
            "period": f"至今 - {self.PHASE_1_START}",
            "actions": ["正常执行投资策略, Theta/Gamma引擎运行中"],
            "target": "维持正常运行, 累积收益",
            "days_to_next_phase": days_to_phase_1,
        }

    def get_schedule(self) -> List[Dict]:
        """获取完整清仓时间表

        Returns:
            时间表列表
        """
        return [
            {
                "phase": 0,
                "name": "正常运行期",
                "period": f"2026-07 ~ {self.PHASE_1_START}",
                "actions": ["投资策略正常运行"],
            },
            {
                "phase": 1,
                "name": self.config.get("phase_1", {}).get("name", "锁定科技利润"),
                "period": self.config.get("phase_1", {}).get("period", "2030-Q3"),
                "actions": self.config.get("phase_1", {}).get("actions", []),
                "target": self.config.get("phase_1", {}).get("target", ""),
            },
            {
                "phase": 2,
                "name": self.config.get("phase_2", {}).get("name", "红利资产收尾"),
                "period": self.config.get("phase_2", {}).get("period", "2030-11"),
                "actions": self.config.get("phase_2", {}).get("actions", []),
                "target": self.config.get("phase_2", {}).get("target", ""),
            },
            {
                "phase": 3,
                "name": self.config.get("phase_3", {}).get("name", "账户归零清盘"),
                "period": self.config.get("phase_3", {}).get("period", "2030-12"),
                "actions": self.config.get("phase_3", {}).get("actions", []),
                "target": self.config.get("phase_3", {}).get("target", ""),
            },
        ]

    def check_alert(self, days_threshold: int = 30) -> Optional[Dict]:
        """检查是否需要清仓预警

        Args:
            days_threshold: 提前预警天数 (默认30天)

        Returns:
            预警信息 (None 表示无需预警)
        """
        current = self.get_current_phase()

        if current["phase"] == 0:
            # 正常运行期, 检查是否临近 Phase 1
            days_left = current["days_to_next_phase"]
            if days_left <= days_threshold:
                return {
                    "alert": True,
                    "type": "phase_1_approaching",
                    "days_left": days_left,
                    "message": f"距清仓 Phase 1 仅剩 {days_left} 天, 请准备 Delta 中性化",
                    "preparation": [
                        "梳理科技ETF持仓明细",
                        "确认 IF 期货合约可用",
                        "测试 TWAP 算法执行",
                    ],
                }

        elif current["phase"] in (1, 2):
            days_left = current["days_to_next_phase"]
            if days_left <= 7:
                return {
                    "alert": True,
                    "type": f"phase_{current['phase']}_ending",
                    "days_left": days_left,
                    "message": f"Phase {current['phase']} 即将结束, 请准备下一阶段",
                    "next_actions": self.get_current_phase()["actions"],
                }

        return None

    def _log_event(self, event: Dict) -> None:
        """记录清仓事件"""
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error(f"写入清仓日志失败: {e}")


# ============================================================
# CLI 入口
# ============================================================
if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(description="2030 清仓协议日程")
    parser.add_argument("--current", action="store_true", help="查看当前阶段")
    parser.add_argument("--schedule", action="store_true", help="查看完整时间表")
    parser.add_argument("--alert", action="store_true", help="检查预警")
    parser.add_argument(
        "--date", type=str, help="模拟日期 (YYYY-MM-DD)"
    )
    args = parser.parse_args()

    sched = LiquidationScheduler()

    sim_date = None
    if args.date:
        try:
            sim_date = datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            print("日期格式错误, 请用 YYYY-MM-DD")
            exit(1)

    if args.current or (not args.schedule and not args.alert):
        phase = sched.get_current_phase(sim_date)
        print(f"\n=== 当前清仓阶段 ===")
        print(f"Phase: {phase['phase']}")
        print(f"名称: {phase['name']}")
        print(f"周期: {phase['period']}")
        print(f"距下一阶段: {phase['days_to_next_phase']} 天")
        if phase.get("actions"):
            print(f"\n动作:")
            for a in phase["actions"]:
                print(f"  - {a}")
        if phase.get("target"):
            print(f"目标: {phase['target']}")

    if args.schedule:
        print(f"\n=== 完整清仓时间表 ===")
        for s in sched.get_schedule():
            print(f"\nPhase {s['phase']}: {s['name']}")
            print(f"  周期: {s['period']}")
            if s.get("actions"):
                for a in s["actions"]:
                    print(f"  - {a}")

    if args.alert:
        alert = sched.check_alert()
        if alert:
            print(f"\n⚠️ 清仓预警")
            print(f"类型: {alert['type']}")
            print(f"剩余天数: {alert['days_left']}")
            print(f"消息: {alert['message']}")
        else:
            print("\n✅ 无预警, 距下一阶段尚远")
