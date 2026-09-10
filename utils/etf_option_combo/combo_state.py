"""组合状态持久化管理器 (ComboStateManager).

管理 cache/etf_option_combo_state.json, 支持原子写入、向前兼容与幂等性.

状态schema (v1.0):
    {
        "config_version": "1.0",
        "last_updated": "ISO时间戳",
        "strategy_instances": {
            "{strategy_type}_{underlying}_{trade_date}": {...}
        },
        "budgets": {
            "{strategy_type}": {"ytd_income": float, "ytd_expense": float}
        }
    }

特性:
    - 原子写入: 先写 .tmp 再 os.replace, 保证写入中断不损坏原文件
    - 向前兼容: 旧 schema (无 config_version) 自动迁移至 v1.0
    - 幂等性: 同一 instance_id 重复 save 不产生重复记录
    - 预算分账: 按策略类型跟踪权利金收入/支出
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, cast

from utils.datetime_utils import now_bj

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_STATE_PATH = _PROJECT_ROOT / "cache" / "etf_option_combo_state.json"

_STATE_VERSION = "1.0"
_EMPTY_STATE: dict[str, Any] = {
    "config_version": _STATE_VERSION,
    "last_updated": "",
    "strategy_instances": {},
    "budgets": {},
}


class ComboStateManager:
    """组合状态持久化 — JSON序列化 + 向前兼容 + 幂等性.

    状态文件: cache/etf_option_combo_state.json

    Attributes:
        _state_path: 状态文件路径
        _state: 内存缓存的状态字典
    """

    def __init__(self, state_path: str | Path | None = None) -> None:
        if state_path is None:
            self._state_path = _DEFAULT_STATE_PATH
        else:
            self._state_path = Path(state_path)
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state: dict[str, Any] = self.load()

    def load(self) -> dict[str, Any]:
        """加载状态 — 向前兼容旧 schema (config_version 迁移).

        Returns:
            状态字典. 文件不存在或损坏时返回空 schema.
        """
        if not self._state_path.exists():
            logger.info("状态文件不存在, 使用空 schema: %s", self._state_path)
            return self._empty_state()

        try:
            with open(self._state_path, encoding="utf-8") as f:
                raw: dict[str, Any] = json.load(f)
        except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
            logger.error("状态文件读取失败, 使用空 schema: %s", e)
            return self._empty_state()

        version = raw.get("config_version", "0")
        if version != _STATE_VERSION:
            raw = self._migrate(raw, version)
            logger.info("状态 schema 从 v%s 迁移至 v%s", version, _STATE_VERSION)

        raw.setdefault("strategy_instances", {})
        raw.setdefault("budgets", {})
        return raw

    @staticmethod
    def _empty_state() -> dict[str, Any]:
        """构造全新空 schema (深拷贝, 避免共享嵌套字典)."""
        return {
            "config_version": _STATE_VERSION,
            "last_updated": "",
            "strategy_instances": {},
            "budgets": {},
        }

    def save(self, state: dict[str, Any]) -> bool:
        """保存状态 — 原子写入 (先写 .tmp 再 os.replace).

        Args:
            state: 完整状态字典

        Returns:
            True=成功, False=失败
        """
        state["config_version"] = _STATE_VERSION
        state["last_updated"] = now_bj().isoformat(timespec="seconds")
        state.setdefault("strategy_instances", {})
        state.setdefault("budgets", {})

        tmp_path = str(self._state_path) + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, str(self._state_path))
            self._state = state
            return True
        except (OSError, TypeError, ValueError, OverflowError) as e:
            logger.error("状态保存失败: %s", e)
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            return False

    def get_strategy_instance(self, instance_id: str) -> dict[str, Any] | None:
        """获取策略实例状态 — 用于幂等性 (同交易日同策略同标的不重复生成).

        Args:
            instance_id: 格式 "{strategy_type}_{underlying}_{trade_date}"

        Returns:
            策略实例状态字典, 或 None (不存在)
        """
        instance = self._state.get("strategy_instances", {}).get(instance_id)
        return cast("dict[str, Any] | None", instance)

    def save_strategy_instance(
        self,
        instance_id: str,
        instance_state: dict[str, Any],
    ) -> bool:
        """保存策略实例状态 — 幂等性保证 (同 ID 覆盖不新增).

        Args:
            instance_id: 策略实例ID
            instance_state: 实例状态字典

        Returns:
            True=成功
        """
        self._state.setdefault("strategy_instances", {})[instance_id] = instance_state
        return self.save(self._state)

    def update_budget(
        self,
        strategy_type: str,
        delta_premium: float,
    ) -> bool:
        """更新预算跟踪 — 正=收入, 负=支出, 按策略类型分账.

        Args:
            strategy_type: 策略类型 (StrategyType.value)
            delta_premium: 权利金变动 (正=收入, 负=支出)

        Returns:
            True=成功
        """
        budgets = self._state.setdefault("budgets", {})
        budget = budgets.setdefault(strategy_type, {"ytd_income": 0.0, "ytd_expense": 0.0})

        if delta_premium >= 0:
            budget["ytd_income"] = round(budget.get("ytd_income", 0.0) + delta_premium, 6)
        else:
            budget["ytd_expense"] = round(budget.get("ytd_expense", 0.0) + abs(delta_premium), 6)

        return self.save(self._state)

    def get_budget(self, strategy_type: str) -> dict[str, float]:
        """获取策略预算状态.

        Returns:
            {"ytd_income": float, "ytd_expense": float}
        """
        return cast(
            "dict[str, float]",
            self._state.get("budgets", {}).get(
                strategy_type, {"ytd_income": 0.0, "ytd_expense": 0.0}
            ),
        )

    def clear_all(self) -> bool:
        """清空所有状态 (谨慎使用)."""
        self._state = self._empty_state()
        return self.save(self._state)

    @staticmethod
    def _migrate(raw: dict[str, Any], from_version: str) -> dict[str, Any]:
        """schema 迁移 — 旧版本字段缺失时填充默认值.

        Args:
            raw: 原始状态字典
            from_version: 原始版本号

        Returns:
            迁移后的状态字典 (v1.0)
        """
        if from_version == "0":
            raw["config_version"] = _STATE_VERSION
            if "instances" in raw and "strategy_instances" not in raw:
                raw["strategy_instances"] = raw.pop("instances")
            raw.setdefault("strategy_instances", {})
            raw.setdefault("budgets", {})
            logger.info("状态 schema v0 -> v1.0 迁移完成")
        return raw
