"""Superpowers 适配器 — Agent 技能框架集成 (W8.6 集成).

将 obra/superpowers 接入 AI Hedge Fund, 为 20 位分析师 Agent
提供结构化的开发流程技能 (brainstorming/debugging/TDD/code-review 等).

集成点:
    - quant_modules/ai_hedge_fund/agents/ 20 分析师 Agent
    - quant_modules/ai_hedge_fund/graph/ LangGraph 编排
    - utils/ai_coordinator.py AICoordinator

使用方式:
    from utils.superpowers_adapter import SuperpowersAdapter, get_superpowers_adapter

    adapter = get_superpowers_adapter()
    if adapter.is_ready():
        skills = adapter.list_skills()
        content = adapter.load_skill("systematic-debugging")
        workflow = adapter.get_agent_workflow("warren_buffett")

降级策略:
    - superpowers 目录不存在 → is_ready()=False, Agent 使用默认流程
    - 技能文件缺失 → 返回空, Agent 跳过该技能
    - 解析失败 → 记录警告, 不阻断 LangGraph 编排

依赖路径:
    - 主系统: 28-终极量化交易系统8.4/
    - superpowers 源码: 10_第三方项目/superpowers/skills/

集成日期: 2026-08-22 (v8.6, GitHub 今日热门项目集成)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SUPERPOWERS_SRC = (
    Path(__file__).resolve().parent.parent.parent / "10_第三方项目" / "superpowers"
)
_SKILLS_DIR = _SUPERPOWERS_SRC / "skills"


@dataclass
class SuperpowersConfig:
    """Superpowers 适配配置."""

    skills_dir: Path = field(default_factory=lambda: _SKILLS_DIR)
    # Agent → 技能映射 (每位分析师适用的开发流程技能)
    agent_skill_map: dict[str, list[str]] = field(
        default_factory=lambda: {
            "warren_buffett": [
                "systematic-debugging",
                "verification-before-completion",
            ],
            "ben_graham": ["systematic-debugging", "writing-plans"],
            "phil_fisher": ["brainstorming", "executing-plans"],
            "cathie_wood": ["brainstorming", "dispatching-parallel-agents"],
            "michael_burry": ["systematic-debugging", "receiving-code-review"],
            "charlie_munger": ["verification-before-completion", "writing-plans"],
            "peter_lynch": ["executing-plans", "test-driven-development"],
            "stanley_druckenmiller": [
                "dispatching-parallel-agents",
                "systematic-debugging",
            ],
            "ray_dalio": ["writing-plans", "verification-before-completion"],
            "bill_ackman": ["brainstorming", "requesting-code-review"],
        }
    )


class SuperpowersAdapter:
    """Superpowers 适配器 — Agent 技能框架.

    设计原则:
        - 不可变: 加载技能不修改原文件
        - 优雅降级: 目录不存在时 Agent 使用默认流程
        - 薄包装: 仅读取技能内容, 不执行技能逻辑
    """

    def __init__(self, config: SuperpowersConfig | None = None) -> None:
        self.config = config or SuperpowersConfig()
        self._init_error: str | None = None
        if not self.config.skills_dir.exists():
            self._init_error = f"skills 目录不存在: {self.config.skills_dir}"
            logger.warning(self._init_error)

    def is_ready(self) -> bool:
        """superpowers 技能目录是否可用."""
        return self.config.skills_dir.exists()

    def list_skills(self) -> list[str]:
        """列出所有可用技能 (供 UI 选择)."""
        if not self.is_ready():
            return []
        return [p.name for p in self.config.skills_dir.iterdir() if p.is_dir()]

    def load_skill(self, skill_name: str) -> str | None:
        """加载技能内容.

        Args:
            skill_name: 技能名称 (如 "systematic-debugging")

        Returns:
            技能 Markdown 内容或 None
        """
        if not self.is_ready():
            return None
        skill_path = self.config.skills_dir / skill_name
        if not skill_path.exists():
            logger.warning("技能不存在: %s", skill_name)
            return None
        # 技能目录下通常有 skill.md 或 SKILL.md
        for md_name in ("skill.md", "SKILL.md", "README.md", "index.md"):
            md_path = skill_path / md_name
            if md_path.exists():
                try:
                    content = md_path.read_text(encoding="utf-8")
                    logger.debug("✓ 技能已加载: %s (%s)", skill_name, md_name)
                    return content
                except (OSError, UnicodeDecodeError) as e:
                    logger.warning("读取技能失败: %s: %s", skill_name, e)
                    return None
        logger.warning("技能 %s 未找到 Markdown 文件", skill_name)
        return None

    def get_agent_skills(self, agent_id: str) -> list[str]:
        """获取分析师适用的技能列表."""
        return self.config.agent_skill_map.get(agent_id, [])

    def get_agent_workflow(self, agent_id: str) -> dict[str, Any]:
        """获取分析师的完整技能工作流.

        Returns:
            {"agent_id": str, "skills": [{"name": str, "content": str}]}
        """
        skill_names = self.get_agent_skills(agent_id)
        skills = []
        for name in skill_names:
            content = self.load_skill(name)
            if content:
                skills.append({"name": name, "content": content[:500]})  # 截断避免过大
        return {"agent_id": agent_id, "skills": skills}

    def get_status(self) -> dict[str, Any]:
        """获取 superpowers 状态 (供 UI 系统概览)."""
        return {
            "available": self.is_ready(),
            "skills_dir": str(self.config.skills_dir),
            "skill_count": len(self.list_skills()),
            "agent_count": len(self.config.agent_skill_map),
            "init_error": self._init_error,
        }


_superpowers_instance: SuperpowersAdapter | None = None


def get_superpowers_adapter(
    config: SuperpowersConfig | None = None,
) -> SuperpowersAdapter:
    """获取 superpowers 适配器单例."""
    global _superpowers_instance
    if _superpowers_instance is None:
        _superpowers_instance = SuperpowersAdapter(config)
    return _superpowers_instance


def is_superpowers_available() -> bool:
    """快速检查 superpowers 是否可用."""
    return get_superpowers_adapter().is_ready()


if __name__ == "__main__":
    adapter = get_superpowers_adapter()
