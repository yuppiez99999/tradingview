#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SkillManager: 经验沉淀管理器 (N5).

记录策略运行中的经验教训, 沉淀到经验日志文件供后续复用.

修复 v1 M5 的 3 处 Bug:
  - timedelta 未导入 → 已补充
  - _update_skill_lessons 是空 pass → 最小可用实现
  - 路径硬编码 → 用 os.path + _BASE 相对项目根

设计原则:
  - Feature Flag: USE_SKILL_MANAGER (默认关闭)
  - 不可变性: 不原地修改日志, 只追加
  - 降级安全: 任何写入失败不影响主流程
  - 与 N1 对齐: 在 _trigger_retrain_with_cooldown 中被调用记录重训经验
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional


logger = logging.getLogger("skill_manager")


# ========== 配置 ==========
_SKILL_CONFIG = {
    "feature_flag_name": "USE_SKILL_MANAGER",
    "max_log_entries": 1000,           # 经验日志最大条数
    "default_skill_file": ".claude/skills/alpha_research_skill.md",
    "default_log_dir": "logs",
    "valid_lesson_types": {
        "retrain_success", "retrain_fail",
        "drift_true_positive", "drift_false_positive",
        "strategy_degradation", "signal_improvement",
        "hyperparam_tuning", "data_quality_issue",
        "unknown",
    },
    "valid_impacts": {"positive", "negative", "neutral"},
}


def _check_feature_flag() -> bool:
    """Feature Flag 检查 (与 N1/N2/N4 保持一致)."""
    try:
        val = os.getenv(_SKILL_CONFIG["feature_flag_name"], "False")
        return val.lower() in ("true", "1", "yes", "on")
    except Exception:
        return False


# ========== SkillManager ==========
class SkillManager:
    """经验沉淀管理器.

    两种存储:
      1. 结构化经验日志 (logs/experience_log.json) — 机器可读
      2. Skill Markdown 文件 (.claude/skills/*.md) — 人类可读, 追加模式
    """

    def __init__(
        self,
        project_base: Optional[str] = None,
        skill_file_path: Optional[str] = None,
    ):
        """初始化.

        Args:
            project_base: 项目根目录 (默认: 本文件所在目录)
            skill_file_path: Skill Markdown 文件路径 (相对 project_base 或绝对)
        """
        self._base = project_base or os.path.dirname(os.path.abspath(__file__))

        # Skill 文件路径
        if skill_file_path is None:
            skill_file_path = _SKILL_CONFIG["default_skill_file"]
        if os.path.isabs(skill_file_path):
            self.skill_path = skill_file_path
        else:
            self.skill_path = os.path.join(self._base, skill_file_path)

        # 经验日志路径
        log_dir = os.path.join(self._base, _SKILL_CONFIG["default_log_dir"])
        self.experience_log_path = os.path.join(log_dir, "experience_log.json")

        # 确保目录存在
        try:
            os.makedirs(os.path.dirname(self.skill_path), exist_ok=True)
            os.makedirs(log_dir, exist_ok=True)
        except Exception as e:
            logger.debug(f"创建目录失败 (非致命): {e}")

    # ---------- 读 ----------
    def load_experience_log(self) -> List[Dict[str, Any]]:
        """加载结构化经验日志."""
        if not os.path.exists(self.experience_log_path):
            return []
        try:
            with open(self.experience_log_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
        except Exception as e:
            logger.warning(f"读取经验日志失败: {e}")
        return []

    def get_recent_lessons(
        self,
        symbol: Optional[str] = None,
        days: int = 7,
        lesson_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """获取近 N 天的经验.

        Args:
            symbol: 标的筛选 (None = 全部)
            days: 回溯天数
            lesson_type: 类型筛选 (None = 全部)

        Returns:
            按时间倒序的经验列表
        """
        log = self.load_experience_log()
        cutoff = datetime.now() - timedelta(days=days)
        filtered = []
        for entry in log:
            try:
                ts = datetime.fromisoformat(entry.get("timestamp", ""))
            except (ValueError, TypeError):
                continue
            if ts < cutoff:
                continue
            if symbol is not None and entry.get("symbol") != symbol:
                continue
            if lesson_type is not None and entry.get("lesson_type") != lesson_type:
                continue
            filtered.append(entry)
        return sorted(filtered, key=lambda x: x.get("timestamp", ""), reverse=True)

    # ---------- 写 ----------
    def save_experience(
        self,
        symbol: str,
        lesson: Dict[str, Any],
        enabled: Optional[bool] = None,
    ) -> bool:
        """记录一条经验.

        Args:
            symbol: 标的代码
            lesson: 经验字典
                - type: 经验类型 (见 _SKILL_CONFIG["valid_lesson_types"])
                - description: 描述 (必填)
                - impact: positive | negative | neutral
                - action_taken: 采取的动作
                - verified: 是否已验证 (bool)
            enabled: 覆盖 Feature Flag (True=强制记录, None=用 Flag)

        Returns:
            True 如果成功写入日志
        """
        # Feature Flag 检查
        if enabled is None:
            enabled = _check_feature_flag()
        if not enabled:
            return False

        # 校验必填字段
        description = str(lesson.get("description", "")).strip()
        if not description:
            logger.debug("经验描述为空, 跳过记录")
            return False

        # 规范化字段
        lesson_type = str(lesson.get("type", "unknown"))
        if lesson_type not in _SKILL_CONFIG["valid_lesson_types"]:
            lesson_type = "unknown"

        impact = str(lesson.get("impact", "neutral"))
        if impact not in _SKILL_CONFIG["valid_impacts"]:
            impact = "neutral"

        entry: Dict[str, Any] = {
            "timestamp": datetime.now().isoformat(),
            "symbol": str(symbol),
            "lesson_type": lesson_type,
            "description": description,
            "impact": impact,
            "action_taken": str(lesson.get("action_taken", "")),
            "verified": bool(lesson.get("verified", False)),
        }

        # 1. 写入结构化日志
        success = False
        try:
            log = self.load_experience_log()
            log.append(entry)
            # 限制日志大小
            if len(log) > _SKILL_CONFIG["max_log_entries"]:
                log = log[-_SKILL_CONFIG["max_log_entries"]:]
            with open(self.experience_log_path, "w", encoding="utf-8") as f:
                json.dump(log, f, ensure_ascii=False, indent=2)
            success = True
        except Exception as e:
            logger.warning(f"写入经验日志失败: {e}")

        # 2. 追加到 Skill Markdown (独立 try, 日志失败不影响 markdown)
        try:
            self._append_skill_markdown(entry)
        except Exception as e:
            logger.debug(f"追加 Skill Markdown 失败 (非致命): {e}")

        return success

    def _append_skill_markdown(self, entry: Dict[str, Any]) -> None:
        """将经验追加到 Skill Markdown 文件 (最小可用实现, 修复 v1 pass 空实现).

        采用纯追加模式, 不解析现有 Markdown, 避免破坏已有内容.
        """
        if not os.path.exists(os.path.dirname(self.skill_path)):
            return
        date_str = datetime.now().strftime("%Y-%m-%d")
        # 经验类型到 emoji 的简单映射
        icon_map = {
            "positive": "✅",
            "negative": "⚠️",
            "neutral": "📝",
        }
        icon = icon_map.get(entry.get("impact", "neutral"), "📝")

        block = (
            f"\n### {icon} {date_str} — {entry.get('symbol', 'N/A')} "
            f"[{entry.get('lesson_type', 'unknown')}]\n"
            f"- **描述**: {entry.get('description', '')}\n"
            f"- **影响**: {entry.get('impact', 'neutral')}\n"
            f"- **动作**: {entry.get('action_taken', 'N/A')}\n"
            f"- **已验证**: {'是' if entry.get('verified') else '否'}\n"
        )

        # 检查是否已存在同名 section (避免重复追加同一秒内的重复写入)
        try:
            if os.path.exists(self.skill_path):
                with open(self.skill_path, "r", encoding="utf-8") as f:
                    existing = f.read()
                # 用时间戳去重
                if entry.get("timestamp", "")[:16] in existing:
                    logger.debug(f"经验已存在, 跳过追加: {entry.get('timestamp')}")
                    return
        except Exception:
            pass

        with open(self.skill_path, "a", encoding="utf-8") as f:
            f.write(block)

    # ---------- 统计 ----------
    def summary(self, days: int = 30) -> Dict[str, Any]:
        """生成经验统计摘要."""
        lessons = self.get_recent_lessons(days=days)
        by_type: Dict[str, int] = {}
        by_impact: Dict[str, int] = {"positive": 0, "negative": 0, "neutral": 0}
        for l in lessons:
            t = l.get("lesson_type", "unknown")
            by_type[t] = by_type.get(t, 0) + 1
            imp = l.get("impact", "neutral")
            by_impact[imp] = by_impact.get(imp, 0) + 1
        return {
            "total": len(lessons),
            "window_days": days,
            "by_type": by_type,
            "by_impact": by_impact,
        }


# ========== 便捷函数 ==========
def record_lesson(
    symbol: str,
    lesson_type: str,
    description: str,
    impact: str = "neutral",
    action_taken: str = "",
    verified: bool = False,
) -> bool:
    """便捷函数: 快速记录一条经验 (无需实例化 SkillManager).

    Returns:
        True 如果成功写入
    """
    try:
        sm = SkillManager()
        return sm.save_experience(
            symbol=symbol,
            lesson={
                "type": lesson_type,
                "description": description,
                "impact": impact,
                "action_taken": action_taken,
                "verified": verified,
            },
        )
    except Exception as e:
        logger.debug(f"记录经验失败 (非致命): {e}")
        return False


if __name__ == "__main__":
    # 快速自检
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    logger.info("\n" + "=" * 60)
    logger.info("  N5 SkillManager 自检")
    logger.info("=" * 60)

    # 用临时目录测试
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        sm = SkillManager(project_base=tmpdir)
        logger.info(f"\n项目根: {tmpdir}")
        logger.info(f"经验日志: {sm.experience_log_path}")
        logger.info(f"Skill 文件: {sm.skill_path}")

        # 1. 记录经验
        logger.info("\n--- 记录 3 条经验 ---")
        sm.save_experience("688041.SH", {
            "type": "retrain_success",
            "description": "漂移触发重训后 CV IC 从 0.02 提升到 0.045",
            "impact": "positive",
            "action_taken": "adaptive_retrain",
            "verified": True,
        }, enabled=True)
        sm.save_experience("601899.SH", {
            "type": "drift_false_positive",
            "description": "IC 单日下跌但后续恢复, 误触发重训",
            "impact": "negative",
            "action_taken": "增加冷却期",
            "verified": False,
        }, enabled=True)
        sm.save_experience("588080.SH", {
            "type": "signal_improvement",
            "description": "N4 调整超参后测试集 R² 提升 15%",
            "impact": "positive",
            "action_taken": "hyperparam_tuning",
            "verified": True,
        }, enabled=True)

        # 2. 读取
        lessons = sm.get_recent_lessons(days=7)
        logger.info(f"\n--- 读取 {len(lessons)} 条近 7 天经验 ---")
        for l in lessons:
            logger.info(f"  [{l['lesson_type']}] {l['symbol']}: {l['description'][:40]}...")

        # 3. 统计
        s = sm.summary(days=7)
        logger.info("\n--- 统计 (近 7 天) ---")
        logger.info(f"  总计: {s['total']}")
        logger.info(f"  按类型: {s['by_type']}")
        logger.info(f"  按影响: {s['by_impact']}")

    logger.info("\n" + "=" * 60)
    logger.info("  ✅ N5 自检完成")
    logger.info("=" * 60)
