"""
ai_decision.config — 配置加载与查询
====================================

加载 v8.3_institutional/config/ai_decision.yaml (若不存在则使用内置默认),
提供角色映射 / 辩论阈值 / 聚合参数 / 运行模式的统一查询接口.

设计原则:
  - 不改写既有 model_routing.yaml, 独立新增 ai_decision.yaml, 保证最小爆炸半径
  - 复用项目 yaml (已依赖); 若 yaml 不可用, 回退内置 DEFAULT_CONFIG
  - get_config(name, default): 兼容 utils.config_manager 约定, 但本包自包含
"""

from __future__ import annotations

import os
from typing import Any

_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "v8.3_institutional",
    "config",
    "ai_decision.yaml",
)

DEFAULT_CONFIG: dict[str, Any] = {
    "version": "1.0.0",
    # 运行模式: shadow(默认, 仅记录不执行) / paper(模拟) / auto(经阈值放行)
    "mode": "shadow",
    # 角色 -> 后端模型标识 (与 providers._ROLE_BACKENDS 对应)
    "role_backends": {
        "signal": "deepseek",
        "compliance": "glm",
        "research": "moonshot",
        "reasoning": "claude",
        "intraday": "gpt",
        "bull": "deepseek",
        "bear": "glm",
        "judge": "claude",
    },
    # 辩论引擎参数
    "debate": {
        "confidence_threshold": 0.6,  # 双方置信度 > 此值且方向相反才辩论
        "max_rounds": 2,  # 最多辩论轮数
        "intraday_timeout": 60,  # 盘中总超时 (秒)
        "postclose_timeout": 300,  # 盘后总超时 (秒)
        "fallback_to_fast": True,  # 超时降级为快速聚合
    },
    # 非线性聚合器参数
    "aggregator": {
        "brier_window_days": 30,  # Brier 动态权重滚动窗口
        "diversity_bonus": 0.1,  # 多样性奖励上限
        "semantic_dup_threshold": 0.6,  # 词重叠相似度去重阈值
        "min_confidence": 0.3,  # 低于此置信度观点降权
    },
    # 决策门参数 (auto 模式放行条件)
    "gate": {
        "max_single_pct": 0.02,  # 单笔 <= 净值 2%
        "max_daily_pct": 0.10,  # 日内累计 <= 净值 10%
        "min_confidence": 0.7,  # 自动放行最低置信度
        "require_judge_auto": True,  # 必须 Judge 裁决类型为 AUTO
    },
    # 五 Agent 融合权重 (沿用 finance_agent_orchestrator.DEFAULT_WEIGHTS)
    "agent_weights": {
        "VALUE": 0.25,
        "MOMENTUM": 0.25,
        "SENTIMENT": 0.15,
        "RISK": 0.15,
        "MACRO": 0.20,
    },
}

_CONFIG: dict[str, Any] | None = None


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _load() -> dict[str, Any]:
    global _CONFIG
    if _CONFIG is not None:
        return _CONFIG
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(_CONFIG_PATH):
        try:
            import yaml

            with open(_CONFIG_PATH, encoding="utf-8") as fh:
                user = yaml.safe_load(fh) or {}
            cfg = _deep_merge(cfg, user)
        except (
            ImportError,
            OSError,
            ValueError,
            TypeError,
            AttributeError,
            RuntimeError,
        ) as exc:
            # ImportError: yaml 未安装; OSError: 文件读取失败 (权限/编码/磁盘);
            # ValueError/TypeError/AttributeError: 解析失败/格式错误/字段类型不符;
            # RuntimeError: _deep_merge 合并过程抛出
            import logging

            logging.getLogger("ai_decision.config").warning(
                "加载 ai_decision.yaml 失败, 用内置默认: %s", exc
            )
    # 环境变量可覆盖运行模式 (便于部署切换)
    env_mode = os.environ.get("AI_DECISION_MODE")
    if env_mode:
        cfg["mode"] = env_mode
    _CONFIG = cfg
    return cfg


def get_config(name: str = "", default: Any = None) -> Any:
    """兼容 config_manager 约定的统一查询入口

    name 为空返回整份配置; name 支持点路径 (如 "debate.max_rounds").
    """
    cfg = _load()
    if not name:
        return cfg
    node: Any = cfg
    for part in name.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return default
    return node


def reload() -> None:
    """强制重新加载 (测试用)"""
    global _CONFIG
    _CONFIG = None
    _load()
