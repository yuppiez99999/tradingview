"""mypy/pylint 输出解析器 — 三层面自我进化 Stage 2 增强工具.

模块整合 8.4 — ARCHITECTURE_三层面进化 §第2阶段 (2.6)
用途: 解析 mypy/pylint 静态分析输出, 生成结构化 RootCause

设计原则:
    1. 纯解析: 不运行 mypy/pylint 命令 (避免慢 IO), 仅解析文本
    2. 容错降级: 解析失败返回空列表, 不阻塞
    3. 结构化输出: StaticError → RootCause (evidence 含 file/line/code)
    4. 不可变: StaticError(frozen=True)
    5. 可选集成: CodeDiagnoser 可调用 to_root_causes() 补充诊断

支持的输出格式:
    mypy:   file.py:123: error: message  [error-code]
            file.py:123: note: message
    pylint: file.py:123: C0114: message (missing-module-docstring)
            file.py:123:45: E1101: message (no-member)

用法:
    from utils.alpha.layers.static_parser import StaticParser
    parser = StaticParser()
    # 解析 mypy 输出
    errors = parser.parse_mypy(mypy_output_text)
    # 解析 pylint 输出
    errors = parser.parse_pylint(pylint_output_text)
    # 转 RootCause
    causes = parser.to_root_causes(errors)
"""
from __future__ import annotations

import logging
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.alpha.root_cause import (  # noqa: E402
    ACTION_MANUAL,
    LAYER_CODE,
    SEVERITY_HIGH,
    SEVERITY_LOW,
    SEVERITY_MEDIUM,
    FixSuggestion,
    RootCause,
)

# ============================================================
# 常量
# ============================================================

# mypy 输出正则: file.py:123: error: message  [code]
_MYPY_PATTERN = re.compile(
    r"^(?P<file>[^:]+):"
    r"(?P<line>\d+):"
    r"(?:(?P<col>\d+):)?"
    r"\s*(?P<severity>error|note|warning):\s*"
    r"(?P<message>.+?)"
    r"(?:\s+\[(?P<code>[^\]]+)\])?\s*$"
)

# pylint 输出正则: file.py:123: C0114: message (symbol)
# 或 file.py:123:45: E1101: message (symbol)
_PYLINT_PATTERN = re.compile(
    r"^(?P<file>[^:]+):"
    r"(?P<line>\d+):"
    r"(?:(?P<col>\d+):)?"
    r"\s*(?P<code>[A-Z]\d+):"
    r"\s*(?P<message>.+?)"
    r"(?:\s+\((?P<symbol>[^)]+)\))?\s*$"
)

# pylint 错误类型前缀 → severity 映射
_PYLINT_SEVERITY_MAP: dict[str, str] = {
    "E": SEVERITY_HIGH,    # Error
    "F": SEVERITY_HIGH,    # Fatal
    "W": SEVERITY_MEDIUM,  # Warning
    "C": SEVERITY_LOW,     # Convention
    "R": SEVERITY_LOW,     # Refactor
    "I": SEVERITY_LOW,     # Info
}

# mypy severity 映射
_MYPY_SEVERITY_MAP: dict[str, str] = {
    "error": SEVERITY_HIGH,
    "warning": SEVERITY_MEDIUM,
    "note": SEVERITY_LOW,
}


# ============================================================
# 数据类 (不可变)
# ============================================================


@dataclass(frozen=True)
class StaticError:
    """单条静态分析错误 (不可变).

    Attributes:
        tool: 工具名 ("mypy" / "pylint")
        file: 文件路径
        line: 行号
        col: 列号 (0=未指定)
        severity: 严重程度 (critical/high/medium/low)
        code: 错误代码 (如 "attr-defined", "E1101")
        message: 错误消息
        symbol: pylint 符号名 (如 "no-member"), mypy 为空
    """
    tool: str
    file: str
    line: int
    col: int = 0
    severity: str = SEVERITY_MEDIUM
    code: str = ""
    message: str = ""
    symbol: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "file": self.file,
            "line": self.line,
            "col": self.col,
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "symbol": self.symbol,
        }


# ============================================================
# 解析器
# ============================================================


class StaticParser:
    """mypy/pylint 输出解析器 (纯解析, 不运行命令).

    接口:
        parse_mypy(text) → List[StaticError]
        parse_pylint(text) → List[StaticError]
        to_root_causes(errors) → List[RootCause]
        parse_reports(reports_dir) → List[RootCause] (可选, 解析报告文件)
    """

    def __init__(self, max_errors: int = 100) -> None:
        """初始化.

        Args:
            max_errors: 单次解析最大错误数 (防止超大输出 OOM)
        """
        self.max_errors = int(max_errors)

    # ============================================================
    # mypy 解析
    # ============================================================
    def parse_mypy(self, text: str) -> list[StaticError]:
        """解析 mypy 输出文本.

        Args:
            text: mypy stdout/stderr 文本

        Returns:
            List[StaticError] 解析出的错误列表
        """
        errors: list[StaticError] = []
        if not text:
            return errors
        try:
            for line in text.strip().splitlines():
                if len(errors) >= self.max_errors:
                    logger.warning("mypy 错误数超上限 %d, 截断", self.max_errors)
                    break
                err = self._parse_mypy_line(line)
                if err is not None:
                    errors.append(err)
        except Exception as e:
            logger.warning("mypy 输出解析失败 (降级为空): %s", e)
        return errors

    @staticmethod
    def _parse_mypy_line(line: str) -> StaticError | None:
        """解析单行 mypy 输出."""
        m = _MYPY_PATTERN.match(line.strip())
        if m is None:
            return None
        severity_str = m.group("severity").lower()
        severity = _MYPY_SEVERITY_MAP.get(severity_str, SEVERITY_MEDIUM)
        col_str = m.group("col")
        return StaticError(
            tool="mypy",
            file=m.group("file"),
            line=int(m.group("line")),
            col=int(col_str) if col_str else 0,
            severity=severity,
            code=m.group("code") or "",
            message=m.group("message").strip(),
            symbol="",
        )

    # ============================================================
    # pylint 解析
    # ============================================================
    def parse_pylint(self, text: str) -> list[StaticError]:
        """解析 pylint 输出文本.

        Args:
            text: pylint stdout/stderr 文本

        Returns:
            List[StaticError] 解析出的错误列表
        """
        errors: list[StaticError] = []
        if not text:
            return errors
        try:
            for line in text.strip().splitlines():
                if len(errors) >= self.max_errors:
                    logger.warning("pylint 错误数超上限 %d, 截断", self.max_errors)
                    break
                err = self._parse_pylint_line(line)
                if err is not None:
                    errors.append(err)
        except Exception as e:
            logger.warning("pylint 输出解析失败 (降级为空): %s", e)
        return errors

    @staticmethod
    def _parse_pylint_line(line: str) -> StaticError | None:
        """解析单行 pylint 输出."""
        m = _PYLINT_PATTERN.match(line.strip())
        if m is None:
            return None
        code = m.group("code")
        # pylint 代码首字母决定 severity (E/F/W/C/R/I)
        severity = _PYLINT_SEVERITY_MAP.get(code[0], SEVERITY_MEDIUM) if code else SEVERITY_MEDIUM
        col_str = m.group("col")
        return StaticError(
            tool="pylint",
            file=m.group("file"),
            line=int(m.group("line")),
            col=int(col_str) if col_str else 0,
            severity=severity,
            code=code,
            message=m.group("message").strip(),
            symbol=m.group("symbol") or "",
        )

    # ============================================================
    # 转 RootCause
    # ============================================================
    def to_root_causes(
        self,
        errors: list[StaticError],
        now: str | None = None,
    ) -> list[RootCause]:
        """将 StaticError 列表转为 RootCause 列表.

        Args:
            errors: StaticError 列表
            now: 检测时间 (None=当前时间)

        Returns:
            List[RootCause] 代码层根因列表
        """
        if now is None:
            now = datetime.now(timezone.utc).isoformat()

        causes: list[RootCause] = []
        for err in errors:
            try:
                cause = RootCause(
                    cause_id=f"code-static-{err.tool}-{err.file}-{err.line}-{now}",
                    layer=LAYER_CODE,
                    category=f"static_analysis_{err.tool}",
                    severity=err.severity,
                    evidence={
                        "tool": err.tool,
                        "file": err.file,
                        "line": err.line,
                        "col": err.col,
                        "code": err.code,
                        "message": err.message,
                        "symbol": err.symbol,
                        "source": f"{err.tool}_output",
                    },
                    suggested_fix=FixSuggestion(
                        action_type=ACTION_MANUAL,
                        target_file=err.file,
                        description=(
                            f"{err.tool} {err.code or err.symbol}: "
                            f"{err.message} ({err.file}:{err.line})"
                        ),
                        estimated_risk=0.5,
                        requires_human_approval=True,
                        remediation_commands=[
                            f"# 修复 {err.file}:{err.line} 的 {err.tool} {err.code or err.symbol}",
                        ],
                    ),
                    confidence=0.85,
                    detected_at=now,
                )
                causes.append(cause)
            except Exception as e:
                logger.warning("StaticError 转 RootCause 失败 (跳过): %s", e)
        return causes

    # ============================================================
    # 解析报告文件 (可选)
    # ============================================================
    def parse_reports(self, reports_dir: Path) -> list[RootCause]:
        """解析报告目录中的 mypy/pylint 输出文件.

        查找:
            reports_dir/mypy_*.txt
            reports_dir/pylint_*.txt

        Args:
            reports_dir: 报告目录

        Returns:
            List[RootCause] 所有报告的根因列表
        """
        causes: list[RootCause] = []
        try:
            if not reports_dir.exists():
                return causes
            # mypy 报告
            for f in sorted(reports_dir.glob("mypy_*.txt")):
                try:
                    text = f.read_text(encoding="utf-8", errors="ignore")
                    errors = self.parse_mypy(text)
                    causes.extend(self.to_root_causes(errors))
                except Exception as e:
                    logger.warning("解析 mypy 报告失败 %s: %s", f.name, e)
            # pylint 报告
            for f in sorted(reports_dir.glob("pylint_*.txt")):
                try:
                    text = f.read_text(encoding="utf-8", errors="ignore")
                    errors = self.parse_pylint(text)
                    causes.extend(self.to_root_causes(errors))
                except Exception as e:
                    logger.warning("解析 pylint 报告失败 %s: %s", f.name, e)
        except Exception as e:
            logger.warning("解析报告目录失败 (降级为空): %s", e)
        return causes
