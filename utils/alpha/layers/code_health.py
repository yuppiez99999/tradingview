"""代码层健康度采集器 — 三层面自我进化 Stage 1.

模块整合 8.4 — ARCHITECTURE_三层面进化 §第1阶段
复用: SystemChecker (C1-C9, 40+ 检查项) + mypy/pylint 配置 + _verify 脚本覆盖度

设计原则:
    1. Feature Flag (HC-1): USE_CODE_HEALTH_LAYER 默认 False
    2. 配置走 ConfigManager (HC-5): get_config("evolution").code_health
    3. 只读: SystemChecker 用 skip_datasource=True (不触发数据源 IO)
    4. 容错降级: SystemChecker 失败不阻塞, 返回降级 LayerScore
    5. 不可变: 返回 LayerScore(frozen=True)

子指标 (各 0.0-1.0, 权重走 evolution.yaml):
    p0_pass_rate (0.40): SystemChecker.all_passed 比例 (ERROR 级通过率)
    blocking_failures (0.20): 1 - blocking_failures/MAX (MAX=40)
    static_analysis (0.20): mypy.ini + .pylintrc 配置就绪度
    verify_scripts (0.20): scripts/_verify_*.py 覆盖度 (文件数/20)

用法:
    from utils.alpha.layers.code_health import CodeHealthLayer
    layer = CodeHealthLayer()
    score = layer.collect()
    # score.score → 代码层健康度 (0.0-1.0)
"""

from __future__ import annotations

import contextlib
import io
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# 复用 health_metrics 的 LayerScore (health_metrics 模块在 collect() 调用前已完全加载)
from utils.alpha.health_metrics import LayerScore  # noqa: E402

# ============================================================
# 常量
# ============================================================

DEFAULT_WEIGHTS: dict[str, float] = {
    "p0_pass_rate": 0.40,
    "blocking_failures": 0.20,
    "static_analysis": 0.20,
    "verify_scripts": 0.20,
}

# blocking_failures 归一化上限 (40 项检查)
MAX_BLOCKING_FAILURES = 40

# _verify 脚本覆盖度归一化上限 (期望至少 20 个验证脚本)
EXPECTED_VERIFY_SCRIPTS = 20


class CodeHealthLayer:
    """代码层健康度采集器.

    Feature Flag: USE_CODE_HEALTH_LAYER (默认 False, HC-1)
    配置: evolution.yaml → code_health (HC-5)
    """

    FLAG_NAME = "USE_CODE_HEALTH_LAYER"

    def __init__(
        self,
        weights: dict[str, float] | None = None,
        feature_flag_name: str = FLAG_NAME,
    ) -> None:
        self.feature_flag_name = feature_flag_name
        self._enabled = self._check_feature_flag(feature_flag_name)
        self.weights = weights if weights is not None else self._load_weights()
        self._project_root = _PROJECT_ROOT

        logger.info(
            "CodeHealthLayer 初始化: enabled=%s (flag=%s)",
            self._enabled,
            feature_flag_name,
        )

    # ============================================================
    # Feature Flag (HC-1)
    # ============================================================
    @staticmethod
    def _check_feature_flag(flag_name: str) -> bool:
        try:
            from utils.infra.feature_flags import is_enabled

            return bool(is_enabled(flag_name))
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ) as e:
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.warning("Feature Flag 检查失败 (降级 False): %s — %s", flag_name, e)
            return False

    # ============================================================
    # 配置加载 (HC-5)
    # ============================================================
    def _load_weights(self) -> dict[str, float]:
        try:
            from utils.config_manager import get_config

            cfg = get_config("evolution") or {}
            w = (cfg.get("code_health", {}) or {}).get("weights", {})
            if w:
                return {
                    k: float(w.get(k, DEFAULT_WEIGHTS.get(k, 0.0)))
                    for k in DEFAULT_WEIGHTS
                }
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ) as e:
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.warning("CodeHealth 权重加载失败, 用默认值: %s", e)
        return dict(DEFAULT_WEIGHTS)

    # ============================================================
    # 核心: 采集代码层健康度
    # ============================================================
    def collect(self) -> LayerScore:
        """采集代码层指标. 失败时返回降级 LayerScore."""
        now = datetime.now(UTC).isoformat()

        if not self._enabled:
            return LayerScore(
                layer="code",
                score=0.0,
                is_degraded=True,
                degraded_reason="FEATURE_FLAG_DISABLED",
                collected_at=now,
            )

        # 采集子指标 (各自容错)
        p0_report = self._run_system_check()
        p0_pass_rate = self._calc_p0_pass_rate(p0_report)
        blocking_score = self._calc_blocking_score(p0_report)
        static_score = self._calc_static_analysis_score()
        verify_score = self._calc_verify_scripts_score()

        sub_metrics = {
            "p0_pass_rate": p0_pass_rate,
            "blocking_failures": blocking_score,
            "static_analysis": static_score,
            "verify_scripts": verify_score,
        }

        # 加权求和
        score = (
            self.weights.get("p0_pass_rate", 0.40) * p0_pass_rate
            + self.weights.get("blocking_failures", 0.20) * blocking_score
            + self.weights.get("static_analysis", 0.20) * static_score
            + self.weights.get("verify_scripts", 0.20) * verify_score
        )
        score = max(0.0, min(1.0, score))

        return LayerScore(
            layer="code",
            score=score,
            sub_metrics=sub_metrics,
            is_degraded=False,
            collected_at=now,
        )

    # ============================================================
    # 子指标采集
    # ============================================================
    def _run_system_check(self) -> Any | None:
        """运行 SystemChecker (skip_datasource=True, 捕获 stdout)."""
        try:
            from utils.system_check import SystemChecker

            checker = SystemChecker(strict=False, skip_datasource=True)
            # 捕获 print 输出 (SystemChecker 有 print 语句)
            with contextlib.redirect_stdout(io.StringIO()):
                report = checker.run_all()
            return report
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ) as e:
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.warning("SystemChecker 运行失败 (代码层降级): %s", e)
            return None

    @staticmethod
    def _calc_p0_pass_rate(report: Any | None) -> float:
        """计算 P0 检查通过率 (ERROR 级通过数/总 ERROR 级数)."""
        if report is None:
            return 0.0
        try:
            results = getattr(report, "results", [])

            def _field_text(r: Any, field: str) -> str:
                """字段文本: 枚举取 .value, 字符串取自身, 缺失返回空串."""
                raw = getattr(r, field, "")
                return str(getattr(raw, "value", raw))

            error_items = [
                r
                for r in results
                if _field_text(r, "level") in ("ERROR", "CheckLevel.ERROR")
            ]
            if not error_items:
                return 1.0  # 无 ERROR 项视为全过
            passed = sum(
                1
                for r in error_items
                if _field_text(r, "status") in ("PASS", "CheckStatus.PASS")
            )
            return passed / len(error_items)
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ):  # 兜底: 用 all_passed
            return 1.0 if getattr(report, "all_passed", False) else 0.5

    @staticmethod
    def _calc_blocking_score(report: Any | None) -> float:
        """计算 blocking_failures 倒数分 (0 失败=1.0, 40 失败=0.0)."""
        if report is None:
            return 0.0
        try:
            bf = getattr(report, "blocking_failures", 0)
            return max(0.0, 1.0 - bf / MAX_BLOCKING_FAILURES)
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ):
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            return 0.5

    def _calc_static_analysis_score(self) -> float:
        """计算静态分析配置就绪度 (mypy.ini + .pylintrc 存在性)."""
        try:
            mypy_ini = self._project_root / "mypy.ini"
            pylintrc = self._project_root / ".pylintrc"
            score = 0.0
            if mypy_ini.exists():
                score += 0.5
            if pylintrc.exists():
                score += 0.5
            return score
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ):
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            return 0.0

    def _calc_verify_scripts_score(self) -> float:
        """计算验证脚本覆盖度 (scripts/_verify_*.py 文件数/20)."""
        try:
            scripts_dir = self._project_root / "scripts"
            if not scripts_dir.exists():
                return 0.0
            verify_count = len(list(scripts_dir.glob("_verify_*.py")))
            return min(1.0, verify_count / EXPECTED_VERIFY_SCRIPTS)
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ):
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            return 0.0
