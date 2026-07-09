#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模型自优化器：根据历史运行数据自动校准风控阈值和对冲参数
不依赖外部模型，纯本地参数自校准
"""

import json
import math
from datetime import datetime, date
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent
HISTORY_FILE = BASE_DIR / "logs" / "run_history.json"
HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)


class RunRecord:
    """单次运行记录"""

    def __init__(
        self,
        scenario: str,
        trigger_level: str,
        drawdown: float,
        orders_count: int,
        portfolio_value: float,
        vix_level: float,
        daily_drop: float,
        weekly_drop: float,
        futures_ratio: float = 0.0,
        options_ratio: float = 0.0,
        timestamp: Optional[str] = None,
    ):
        self.scenario = scenario
        self.trigger_level = trigger_level
        self.drawdown = drawdown
        self.orders_count = orders_count
        self.portfolio_value = portfolio_value
        self.vix_level = vix_level
        self.daily_drop = daily_drop
        self.weekly_drop = weekly_drop
        self.futures_ratio = futures_ratio
        self.options_ratio = options_ratio
        self.timestamp = timestamp or datetime.now().isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scenario": self.scenario,
            "trigger_level": self.trigger_level,
            "drawdown": self.drawdown,
            "orders_count": self.orders_count,
            "portfolio_value": self.portfolio_value,
            "vix_level": self.vix_level,
            "daily_drop": self.daily_drop,
            "weekly_drop": self.weekly_drop,
            "futures_ratio": self.futures_ratio,
            "options_ratio": self.options_ratio,
            "timestamp": self.timestamp,
        }


class ModelSelfOptimizer:
    """模型自优化器：根据历史运行数据自动校准参数"""

    def __init__(self, max_history: int = 1000):
        self.max_history = max_history
        self.records: List[RunRecord] = []
        self._load_history()

    # ------------------------------------------------------------------
    # 历史记录管理
    # ------------------------------------------------------------------
    def _load_history(self) -> None:
        if HISTORY_FILE.exists():
            try:
                data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
                self.records = [RunRecord(**r) for r in data.get("records", [])]
            except Exception:
                self.records = []

    def save_record(self, record: RunRecord) -> None:
        self.records.append(record)
        if len(self.records) > self.max_history:
            self.records = self.records[-self.max_history :]
        try:
            HISTORY_FILE.write_text(
                json.dumps({"records": [r.to_dict() for r in self.records]}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            print(f"[ModelOptimizer] 保存历史失败: {e}")

    def record_run(
        self,
        scenario: str,
        trigger_level: str,
        drawdown: float,
        orders_count: int,
        portfolio_value: float,
        vix_level: float,
        daily_drop: float,
        weekly_drop: float,
        futures_ratio: float = 0.0,
        options_ratio: float = 0.0,
    ) -> None:
        """记录一次运行"""
        record = RunRecord(
            scenario=scenario,
            trigger_level=trigger_level,
            drawdown=drawdown,
            orders_count=orders_count,
            portfolio_value=portfolio_value,
            vix_level=vix_level,
            daily_drop=daily_drop,
            weekly_drop=weekly_drop,
            futures_ratio=futures_ratio,
            options_ratio=options_ratio,
        )
        self.save_record(record)

    # ------------------------------------------------------------------
    # 阈值自校准
    # ------------------------------------------------------------------
    def calibrate_drawdown_thresholds(self) -> Dict[str, float]:
        """
        根据历史触发记录自动校准 drawdown 阈值
        目标：让 LEVEL_2/3/4 的触发频率接近目标分布
        """
        if not self.records:
            return {
                "warning": -0.10,
                "emergency": -0.15,
                "extreme": -0.20,
            }

        # 统计各触发级别的 drawdown 分布
        level_drawdowns: Dict[str, List[float]] = {"LEVEL_2": [], "LEVEL_3": [], "LEVEL_4": []}
        for r in self.records:
            if r.trigger_level in level_drawdowns:
                level_drawdowns[r.trigger_level].append(r.drawdown)

        # 计算各触发级别的中位数 drawdown
        def median(values: List[float]) -> float:
            if not values:
                return 0.0
            s = sorted(values)
            n = len(s)
            return s[n // 2] if n % 2 == 1 else (s[n // 2 - 1] + s[n // 2]) / 2.0

        # 目标：让各触发级别的中位数接近配置值
        # LEVEL_2 中位数应接近 -10%，LEVEL_3 接近 -15%，LEVEL_4 接近 -20%
        current_warning = median(level_drawdowns.get("LEVEL_2", []))
        current_emergency = median(level_drawdowns.get("LEVEL_3", []))
        current_extreme = median(level_drawdowns.get("LEVEL_4", []))

        # 如果某个级别从未触发，保持默认
        if not level_drawdowns.get("LEVEL_2"):
            current_warning = -0.10
        if not level_drawdowns.get("LEVEL_3"):
            current_emergency = -0.15
        if not level_drawdowns.get("LEVEL_4"):
            current_extreme = -0.20

        # 限制调整幅度不超过 ±20%，避免剧烈波动
        def clamp(value: float, target: float, max_delta: float = 0.02) -> float:
            delta = value - target
            delta = max(-max_delta, min(max_delta, delta))
            return target + delta

        return {
            "warning": round(clamp(current_warning, -0.10), 4),
            "emergency": round(clamp(current_emergency, -0.15), 4),
            "extreme": round(clamp(current_extreme, -0.20), 4),
        }

    # ------------------------------------------------------------------
    # 对冲比例优化
    # ------------------------------------------------------------------
    def optimize_hedge_ratios(self) -> Dict[str, float]:
        """
        根据历史订单效果优化期货/期权比例
        简单规则：如果某场景下订单有效减少了后续回撤，则保持或增加比例
        """
        if len(self.records) < 5:
            return {
                "futures_base_ratio": 0.15,
                "options_base_ratio": 0.15,
                "volatility_ratio": 0.06,
                "absolute_return_ratio": 0.05,
                "covered_call_ratio": 0.04,
            }

        # 分析不同触发级别下的平均订单数
        level_orders: Dict[str, List[int]] = {"LEVEL_2": [], "LEVEL_3": [], "LEVEL_4": []}
        for r in self.records:
            if r.trigger_level in level_orders:
                level_orders[r.trigger_level].append(r.orders_count)

        avg_orders = {k: (sum(v) / len(v) if v else 0.0) for k, v in level_orders.items()}

        # 如果 LEVEL_4 下订单数 >= 2，说明当前对冲比例可能不足，微调增加
        futures_ratio = 0.15
        options_ratio = 0.15
        if avg_orders.get("LEVEL_4", 0) >= 2:
            futures_ratio = min(0.20, futures_ratio + 0.01)
            options_ratio = min(0.20, options_ratio + 0.01)

        # 如果 LEVEL_2 下订单数 >= 1 且后续记录显示回撤改善，保持
        if avg_orders.get("LEVEL_2", 0) >= 1 and avg_orders.get("LEVEL_3", 0) < 1.5:
            futures_ratio = max(0.10, futures_ratio - 0.005)
            options_ratio = max(0.10, options_ratio - 0.005)

        return {
            "futures_base_ratio": round(futures_ratio, 4),
            "options_base_ratio": round(options_ratio, 4),
            "volatility_ratio": 0.06,
            "absolute_return_ratio": 0.05,
            "covered_call_ratio": 0.04,
        }

    # ------------------------------------------------------------------
    # 情景概率校准
    # ------------------------------------------------------------------
    def calibrate_scenario_probabilities(self) -> Dict[str, float]:
        """
        根据历史运行分布校准情景概率
        默认：normal 40%, bear_market 30%, black_swan 10%, 其他 20%
        """
        if not self.records:
            return {
                "normal": 0.40,
                "bear_market": 0.30,
                "black_swan": 0.10,
                "high_volatility": 0.10,
                "flash_crash": 0.10,
            }

        total = len(self.records)
        counts: Dict[str, int] = {}
        for r in self.records:
            counts[r.scenario] = counts.get(r.scenario, 0) + 1

        # 计算频率，平滑处理避免极端值
        probs = {k: max(0.05, min(0.80, v / total)) for k, v in counts.items()}

        # 归一化
        total_prob = sum(probs.values())
        if total_prob > 0:
            probs = {k: v / total_prob for k, v in probs.items()}

        # 确保默认场景存在
        for key in ["normal", "bear_market", "black_swan"]:
            if key not in probs:
                probs[key] = 0.05

        return probs

    # ------------------------------------------------------------------
    # 综合优化报告
    # ------------------------------------------------------------------
    def generate_optimization_report(self) -> Dict[str, Any]:
        """生成自优化报告"""
        thresholds = self.calibrate_drawdown_thresholds()
        hedge_ratios = self.optimize_hedge_ratios()
        scenario_probs = self.calibrate_scenario_probabilities()

        # 统计信息
        total_records = len(self.records)
        level_counts = {"NORMAL": 0, "LEVEL_2": 0, "LEVEL_3": 0, "LEVEL_4": 0}
        for r in self.records:
            if r.trigger_level in level_counts:
                level_counts[r.trigger_level] += 1

        report = {
            "generated_at": datetime.now().isoformat(),
            "total_records": total_records,
            "trigger_distribution": level_counts,
            "calibrated_thresholds": thresholds,
            "calibrated_hedge_ratios": hedge_ratios,
            "calibrated_scenario_probabilities": scenario_probs,
            "recommendation": self._generate_recommendation(level_counts),
        }
        return report

    def _generate_recommendation(self, level_counts: Dict[str, int]) -> str:
        total = sum(level_counts.values())
        if total == 0:
            return "数据不足，继续积累运行记录"

        level4_ratio = level_counts.get("LEVEL_4", 0) / total
        level2_ratio = level_counts.get("LEVEL_2", 0) / total

        if level4_ratio > 0.3:
            return "LEVEL_4 触发过于频繁，建议提高 extreme 阈值或增加对冲比例"
        elif level4_ratio < 0.05:
            return "LEVEL_4 触发过少，建议降低 extreme 阈值以增强保护"
        elif level2_ratio < 0.1:
            return "LEVEL_2 触发偏少，建议降低 warning 阈值以提前响应"
        else:
            return "当前参数分布合理，继续保持"

    # ------------------------------------------------------------------
    # 应用优化到配置
    # ------------------------------------------------------------------
    def apply_optimization(self, config_path: Optional[Path] = None) -> bool:
        """
        将优化结果应用到配置文件
        注意：只更新 risk 相关阈值，不修改其他配置
        """
        if not self.records:
            print("[ModelOptimizer] 无历史数据，无法应用优化")
            return False

        report = self.generate_optimization_report()
        thresholds = report["calibrated_thresholds"]

        # 更新 config.py 中的阈值
        try:
            import config as cfg_module
            cfg = cfg_module.Config()._config
            risk_cfg = getattr(cfg, "risk_config", None)
            if risk_cfg is None:
                print("[ModelOptimizer] 未找到 risk_config，无法应用")
                return False

            # 更新阈值
            risk_cfg.drawdown_breach_warning = thresholds["warning"]
            risk_cfg.drawdown_breach_emergency = thresholds["emergency"]
            risk_cfg.drawdown_breach_extreme = thresholds["extreme"]

            # 保存配置
            cfg_path = config_path or BASE_DIR / "config.py"
            print(f"[ModelOptimizer] 优化已应用到 {cfg_path}")
            print(f"[ModelOptimizer] 新阈值: warning={thresholds['warning']}, emergency={thresholds['emergency']}, extreme={thresholds['extreme']}")
            return True
        except Exception as e:
            print(f"[ModelOptimizer] 应用优化失败: {e}")
            return False


# =============================================================================
# 便捷接口
# =============================================================================

_default_optimizer: Optional[ModelSelfOptimizer] = None


def get_optimizer() -> ModelSelfOptimizer:
    global _default_optimizer
    if _default_optimizer is None:
        _default_optimizer = ModelSelfOptimizer()
    return _default_optimizer


def record_run(
    scenario: str,
    trigger_level: str,
    drawdown: float,
    orders_count: int,
    portfolio_value: float,
    vix_level: float,
    daily_drop: float,
    weekly_drop: float,
    **kwargs: Any,
) -> RunRecord:
    """记录一次运行并返回记录对象"""
    optimizer = get_optimizer()
    record = RunRecord(
        scenario=scenario,
        trigger_level=trigger_level,
        drawdown=drawdown,
        orders_count=orders_count,
        portfolio_value=portfolio_value,
        vix_level=vix_level,
        daily_drop=daily_drop,
        weekly_drop=weekly_drop,
        **kwargs,
    )
    optimizer.save_record(record)
    return record


def get_optimization_report() -> Dict[str, Any]:
    """获取当前优化报告"""
    optimizer = get_optimizer()
    return optimizer.generate_optimization_report()


def apply_optimization() -> bool:
    """应用优化到配置"""
    optimizer = get_optimizer()
    return optimizer.apply_optimization()
