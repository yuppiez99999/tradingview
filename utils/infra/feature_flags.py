# -*- coding: utf-8 -*-
"""Feature Flag 框架 — 三层保护 Layer 3.

模块整合 8.4 — ARCHITECTURE §1.4 / ADR-003
任务: T1.3

设计原则 (ADR-003):
    1. 默认值铁律: flag default 必须等于当前生产行为 (即所有新 flag 默认 False)
    2. 启用双签: 变更发起人 + 风控负责人
    3. 禁用单签: 任何风控人员可一键关闭
    4. 审计可追溯: 所有变更写入 reports/flag_audit/{flag_name}.jsonl
    5. 配置走 ConfigManager 4 级优先级 (HC-5)

API:
    from utils.infra.feature_flags import FeatureFlags, is_enabled

    if FeatureFlags.is_enabled("USE_LLM_REPORT_ANALYZER"):
        from utils.alpha.llm_router import LLMRouter
    else:
        from utils.llm_client import LLMClient  # 原路径

硬约束:
    - HC-5: ConfigManager 4 级优先级解析不可绕过
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Optional

# 复用 ConfigManager 4 级优先级 (HC-5)
from utils.config_manager import get_config

logger = logging.getLogger("feature_flags")


def _find_project_root() -> Path:
    """向上查找项目根目录 (通过已知 marker 文件/目录识别).

    比硬编码 parent.parent.parent 更健壮, 文件移动不会失效.
    """
    project_markers = [
        "config",
        "utils",
        "v8.3_institutional",
        "research",
        "tests",
        "requirements.txt",
        "ruff.toml",
        "pytest.ini",
    ]
    current = Path(__file__).resolve().parent
    for _ in range(10):
        if any((current / m).exists() for m in project_markers):
            return current
        if current.parent == current:
            break
        current = current.parent
    return Path(__file__).resolve().parent.parent.parent


# 项目根目录
_PROJECT_ROOT = _find_project_root()

# 运行时覆盖目录 (测试/紧急回滚用)
# 优先级: 环境变量 QUANT_FLAG_OVERRIDE_DIR > reports/flag_overrides/
_OVERRIDE_DIR_ENV = "QUANT_FLAG_OVERRIDE_DIR"
_DEFAULT_OVERRIDE_DIR = _PROJECT_ROOT / "reports" / "flag_overrides"


class FlagError(Exception):
    """Feature Flag 操作基础异常."""


class FlagNotFoundError(FlagError):
    """Flag 不在注册表中."""


class FlagPermissionError(FlagError):
    """缺少双签或权限不足."""


class FeatureFlags:
    """Feature Flag 注册表 + 热加载.

    单例模式, 全项目共享一份 flag 状态.
    所有 flag 默认值来自 v8.3_institutional/config/feature_flags.yaml.
    运行时覆盖通过 reports/flag_overrides/{flag_name}.json 实现 (优先级最高).
    """

    _instance: Optional["FeatureFlags"] = None
    _lock: RLock = RLock()

    def __init__(self) -> None:
        self._flags_cache: Dict[str, Dict[str, Any]] = {}
        self._overrides: Dict[str, bool] = {}
        self._audit_trails: Dict[str, List[Dict[str, Any]]] = {}
        self._last_load_mtime: float = 0.0
        self._load_config()

    @classmethod
    def get_instance(cls) -> "FeatureFlags":
        """获取单例 (线程安全)."""
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """重置单例 (仅测试用)."""
        with cls._lock:
            cls._instance = None

    # ============================================================
    # 配置加载 (走 ConfigManager, 满足 HC-5)
    # ============================================================
    def _load_config(self) -> None:
        """从 ConfigManager 加载 feature_flags.yaml (4 级优先级)."""
        # ConfigManager 自动按 4 级优先级解析 (HC-5)
        cfg = get_config("feature_flags", default={})
        if not cfg:
            logger.warning("feature_flags.yaml 未找到, 所有 flag 默认 False")
            self._flags_cache = {}
            return

        self._flags_cache = cfg.get("flags", {}) or {}
        settings = cfg.get("settings", {}) or {}
        self._audit_log_dir = Path(settings.get("audit_log_dir", "reports/flag_audit"))
        if not self._audit_log_dir.is_absolute():
            self._audit_log_dir = _PROJECT_ROOT / self._audit_log_dir

        self._last_load_mtime = time.time()
        self._load_overrides()

    def _load_overrides(self) -> None:
        """加载运行时覆盖 (优先级最高, 用于紧急回滚或测试).

        覆盖目录优先级:
            1. 环境变量 QUANT_FLAG_OVERRIDE_DIR
            2. reports/flag_overrides/ (默认)
        """
        override_dir_str = os.environ.get(_OVERRIDE_DIR_ENV, "").strip()
        if override_dir_str:
            override_dir = Path(override_dir_str).expanduser().resolve()
        else:
            override_dir = _DEFAULT_OVERRIDE_DIR

        self._overrides = {}
        if not override_dir.exists():
            return

        for flag_file in override_dir.glob("*.json"):
            try:
                with open(flag_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                flag_name = data.get("flag_name") or flag_file.stem
                enabled = bool(data.get("enabled", False))
                self._overrides[flag_name] = enabled
                logger.info(
                    "Flag override loaded: %s = %s (from %s)",
                    flag_name,
                    enabled,
                    flag_file,
                )
            except (json.JSONDecodeError, OSError) as e:
                logger.error("Failed to load flag override %s: %s", flag_file, e)

    def reload(self) -> None:
        """重新加载配置 + 覆盖 (用于热加载)."""
        with self._lock:
            self._load_config()
            logger.info("FeatureFlags reloaded: %d flags, %d overrides", len(self._flags_cache), len(self._overrides))

    # ============================================================
    # 查询 API
    # ============================================================
    def is_enabled(self, name: str) -> bool:
        """查询 flag 是否启用.

        优先级:
            1. 运行时覆盖 (reports/flag_overrides/)
            2. 注册表默认值 (feature_flags.yaml)

        Args:
            name: flag 名称 (如 "USE_LLM_REPORT_ANALYZER")

        Returns:
            True 如果启用, False 如果禁用或未注册

        Usage:
            >>> FeatureFlags.is_enabled("USE_LLM_REPORT_ANALYZER")
            False  # 默认值, 不改变现状
        """
        # 优先级 1: 运行时覆盖
        if name in self._overrides:
            return self._overrides[name]

        # 优先级 2: 注册表默认值
        flag_def = self._flags_cache.get(name)
        if flag_def is None:
            logger.warning("Flag not registered: %s (returning False)", name)
            return False

        return bool(flag_def.get("default", False))

    def get_flag_def(self, name: str) -> Dict[str, Any]:
        """获取 flag 完整定义 (描述/要求/回滚秒数等)."""
        flag_def = self._flags_cache.get(name)
        if flag_def is None:
            raise FlagNotFoundError(f"Flag not registered: {name}")
        return dict(flag_def)

    def list_flags(self) -> List[Dict[str, Any]]:
        """列出所有已注册 flag (审计用)."""
        result = []
        for name, flag_def in self._flags_cache.items():
            entry = {"name": name, **flag_def}
            entry["current_value"] = self.is_enabled(name)
            entry["overridden"] = name in self._overrides
            result.append(entry)
        return result

    # ============================================================
    # 变更 API (双签 / 单签)
    # ============================================================
    def enable(self, name: str, signer: str, co_signer: str, reason: str = "") -> None:
        """启用 flag (需双签).

        Args:
            name: flag 名称
            signer: 变更发起人 (字符串标识)
            co_signer: 风控负责人 (字符串标识)
            reason: 变更原因 (写入审计日志)

        Raises:
            FlagNotFoundError: flag 未注册
            FlagPermissionError: 缺少双签
        """
        if not name or not isinstance(name, str):
            raise FlagError("name must be non-empty string")
        if not signer or not co_signer:
            raise FlagPermissionError(f"Enable {name} requires dual signature (signer + co_signer)")
        if signer == co_signer:
            raise FlagPermissionError(f"Enable {name}: signer and co_signer must be different persons")

        if name not in self._flags_cache:
            raise FlagNotFoundError(f"Flag not registered: {name}")

        with self._lock:
            self._set_override(name, True, signer, co_signer, reason, "enable")
            logger.info("Flag ENABLED: %s by %s + %s (reason: %s)", name, signer, co_signer, reason)

    def disable(self, name: str, signer: str, reason: str = "") -> None:
        """禁用 flag (单签即可, 任何风控人员可一键关闭).

        Args:
            name: flag 名称
            signer: 操作人 (字符串标识)
            reason: 操作原因
        """
        if not name or not isinstance(name, str):
            raise FlagError("name must be non-empty string")
        if not signer:
            raise FlagPermissionError(f"Disable {name} requires signer")

        if name not in self._flags_cache:
            raise FlagNotFoundError(f"Flag not registered: {name}")

        with self._lock:
            self._set_override(name, False, signer, "", reason, "disable")
            logger.warning("Flag DISABLED: %s by %s (reason: %s)", name, signer, reason)

    def _set_override(self, name: str, enabled: bool, signer: str, co_signer: str, reason: str, action: str) -> None:
        """写入覆盖文件 + 审计日志.

        使用原子写入模式（先写临时文件再 rename），避免并发写入导致文件损坏.
        """
        # 1. 写覆盖文件（原子写入）
        self._audit_log_dir.mkdir(parents=True, exist_ok=True)
        override_dir = Path(os.environ.get(_OVERRIDE_DIR_ENV, "").strip() or _DEFAULT_OVERRIDE_DIR)
        override_dir.mkdir(parents=True, exist_ok=True)
        override_file = override_dir / f"{name}.json"
        override_data = {
            "flag_name": name,
            "enabled": enabled,
            "signer": signer,
            "co_signer": co_signer or None,
            "reason": reason,
            "action": action,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

        # 原子写入：临时文件 + rename，避免并发损坏
        if override_file.exists():
            logger.info("Overwriting existing flag override: %s at %s", name, override_file)
        temp_file = override_file.with_suffix(".json.tmp")
        try:
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(override_data, f, ensure_ascii=False, indent=2)
            temp_file.replace(override_file)
        except Exception as e:
            if temp_file.exists():
                temp_file.unlink(missing_ok=True)
            raise

        # 2. 更新内存
        self._overrides[name] = enabled

        # 3. 写审计日志 (JSONL 格式, 追加)
        audit_file = self._audit_log_dir / f"{name}.jsonl"
        with open(audit_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(override_data, ensure_ascii=False) + "\n")

    # ============================================================
    # 审计 API
    # ============================================================
    def audit_trail(self, name: str) -> List[Dict[str, Any]]:
        """获取 flag 的审计轨迹.

        Args:
            name: flag 名称

        Returns:
            审计记录列表 (按时间正序)
        """
        audit_file = self._audit_log_dir / f"{name}.jsonl"
        if not audit_file.exists():
            return []
        records = []
        with open(audit_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return records


# ============================================================
# 模块级快捷函数 (推荐业务代码使用)
# ============================================================
def is_enabled(name: str) -> bool:
    """快捷函数: 查询 flag 是否启用.

    Usage:
        >>> from utils.infra.feature_flags import is_enabled
        >>> if is_enabled("USE_LLM_REPORT_ANALYZER"):
        ...     pass
    """
    return FeatureFlags.get_instance().is_enabled(name)


def enable(name: str, signer: str, co_signer: str, reason: str = "") -> None:
    """快捷函数: 启用 flag (双签)."""
    FeatureFlags.get_instance().enable(name, signer, co_signer, reason)


def disable(name: str, signer: str, reason: str = "") -> None:
    """快捷函数: 禁用 flag (单签)."""
    FeatureFlags.get_instance().disable(name, signer, reason)


def list_flags() -> List[Dict[str, Any]]:
    """快捷函数: 列出所有 flag (审计用)."""
    return FeatureFlags.get_instance().list_flags()


def audit_trail(name: str) -> List[Dict[str, Any]]:
    """快捷函数: 获取 flag 审计轨迹."""
    return FeatureFlags.get_instance().audit_trail(name)


def reload() -> None:
    """快捷函数: 重新加载配置 (热加载)."""
    FeatureFlags.get_instance().reload()
