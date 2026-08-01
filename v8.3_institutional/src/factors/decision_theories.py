# -*- coding: utf-8 -*-
"""Re-export 兼容层 — decision_theories 已迁移到 utils/alpha/.

模块整合 8.4 — T2.2 (2026-07-27)
================================
本文件保留以维持向后兼容 (HC-1 保护 V9 基线):
  - 原路径: v8.3_institutional/src/factors/decision_theories.py (本文件)
  - 新路径: utils/alpha/decision_theories.py (生产唯一事实源)

所有实际逻辑已迁移到新路径, 本文件仅做透明 re-export.
导入失败会回退到本地占位符, 避免阻断历史调用链.

Feature Flag: USE_DECISION_THEORIES_FUSION (默认 False, 启用新融合逻辑)
"""

from __future__ import annotations

import logging
import os
import sys

logger = logging.getLogger(__name__)

# 将项目根目录加入 sys.path, 确保能导入 utils.alpha.decision_theories
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

try:
    # 从新路径 re-export 全部公开 API
    from utils.alpha.decision_theories import (
        TheoryDecision,
        SorosReflexivityEngine,
        DalioEconomicMachine,
        FirstPrinciplesAnalyzer,
        BuffettMungerFramework,
        TheoryFusionEngine,
        run_full_theory_analysis,
    )

    # 历史名称兼容: 旧 __init__.py 引用了不存在的 DecisionTheoryEngine
    # 为避免破坏历史调用链, 别名指向融合引擎 (功能最接近)
    DecisionTheoryEngine = TheoryFusionEngine

    __all__ = [
        "BuffettMungerFramework",
        "DalioEconomicMachine",
        "DecisionTheoryEngine",  # 历史兼容别名
        "FirstPrinciplesAnalyzer",
        "SorosReflexivityEngine",
        "TheoryDecision",
        "TheoryFusionEngine",
        "run_full_theory_analysis",
    ]
    logger.debug("decision_theories re-export 从 utils/alpha/ 加载成功")
except ImportError as e:
    logger.warning("utils.alpha.decision_theories 导入失败, 历史路径降级: %s", e)

    # 降级占位符 (避免 import 错误阻断调用链)
    class _StubClass:  # type: ignore
        """导入失败时的占位符, 调用时抛 ImportError."""

        def __init__(self, *args, **kwargs):
            raise ImportError(
                "decision_theories 不可用: utils.alpha.decision_theories 导入失败. 请检查 Python 环境和 sys.path 配置."
            )

    TheoryDecision = _StubClass  # type: ignore
    SorosReflexivityEngine = _StubClass  # type: ignore
    DalioEconomicMachine = _StubClass  # type: ignore
    FirstPrinciplesAnalyzer = _StubClass  # type: ignore
    BuffettMungerFramework = _StubClass  # type: ignore
    TheoryFusionEngine = _StubClass  # type: ignore
    DecisionTheoryEngine = _StubClass  # type: ignore

    def run_full_theory_analysis(*args, **kwargs):  # type: ignore
        raise ImportError("run_full_theory_analysis 不可用: utils.alpha.decision_theories 导入失败.")

    __all__ = [
        "BuffettMungerFramework",
        "DalioEconomicMachine",
        "DecisionTheoryEngine",
        "FirstPrinciplesAnalyzer",
        "SorosReflexivityEngine",
        "TheoryDecision",
        "TheoryFusionEngine",
        "run_full_theory_analysis",
    ]
