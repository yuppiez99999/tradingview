# -*- coding: utf-8 -*-
"""T5.4 日级报告生成器主类 — Facade 模式 + Feature Flag 透传.

从 daily_workflow.phase_report (615 行) 抽取为独立模块, 提供:
  1. DailyReportGenerator 主类 (Facade 模式)
  2. generate() 入口生成完整日级报告 (Markdown + JSON)
  3. Feature Flag USE_DAILY_REPORT_GENERATOR (默认 False, HC-1 透传)
  4. ConfigManager 4 级优先级 (HC-5)
  5. 各子模块独立初始化, 单一失败不阻塞主流程 (HC-2)

设计原则:
  1. daily_workflow.py 保持只读 (不修改原文件)
  2. 新模块提供等价 API, 双签启用后 daily_workflow.py 可选择性调用
  3. 纯函数 + 主类双层 API:
     - 纯函数 (report_sections.py): 可独立测试, 无外部依赖
     - 主类 (本文件): 集成 Feature Flag / ConfigManager / 日志

模块整合 8.4 — ARCHITECTURE §5
"""

from __future__ import annotations

import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

# 项目根目录
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.reporting.report_sections import (  # noqa: E402
    # 常量
    build_report_header,
    build_phase_execution_summary,
    render_phase_summary,
    render_pnl_attribution,
    render_barra_decomposition,
    render_eod_guard_chain,
    build_report_summary,
    write_report_with_retry,
    save_state_json,
)

logger = logging.getLogger("daily_report_generator")


# ============================================================
# 常量
# ============================================================

# Feature Flag 名称
FLAG_NAME = "USE_DAILY_REPORT_GENERATOR"

# ConfigManager 配置名
DEFAULT_CONFIG_NAME = "daily_report_generator"

# 默认报告目录 (相对项目根)
DEFAULT_REPORT_DIR = "reports/daily_workflow"

# 默认文件名模板
DEFAULT_MD_TEMPLATE = "v75_daily_workflow_{date}.md"
DEFAULT_JSON_TEMPLATE = "v75_daily_workflow_{date}.json"

# 状态码
STATUS_OK = "ok"
STATUS_FEATURE_FLAG_DISABLED = "feature_flag_disabled"
STATUS_EMPTY_INPUT = "empty_input"
STATUS_ERROR = "error"
STATUS_PARTIAL = "partial"


# ============================================================
# 异常
# ============================================================


class DailyReportGeneratorError(Exception):
    """日级报告生成器基础异常."""

    def __init__(self, message: str, reason: str = "", cause: Optional[Exception] = None):
        super().__init__(message)
        self.reason = reason
        self.cause = cause


class ReportWriteError(DailyReportGeneratorError):
    """报告写入失败."""


class FeatureFlagError(DailyReportGeneratorError):
    """Feature Flag 查询异常."""


# ============================================================
# 输入数据类
# ============================================================


@dataclass
class ReportInput:
    """日级报告生成输入容器.

    将 daily_workflow.py 的分散状态聚合为统一输入,
    使报告生成逻辑可独立测试 (不依赖 self.state).

    Attributes:
        trade_date: 交易日期 (YYYY-MM-DD)
        capital: 资金规模
        dry_run: 干跑模式
        sim_mode: 模拟盘模式
        live_mode: 实盘模式
        phases_state: 各阶段状态字典
        pnl_attribution_result: PnL 归因结果 (可选)
        barra_result: Barra 风险分解结果 (可选)
        guard_results: EOD 七 Guard 风控链结果 (可选)
        executed_phases: 实际执行的阶段顺序 (可选)
    """

    trade_date: str = ""
    capital: float = 0.0
    dry_run: bool = False
    sim_mode: bool = False
    live_mode: bool = False
    phases_state: Dict[str, Any] = field(default_factory=dict)
    pnl_attribution_result: Optional[Dict[str, Any]] = None
    barra_result: Optional[Dict[str, Any]] = None
    guard_results: Optional[Dict[str, Any]] = None
    executed_phases: Optional[Sequence[str]] = None

    def is_empty(self) -> bool:
        """检查输入是否全为空."""
        return (
            not self.phases_state
            and self.pnl_attribution_result is None
            and self.barra_result is None
            and self.guard_results is None
        )


# ============================================================
# 输出数据类
# ============================================================


@dataclass
class ReportResult:
    """日级报告生成结果.

    Attributes:
        report_path: Markdown 报告文件路径
        state_path: JSON 状态文件路径 (可选)
        markdown_content: Markdown 内容 (字符串)
        lines: Markdown 行列表
        status: 生成状态 (ok/feature_flag_disabled/empty_input/error)
        reason: 状态说明
        generation_time_ms: 生成耗时 (毫秒)
        generated_at: 生成时间戳 (ISO 格式)
        config_source: 配置文件来源路径
        feature_flag_name: Feature Flag 名称
    """

    report_path: Optional[Path] = None
    state_path: Optional[Path] = None
    markdown_content: str = ""
    lines: List[str] = field(default_factory=list)
    status: str = STATUS_OK
    reason: str = ""
    generation_time_ms: float = 0.0
    generated_at: str = ""
    config_source: str = ""
    feature_flag_name: str = FLAG_NAME

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典 (供 JSON 输出)."""
        return {
            "report_path": str(self.report_path) if self.report_path else None,
            "state_path": str(self.state_path) if self.state_path else None,
            "status": self.status,
            "reason": self.reason,
            "generation_time_ms": round(self.generation_time_ms, 2),
            "generated_at": self.generated_at,
            "config_source": self.config_source,
            "feature_flag_name": self.feature_flag_name,
            "lines_count": len(self.lines),
            "markdown_length": len(self.markdown_content),
        }


# ============================================================
# 主类
# ============================================================


class DailyReportGenerator:
    """日级报告生成器主类 (Facade 模式 + HC-1 + HC-2 + HC-5).

    集成 report_sections.py 的 6 个纯函数, 提供:
      1. Feature Flag 透传 (USE_DAILY_REPORT_GENERATOR 默认 False)
      2. ConfigManager 4 级优先级配置加载
      3. 完整报告生成 (Markdown + JSON 双输出)
      4. 子模块独立降级 (PnL/Barra/Guard 任一缺失不阻塞)

    Usage:
        >>> generator = DailyReportGenerator()
        >>> result = generator.generate(ReportInput(trade_date="2026-07-27"))
        >>> print(result.report_path)
    """

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        config_name: str = DEFAULT_CONFIG_NAME,
        report_dir: Optional[Path] = None,
        feature_flag_name: str = FLAG_NAME,
    ) -> None:
        """初始化日级报告生成器.

        Args:
            config: 显式配置字典 (优先级最高, 用于测试注入)
            config_name: ConfigManager 配置短名
            report_dir: 报告输出目录 (覆盖配置)
            feature_flag_name: Feature Flag 名称
        """
        self._feature_flag_name = feature_flag_name

        # 加载配置 (HC-5: ConfigManager 4 级优先级)
        if config is not None:
            self._config = config
            self._config_source = "explicit_dict"
        else:
            self._config = self._load_config(config_name)
            self._config_source = self._resolve_config_source(config_name)

        # 解析配置项
        settings = self._config.get("settings", {}) if isinstance(self._config, dict) else {}
        self._report_dir = Path(report_dir) if report_dir else Path(settings.get("report_dir", DEFAULT_REPORT_DIR))
        self._md_template = settings.get("markdown_filename_template", DEFAULT_MD_TEMPLATE)
        self._json_template = settings.get("json_filename_template", DEFAULT_JSON_TEMPLATE)
        self._save_json = bool(settings.get("save_state_json", True))
        self._max_retries = int(settings.get("write_max_retries", 3))
        self._retry_delay = float(settings.get("write_retry_delay_seconds", 0.5))

        logger.debug(
            f"[DailyReportGenerator] 初始化完成, report_dir={self._report_dir}, config_source={self._config_source}"
        )

    # ============================================================
    # 公共 API
    # ============================================================

    def generate(
        self,
        input_data: Optional[ReportInput] = None,
        trade_date: str = "",
        capital: float = 0.0,
        dry_run: bool = False,
        sim_mode: bool = False,
        live_mode: bool = False,
        phases_state: Optional[Dict[str, Any]] = None,
        pnl_attribution_result: Optional[Dict[str, Any]] = None,
        barra_result: Optional[Dict[str, Any]] = None,
        guard_results: Optional[Dict[str, Any]] = None,
        save: bool = True,
    ) -> ReportResult:
        """生成日级报告.

        Args:
            input_data: 统一输入容器 (与下面参数互斥, 优先使用)
            trade_date: 交易日期
            capital: 资金规模
            dry_run: 干跑模式
            sim_mode: 模拟盘模式
            live_mode: 实盘模式
            phases_state: 各阶段状态字典
            pnl_attribution_result: PnL 归因结果
            barra_result: Barra 风险分解结果
            guard_results: EOD 七 Guard 风控链结果
            save: 是否保存到文件 (默认 True)

        Returns:
            ReportResult
        """
        start_time = time.perf_counter()

        # 处理统一输入容器
        if input_data is not None:
            trade_date = input_data.trade_date or trade_date
            capital = input_data.capital or capital
            dry_run = dry_run or input_data.dry_run
            sim_mode = sim_mode or input_data.sim_mode
            live_mode = live_mode or input_data.live_mode
            phases_state = phases_state or input_data.phases_state
            pnl_attribution_result = pnl_attribution_result or input_data.pnl_attribution_result
            barra_result = barra_result or input_data.barra_result
            guard_results = guard_results or input_data.guard_results

        # 默认日期
        if not trade_date:
            trade_date = datetime.now().strftime("%Y-%m-%d")

        # HC-1: Feature Flag 透传
        if not self._is_feature_flag_enabled():
            return self._build_disabled_result(trade_date, start_time)

        # 空输入检查
        if not phases_state and not pnl_attribution_result and not barra_result and not guard_results:
            return self._build_empty_result(trade_date, start_time)

        # 生成报告
        try:
            result = self._generate_internal(
                trade_date=trade_date,
                capital=capital,
                dry_run=dry_run,
                sim_mode=sim_mode,
                live_mode=live_mode,
                phases_state=phases_state or {},
                pnl_attribution_result=pnl_attribution_result,
                barra_result=barra_result,
                guard_results=guard_results,
                save=save,
                start_time=start_time,
            )
            return result
        except Exception as e:  # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            logger.error(f"[DailyReportGenerator] 报告生成失败: {e}", exc_info=True)
            return ReportResult(
                status=STATUS_ERROR,
                reason=f"生成异常: {type(e).__name__}: {e}",
                generation_time_ms=elapsed_ms,
                generated_at=datetime.now().isoformat(timespec="seconds"),
                config_source=self._config_source,
                feature_flag_name=self._feature_flag_name,
            )

    def is_enabled(self) -> bool:
        """查询 Feature Flag 是否启用 (HC-1)."""
        return self._is_feature_flag_enabled()

    # ============================================================
    # 内部方法
    # ============================================================

    def _generate_internal(
        self,
        trade_date: str,
        capital: float,
        dry_run: bool,
        sim_mode: bool,
        live_mode: bool,
        phases_state: Dict[str, Any],
        pnl_attribution_result: Optional[Dict[str, Any]],
        barra_result: Optional[Dict[str, Any]],
        guard_results: Optional[Dict[str, Any]],
        save: bool,
        start_time: float,
    ) -> ReportResult:
        """内部生成逻辑 (已通过 Feature Flag 和空输入检查)."""
        # 组装报告行
        lines: List[str] = []

        # 段 1+2: 头部 + 阶段摘要
        lines.extend(
            build_report_header(
                trade_date=trade_date,
                capital=capital,
                dry_run=dry_run,
                sim_mode=sim_mode,
                live_mode=live_mode,
            )
        )
        lines.extend(build_phase_execution_summary(phases_state))

        # 段 4+5+6: 各阶段详情
        lines.extend(render_phase_summary(phases_state))

        # 段 8 上: P&L 归因
        lines.extend(render_pnl_attribution(pnl_attribution_result))

        # 段 8 下: Barra 分解
        lines.extend(render_barra_decomposition(barra_result))

        # 段 9 上: EOD 七 Guard
        lines.extend(render_eod_guard_chain(guard_results))

        # 段 9 中: 总结
        lines.extend(build_report_summary(phases_state, pnl_attribution_result))

        # 计算耗时
        elapsed_ms = (time.perf_counter() - start_time) * 1000

        # 构建结果
        result = ReportResult(
            lines=lines,
            markdown_content="\n".join(lines),
            status=STATUS_OK,
            reason="报告生成成功",
            generation_time_ms=elapsed_ms,
            generated_at=datetime.now().isoformat(timespec="seconds"),
            config_source=self._config_source,
            feature_flag_name=self._feature_flag_name,
        )

        # 保存文件
        if save:
            try:
                md_filename = self._md_template.format(date=trade_date.replace("-", ""))
                md_path = self._report_dir / md_filename
                result.report_path = write_report_with_retry(
                    md_path, lines, max_retries=self._max_retries, retry_delay_seconds=self._retry_delay
                )

                if self._save_json:
                    json_filename = self._json_template.format(date=trade_date.replace("-", ""))
                    json_path = self._report_dir / json_filename
                    result.state_path = save_state_json(
                        json_path,
                        phases_state,
                        extra_fields={
                            "trade_date": trade_date,
                            "generated_at": result.generated_at,
                            "generation_time_ms": result.generation_time_ms,
                        },
                    )
            except OSError as e:
                # 写入失败不抛异常, 仅标记状态
                result.status = STATUS_PARTIAL
                result.reason = f"报告生成成功但写入失败: {e}"
                logger.error(f"[DailyReportGenerator] 文件写入失败: {e}")

        return result

    def _is_feature_flag_enabled(self) -> bool:
        """查询 Feature Flag (HC-1: 默认 False)."""
        try:
            from utils.infra.feature_flags import FeatureFlags

            return bool(FeatureFlags.is_enabled(self._feature_flag_name))  # type: ignore
        except Exception as e:  # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化
            logger.debug(f"[DailyReportGenerator] Feature Flag 查询失败 (默认 False): {e}")
            return False

    def _build_disabled_result(self, trade_date: str, start_time: float) -> ReportResult:
        """构建 Feature Flag 关闭时的降级结果."""
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        return ReportResult(
            status=STATUS_FEATURE_FLAG_DISABLED,
            reason=f"Feature Flag {self._feature_flag_name}=False (HC-1 透传)",
            generation_time_ms=elapsed_ms,
            generated_at=datetime.now().isoformat(timespec="seconds"),
            config_source=self._config_source,
            feature_flag_name=self._feature_flag_name,
        )

    def _build_empty_result(self, trade_date: str, start_time: float) -> ReportResult:
        """构建空输入结果."""
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        return ReportResult(
            status=STATUS_EMPTY_INPUT,
            reason="全部输入为空 (phases_state/pnl/barra/guard 均无)",
            generation_time_ms=elapsed_ms,
            generated_at=datetime.now().isoformat(timespec="seconds"),
            config_source=self._config_source,
            feature_flag_name=self._feature_flag_name,
        )

    def _load_config(self, config_name: str) -> Dict[str, Any]:
        """加载配置 (HC-5: ConfigManager 4 级优先级)."""
        try:
            from utils.config_manager import get_config

            cfg = get_config(config_name)
            if cfg:
                return cfg
        except Exception as exc:  # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化
            logger.warning(f"[DailyReportGenerator] 加载配置失败 (name={config_name}): {exc}")
        return {}

    def _resolve_config_source(self, config_name: str) -> str:
        """解析配置文件来源路径 (用于审计)."""
        try:
            from utils.config_manager import get_config_source

            source = get_config_source(config_name)
            return str(source) if source else "config_manager_miss"
        except Exception:  # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化
            return "config_manager_unavailable"


# ============================================================
# 模块级便捷函数
# ============================================================


def is_daily_report_generator_enabled() -> bool:
    """查询日级报告生成器 Feature Flag 是否启用 (HC-1)."""
    try:
        from utils.infra.feature_flags import FeatureFlags

        return bool(FeatureFlags.is_enabled(FLAG_NAME))  # type: ignore
    except Exception:  # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化
        return False


def create_default_generator() -> DailyReportGenerator:
    """创建默认配置的生成器实例."""
    return DailyReportGenerator()


def generate_daily_report(
    input_data: Optional[ReportInput] = None,
    save: bool = True,
    **kwargs: Any,
) -> ReportResult:
    """便捷函数: 生成日级报告.

    Args:
        input_data: 统一输入容器 (与 kwargs 互斥, 优先使用)
        save: 是否保存到文件
        **kwargs: 与 ReportInput 字段同名的参数

    Returns:
        ReportResult
    """
    generator = DailyReportGenerator()
    return generator.generate(input_data=input_data, save=save, **kwargs)


# ============================================================
# 模块导出
# ============================================================

__all__ = [
    "DEFAULT_CONFIG_NAME",
    "DEFAULT_JSON_TEMPLATE",
    "DEFAULT_MD_TEMPLATE",
    "DEFAULT_REPORT_DIR",
    # 常量
    "FLAG_NAME",
    "STATUS_EMPTY_INPUT",
    "STATUS_ERROR",
    "STATUS_FEATURE_FLAG_DISABLED",
    "STATUS_OK",
    "STATUS_PARTIAL",
    # 主类
    "DailyReportGenerator",
    # 异常
    "DailyReportGeneratorError",
    "FeatureFlagError",
    # 数据类
    "ReportInput",
    "ReportResult",
    "ReportWriteError",
    "create_default_generator",
    "generate_daily_report",
    # 便捷函数
    "is_daily_report_generator_enabled",
]
