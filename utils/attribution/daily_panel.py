"""T5.3 日级归因面板 — Brinson + Barra + TCA 三合一.

对冲基金 L7 归因层核心模块, 整合三套独立归因系统输出日级统一报告:
  - Brinson 归因 (T5.1): 行业配置/选股/交互三效应
  - Barra 因子归因 (T5.2): 10 风格因子 + 8 行业因子 PnL 拆分
  - TCA 执行归因 (T3.5): Alpha/Execution/Risk 三段式 PnL 拆分

输出双格式:
  - JSON: 结构化数据, 供 Streamlit 面板消费 (reports/attribution/daily_panel_{date}.json)
  - Markdown: 人类可读报告 (reports/attribution/daily_panel_{date}.md)

设计原则:
  1. Facade 模式: 不修改 BrinsonAttributionManager / FactorAttributionManager / PostTradeAttribution
  2. 容错降级: 任一子模块失败/降级不阻塞整体报告生成, 标记为 "degraded"
  3. HC-1 Feature Flag 透传: USE_DAILY_ATTRIBUTION_PANEL 默认 False, 关闭时返回全零降级结果
  4. HC-5 ConfigManager 4 级优先级: v8.3_institutional/config/daily_panel.yaml
  5. 子模块独立 Feature Flag: Brinson/Factor/TCA 各自独立, 聚合层容忍任一关闭
  6. 性能: 生成时间 < 30s (验收标准)

用法:
    from utils.attribution.daily_panel import DailyAttributionPanel, DailyReportInput

    panel = DailyAttributionPanel()
    report = panel.generate(
        attribution_date="2026-07-27",
        brinson_input=DailyReportInput(...),
        factor_input=DailyReportInput(...),
        tca_input=DailyReportInput(...),
    )
    panel.save(report)

硬约束:
  - HC-1: flag=False 时返回 DailyAttributionReport(status="feature_flag_disabled") 全零结果
  - HC-5: 配置走 ConfigManager 4 级优先级
  - 生成时间 < 30s
"""

from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, cast

from utils.config_manager import get_config

logger = logging.getLogger(__name__)

# FeatureFlags 前向声明 (模块级) — 根除 5 处局部 try import ignore
# is_enabled 是类方法，返回 True/False；缺失/异常一律降级 False (保守 fail-open)
_FeatureFlags: Optional[type]
try:
    from utils.infra.feature_flags import FeatureFlags as _FFClass

    _FeatureFlags = _FFClass
except (ImportError, AttributeError):
    _FeatureFlags = None


# ============================================================
# 常量
# ============================================================

# Feature Flag 名称 (HC-1)
FLAG_NAME = "USE_DAILY_ATTRIBUTION_PANEL"

# ConfigManager 配置名 (HC-5)
DEFAULT_CONFIG_NAME = "daily_panel"

# 默认基准标的 (沪深300ETF)
DEFAULT_PRIMARY_BENCHMARK = "510300.SH"
DEFAULT_SECONDARY_BENCHMARK = "510500.SH"

# 默认报告输出目录 (相对项目根)
DEFAULT_REPORT_DIR = "reports/attribution"

# 文件命名模板
DEFAULT_JSON_TEMPLATE = "daily_panel_{date}.json"
DEFAULT_MARKDOWN_TEMPLATE = "daily_panel_{date}.md"

# 数值精度
DEFAULT_DECIMAL_PRECISION = 6
DEFAULT_RETURN_PRECISION = 4
DEFAULT_BPS_PRECISION = 2

# 生成超时 (秒)
DEFAULT_GENERATION_TIMEOUT = 30

# 残差验证容差
DEFAULT_RESIDUAL_TOLERANCE = 1e-6

# 子模块状态码
MODULE_STATUS_OK = "ok"
MODULE_STATUS_DEGRADED = "degraded"
MODULE_STATUS_SKIPPED = "skipped"
MODULE_STATUS_ERROR = "error"
MODULE_STATUS_FEATURE_FLAG_DISABLED = "feature_flag_disabled"
MODULE_STATUS_EMPTY_INPUT = "empty_input"

# 报告整体状态码
STATUS_OK = "ok"
STATUS_FEATURE_FLAG_DISABLED = "feature_flag_disabled"
STATUS_PARTIAL = "partial"  # 部分模块降级
STATUS_ALL_DEGRADED = "all_degraded"  # 全部模块降级
STATUS_EMPTY_INPUT = "empty_input"
STATUS_ERROR = "error"

# 项目根目录
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# 时区
DEFAULT_TIMEZONE = "Asia/Shanghai"


# ============================================================
# 异常体系
# ============================================================


class DailyPanelError(Exception):
    """日级归因面板基础异常."""


class PanelGenerationError(DailyPanelError):
    """报告生成失败."""


class PersistenceError(DailyPanelError):
    """持久化失败."""


class ModuleAggregationError(DailyPanelError):
    """子模块聚合失败 (单模块异常不阻塞, 仅当全部失败时抛出)."""


# ============================================================
# 输入数据类 (从外部收集)
# ============================================================


@dataclass
class BrinsonInput:
    """Brinson 归因输入参数 (对齐 BrinsonAttributionManager.attribute)."""

    portfolio_weights: dict[str, float] = field(default_factory=dict)
    benchmark_weights: dict[str, float] | None = None
    portfolio_returns: dict[str, float] = field(default_factory=dict)
    benchmark_returns: dict[str, float] = field(default_factory=dict)
    benchmark_code: str | None = None
    validate: bool = True

    def is_empty(self) -> bool:
        """判断输入是否为空."""
        return not self.portfolio_weights or not self.portfolio_returns


@dataclass
class FactorInput:
    """Barra 因子归因输入参数 (对齐 FactorAttributionManager.attribute)."""

    portfolio_exposures: dict[str, float] = field(default_factory=dict)
    benchmark_exposures: dict[str, float] | None = None
    factor_returns: dict[str, float] = field(default_factory=dict)
    portfolio_value: float = 1_000_000.0
    specific_pnl: float = 0.0
    active_return: float | None = None
    benchmark_code: str | None = None
    factor_cov_matrix: dict[str, dict[str, float]] | None = None
    active_weights: dict[str, float] | None = None
    stock_specific_risks: dict[str, float] | None = None
    ic_metrics: dict[str, Any] | None = None

    def is_empty(self) -> bool:
        """判断输入是否为空."""
        return not self.portfolio_exposures or not self.factor_returns


@dataclass
class TCAInput:
    """TCA 执行归因输入参数.

    两种模式:
      1. summary_dict: 已聚合的 TCA 汇总数据 (来自 PostTradeAttribution.summarize())
      2. pnl_attributions: 原始 PnLAttribution 列表 (聚合层内部聚合)
    """

    summary_dict: dict[str, Any] | None = None
    pnl_attributions: list[dict[str, Any]] | None = None
    fill_records: list[dict[str, Any]] | None = None

    def is_empty(self) -> bool:
        """判断输入是否为空."""
        return not self.summary_dict and not self.pnl_attributions and not self.fill_records


@dataclass
class DailyReportInput:
    """日级归因面板统一输入容器."""

    brinson: BrinsonInput | None = None
    factor: FactorInput | None = None
    tca: TCAInput | None = None

    def is_all_empty(self) -> bool:
        """判断是否所有模块输入都为空."""
        brinson_empty = self.brinson is None or self.brinson.is_empty()
        factor_empty = self.factor is None or self.factor.is_empty()
        tca_empty = self.tca is None or self.tca.is_empty()
        return brinson_empty and factor_empty and tca_empty


# ============================================================
# 输出数据类
# ============================================================


@dataclass
class ModuleStatus:
    """子模块状态汇总."""

    module_name: str = ""
    status: str = MODULE_STATUS_OK
    reason: str = ""
    feature_flag_enabled: bool = False
    generation_time_ms: float = 0.0
    has_data: bool = False

    def to_dict(self) -> dict[str, Any]:
        """转为字典."""
        return {
            "module_name": self.module_name,
            "status": self.status,
            "reason": self.reason,
            "feature_flag_enabled": self.feature_flag_enabled,
            "generation_time_ms": round(self.generation_time_ms, 2),
            "has_data": self.has_data,
        }


@dataclass
class DailyAttributionReport:
    """日级归因面板统一输出容器.

    三套归因结果并列存放 (不互相求和), 各自从不同视角解释组合 PnL:
      - brinson_dict: Brinson 三效应 (行业/选股/交互)
      - factor_dict: Barra 因子收益 (10 风格 + 8 行业)
      - tca_dict: TCA 执行归因 (Alpha/Execution/Risk)

    Attributes:
        attribution_date: 归因日期 (YYYY-MM-DD)
        benchmark_code: 主基准标的代码
        brinson_dict: Brinson 归因结果 dict (来自 BrinsonResult.to_dict())
        factor_dict: 因子归因结果 dict (来自 FactorAttributionResult.to_dict())
        tca_dict: TCA 归因汇总 dict (来自 PostTradeAttribution.summarize() 或聚合层自聚合)
        module_statuses: 各子模块状态列表
        brinson_markdown: Brinson 归因 Markdown 片段
        factor_markdown: 因子归因 Markdown 片段
        tca_markdown: TCA 归因 Markdown 片段 (聚合层生成)
        total_pnl: 组合总 PnL (来自 factor_dict 或 brinson excess_return × portfolio_value)
        active_return: 主动收益 (来自 factor_dict)
        active_risk: 主动风险 (来自 factor_dict)
        information_ratio: 信息比率 (来自 factor_dict)
        generation_time_ms: 总生成时间 (毫秒)
        generated_at: 报告生成时间戳 (ISO 格式)
        config_source: 配置文件来源路径
        feature_flag_name: Feature Flag 名称
        status: 报告整体状态
        reason: 状态说明
    """

    attribution_date: str = ""
    benchmark_code: str = ""
    brinson_dict: dict[str, Any] = field(default_factory=dict)
    factor_dict: dict[str, Any] = field(default_factory=dict)
    tca_dict: dict[str, Any] = field(default_factory=dict)
    module_statuses: list[ModuleStatus] = field(default_factory=list)
    brinson_markdown: str = ""
    factor_markdown: str = ""
    tca_markdown: str = ""
    total_pnl: float = 0.0
    active_return: float = 0.0
    active_risk: float = 0.0
    information_ratio: float = 0.0
    generation_time_ms: float = 0.0
    generated_at: str = ""
    config_source: str = ""
    feature_flag_name: str = FLAG_NAME
    status: str = STATUS_OK
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        """转为字典 (用于 JSON 序列化)."""
        return {
            "attribution_date": self.attribution_date,
            "benchmark_code": self.benchmark_code,
            "brinson": self.brinson_dict,
            "factor": self.factor_dict,
            "tca": self.tca_dict,
            "module_statuses": [s.to_dict() for s in self.module_statuses],
            "summary": {
                "total_pnl": round(self.total_pnl, 2),
                "active_return": round(self.active_return, DEFAULT_DECIMAL_PRECISION),
                "active_risk": round(self.active_risk, DEFAULT_DECIMAL_PRECISION),
                "information_ratio": round(self.information_ratio, DEFAULT_DECIMAL_PRECISION),
            },
            "metadata": {
                "generation_time_ms": round(self.generation_time_ms, 2),
                "generated_at": self.generated_at,
                "config_source": self.config_source,
                "feature_flag_name": self.feature_flag_name,
                "status": self.status,
                "reason": self.reason,
            },
        }

    def to_markdown(self) -> str:
        """转为 Markdown 报告 (人类可读)."""
        lines: list[str] = []
        # 头部
        lines.append("# 日级归因面板 (Daily Attribution Panel)")
        lines.append("")
        lines.append(f"- **归因日期**: {self.attribution_date}")
        lines.append(f"- **基准标的**: {self.benchmark_code}")
        lines.append(f"- **报告状态**: {self.status}")
        if self.reason:
            lines.append(f"- **状态说明**: {self.reason}")
        lines.append(f"- **生成时间**: {self.generated_at} ({self.generation_time_ms:.0f} ms)")
        lines.append("")

        # 汇总摘要
        lines.append("## 摘要")
        lines.append("")
        lines.append("| 指标 | 数值 |")
        lines.append("|------|------|")
        lines.append(f"| 组合总 PnL | ¥{self.total_pnl:.2f} |")
        lines.append(f"| 主动收益 | {self.active_return:.4%} |")
        lines.append(f"| 主动风险 (年化) | {self.active_risk:.4%} |")
        ir_display = f"{self.information_ratio:.4f}" if math.isfinite(self.information_ratio) else "N/A"
        lines.append(f"| 信息比率 (IR) | {ir_display} |")
        lines.append("")

        # 子模块状态
        lines.append("## 子模块状态")
        lines.append("")
        lines.append("| 模块 | 状态 | Feature Flag | 耗时(ms) | 说明 |")
        lines.append("|------|------|--------------|----------|------|")
        for s in self.module_statuses:
            flag_str = "ON" if s.feature_flag_enabled else "OFF"
            lines.append(f"| {s.module_name} | {s.status} | {flag_str} | {s.generation_time_ms:.0f} | {s.reason} |")
        lines.append("")

        # Brinson 章节
        if self.brinson_markdown:
            lines.append("## Brinson 归因 (行业配置/选股/交互)")
            lines.append("")
            lines.append(self.brinson_markdown)
            lines.append("")

        # Factor 章节
        if self.factor_markdown:
            lines.append("## Barra 因子归因 (10 风格 + 8 行业)")
            lines.append("")
            lines.append(self.factor_markdown)
            lines.append("")

        # TCA 章节
        if self.tca_markdown:
            lines.append("## TCA 执行归因 (Alpha/Execution/Risk)")
            lines.append("")
            lines.append(self.tca_markdown)
            lines.append("")

        # 脚注
        lines.append("---")
        lines.append("")
        lines.append(f"*配置来源: {self.config_source}*")
        lines.append(f"*Feature Flag: {self.feature_flag_name}*")
        return "\n".join(lines)


# ============================================================
# 核心面板类
# ============================================================


class DailyAttributionPanel:
    """日级归因面板主类 (Facade 模式 + HC-1 + HC-5).

    集成三个独立归因模块, 输出统一日级报告:
      1. BrinsonAttributionManager (T5.1)
      2. FactorAttributionManager (T5.2)
      3. PostTradeAttribution (T3.5)

    每个子模块独立初始化, 单一失败不阻塞主流程 (HC-2 主路径不阻塞).

    Usage:
        >>> panel = DailyAttributionPanel()
        >>> report = panel.generate(
        ...     attribution_date="2026-07-27",
        ...     brinson_input=BrinsonInput(...),
        ...     factor_input=FactorInput(...),
        ...     tca_input=TCAInput(...),
        ... )
        >>> panel.save(report)
    """

    def __init__(
        self,
        config_name: str = DEFAULT_CONFIG_NAME,
        feature_flag_name: str = FLAG_NAME,
        config: dict[str, Any] | None = None,
        project_root: Path | None = None,
    ) -> None:
        """初始化日级归因面板.

        Args:
            config_name: ConfigManager 配置名
            feature_flag_name: Feature Flag 名称
            config: 显式配置 (优先于 ConfigManager)
            project_root: 项目根目录 (默认自动推断)
        """
        self._feature_flag_name = feature_flag_name
        self._config_name = config_name
        self._project_root = project_root or _PROJECT_ROOT

        # 加载配置 (HC-5: ConfigManager 4 级优先级)
        self._config = config if config is not None else self._load_config(config_name)
        self._settings = self._config.get("settings", {}) or {}
        self._aggregation = self._config.get("aggregation", {}) or {}
        self._report_cfg = self._config.get("report", {}) or {}
        self._persistence_cfg = self._config.get("persistence", {}) or {}

        # 提取常用配置
        self._primary_benchmark = self._settings.get("primary_benchmark", DEFAULT_PRIMARY_BENCHMARK)
        self._report_dir_name = self._settings.get("report_dir", DEFAULT_REPORT_DIR)
        self._json_template = self._settings.get("json_filename_template", DEFAULT_JSON_TEMPLATE)
        self._md_template = self._settings.get("markdown_filename_template", DEFAULT_MARKDOWN_TEMPLATE)
        self._generation_timeout = self._settings.get("generation_timeout_seconds", DEFAULT_GENERATION_TIMEOUT)
        self._decimal_precision = self._settings.get("decimal_precision", DEFAULT_DECIMAL_PRECISION)
        self._return_precision = self._settings.get("return_precision", DEFAULT_RETURN_PRECISION)
        self._bps_precision = self._settings.get("bps_precision", DEFAULT_BPS_PRECISION)
        self._residual_tolerance = self._aggregation.get("residual_tolerance", DEFAULT_RESIDUAL_TOLERANCE)
        self._degraded_handling = self._aggregation.get("degraded_handling", "placeholder")

        # 子模块启用开关 (双层控制: 配置 + Feature Flag)
        self._enable_brinson_cfg = self._settings.get("enable_brinson", True)
        self._enable_factor_cfg = self._settings.get("enable_factor", True)
        self._enable_tca_cfg = self._settings.get("enable_tca", True)

        # 报告目录 (绝对路径)
        self._report_dir = self._project_root / self._report_dir_name

        # 配置来源路径 (用于审计)
        self._config_source = self._resolve_config_source(config_name)

        # 延迟初始化子模块 Manager (避免 import 时失败)
        self._brinson_mgr = None
        self._factor_mgr = None
        self._tca_mgr = None

        logger.debug(
            f"[DailyAttributionPanel] 初始化完成, "
            f"report_dir={self._report_dir}, primary_benchmark={self._primary_benchmark}"
        )

    # ------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------

    def generate(
        self,
        attribution_date: str = "",
        brinson_input: BrinsonInput | None = None,
        factor_input: FactorInput | None = None,
        tca_input: TCAInput | None = None,
        inputs: DailyReportInput | None = None,
    ) -> DailyAttributionReport:
        """生成日级归因报告.

        Args:
            attribution_date: 归因日期 (YYYY-MM-DD), 默认今天
            brinson_input: Brinson 归因输入 (可选)
            factor_input: Barra 因子归因输入 (可选)
            tca_input: TCA 执行归因输入 (可选)
            inputs: 统一输入容器 (与上面三个互斥, 优先使用)

        Returns:
            DailyAttributionReport
        """
        start_time = time.perf_counter()

        # HC-1: Feature Flag 透传
        if not self._is_feature_flag_enabled():
            return self._build_disabled_report(attribution_date, start_time)

        # 处理统一输入容器
        if inputs is not None:
            brinson_input = inputs.brinson
            factor_input = inputs.factor
            tca_input = inputs.tca

        # 默认日期
        if not attribution_date:
            attribution_date = datetime.now().strftime("%Y-%m-%d")

        # 全部输入为空
        all_empty = (
            (brinson_input is None or brinson_input.is_empty())
            and (factor_input is None or factor_input.is_empty())
            and (tca_input is None or tca_input.is_empty())
        )
        if all_empty:
            return self._build_empty_report(attribution_date, start_time, use_perf_counter=True)

        # 初始化报告容器
        report = DailyAttributionReport(
            attribution_date=attribution_date,
            benchmark_code=self._primary_benchmark,
            config_source=self._config_source,
            feature_flag_name=self._feature_flag_name,
            generated_at=datetime.now().isoformat(timespec="seconds"),
        )

        module_statuses: list[ModuleStatus] = []

        # 1. Brinson 归因
        brinson_result_dict, brinson_md, brinson_status = self._run_brinson(attribution_date, brinson_input)
        report.brinson_dict = brinson_result_dict
        report.brinson_markdown = brinson_md
        module_statuses.append(brinson_status)

        # 2. Factor 归因
        factor_result_dict, factor_md, factor_status = self._run_factor(attribution_date, factor_input)
        report.factor_dict = factor_result_dict
        report.factor_markdown = factor_md
        module_statuses.append(factor_status)

        # 3. TCA 执行归因
        tca_result_dict, tca_md, tca_status = self._run_tca(attribution_date, tca_input)
        report.tca_dict = tca_result_dict
        report.tca_markdown = tca_md
        module_statuses.append(tca_status)

        report.module_statuses = module_statuses

        # 提取汇总指标 (优先从 factor_dict, 因为它有完整的 PnL/Risk 分解)
        self._populate_summary_from_factor(report)

        # 总体状态评估
        report.status, report.reason = self._evaluate_overall_status(module_statuses)

        # 生成耗时
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        report.generation_time_ms = elapsed_ms

        # 超时警告 (不抛异常, 仅日志)
        if elapsed_ms > self._generation_timeout * 1000:
            logger.warning(
                f"[DailyAttributionPanel] 生成耗时 {elapsed_ms:.0f}ms 超过阈值 {self._generation_timeout * 1000:.0f}ms"
            )

        logger.info(
            f"[DailyAttributionPanel] 报告生成完成, date={attribution_date}, "
            f"status={report.status}, elapsed={elapsed_ms:.0f}ms"
        )
        return report

    def save(
        self,
        report: DailyAttributionReport,
        report_dir: Path | None = None,
        save_json: bool | None = None,
        save_markdown: bool | None = None,
    ) -> dict[str, Path]:
        """持久化日级归因报告 (JSON + Markdown 双输出).

        Args:
            report: 日级归因报告
            report_dir: 输出目录 (默认 self._report_dir)
            save_json: 是否保存 JSON (None 时用配置)
            save_markdown: 是否保存 Markdown (None 时用配置)

        Returns:
            保存的文件路径字典 {"json": Path, "markdown": Path}

        Raises:
            PersistenceError: 持久化失败
        """
        target_dir = report_dir or self._report_dir
        do_json = save_json if save_json is not None else self._persistence_cfg.get("save_json", True)
        do_md = save_markdown if save_markdown is not None else self._persistence_cfg.get("save_markdown", True)

        saved_paths: dict[str, Path] = {}

        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise PersistenceError(f"创建报告目录失败: {target_dir}, 原因: {exc}") from exc

        date_str = report.attribution_date or datetime.now().strftime("%Y-%m-%d")

        if do_json:
            json_filename = self._json_template.format(date=date_str)
            json_path = target_dir / json_filename
            try:
                with open(json_path, "w", encoding="utf-8") as f:
                    json.dump(report.to_dict(), f, ensure_ascii=False, indent=2, default=str)
                saved_paths["json"] = json_path
                logger.debug(f"[DailyAttributionPanel] JSON 报告已保存: {json_path}")
            except OSError as exc:
                raise PersistenceError(f"保存 JSON 报告失败: {json_path}, 原因: {exc}") from exc

        if do_md:
            md_filename = self._md_template.format(date=date_str)
            md_path = target_dir / md_filename
            try:
                with open(md_path, "w", encoding="utf-8") as f:
                    f.write(report.to_markdown())
                saved_paths["markdown"] = md_path
                logger.debug(f"[DailyAttributionPanel] Markdown 报告已保存: {md_path}")
            except OSError as exc:
                raise PersistenceError(f"保存 Markdown 报告失败: {md_path}, 原因: {exc}") from exc

        return saved_paths

    def generate_and_save(
        self,
        attribution_date: str = "",
        brinson_input: BrinsonInput | None = None,
        factor_input: FactorInput | None = None,
        tca_input: TCAInput | None = None,
        inputs: DailyReportInput | None = None,
        report_dir: Path | None = None,
    ) -> tuple[DailyAttributionReport, dict[str, Path]]:
        """生成并保存报告 (便捷方法)."""
        report = self.generate(
            attribution_date=attribution_date,
            brinson_input=brinson_input,
            factor_input=factor_input,
            tca_input=tca_input,
            inputs=inputs,
        )
        paths = self.save(report, report_dir=report_dir)
        return report, paths

    def is_enabled(self) -> bool:
        """检查 Feature Flag 是否启用 (HC-1)."""
        return self._is_feature_flag_enabled()

    def get_config_source(self) -> str:
        """获取配置来源路径."""
        return self._config_source

    # ------------------------------------------------------------
    # 内部实现 — 子模块执行
    # ------------------------------------------------------------

    def _run_brinson(
        self,
        attribution_date: str,
        brinson_input: BrinsonInput | None,
    ) -> tuple[dict[str, Any], str, ModuleStatus]:
        """执行 Brinson 归因."""
        start = time.time()
        status = ModuleStatus(module_name="brinson")

        # 配置层开关
        if not self._enable_brinson_cfg:
            status.status = MODULE_STATUS_SKIPPED
            status.reason = "配置 enable_brinson=False"
            return {}, "", status

        # 输入为空
        if brinson_input is None or brinson_input.is_empty():
            status.status = MODULE_STATUS_EMPTY_INPUT
            status.reason = "Brinson 输入为空"
            return {}, "", status

        try:
            # 延迟初始化 Manager
            if self._brinson_mgr is None:
                self._brinson_mgr = self._init_brinson_manager()

            if self._brinson_mgr is None:
                # Brinson Manager 初始化失败, 降级
                status.status = MODULE_STATUS_DEGRADED
                status.reason = "Brinson Manager 初始化失败"
                return {}, "", status

            # 调用 Brinson 归因
            result = self._brinson_mgr.attribute(
                portfolio_weights=brinson_input.portfolio_weights,
                benchmark_weights=brinson_input.benchmark_weights,
                portfolio_returns=brinson_input.portfolio_returns,
                benchmark_returns=brinson_input.benchmark_returns,
                attribution_date=attribution_date,
                benchmark_code=brinson_input.benchmark_code,
                validate=brinson_input.validate,
            )

            status.generation_time_ms = (time.time() - start) * 1000
            status.feature_flag_enabled = self._is_brinson_flag_enabled()
            status.has_data = True

            # 判断是否降级结果
            result_status = getattr(result, "status", "ok")
            if result_status == "feature_flag_disabled":
                status.status = MODULE_STATUS_DEGRADED
                status.reason = "Brinson Feature Flag 关闭"
                return result.to_dict(), "", status
            elif result_status != "ok":
                status.status = MODULE_STATUS_DEGRADED
                status.reason = f"Brinson 状态: {result_status}"
            else:
                status.status = MODULE_STATUS_OK

            return result.to_dict(), result.to_markdown(), status

        except (AttributeError, TypeError, ValueError, OSError) as exc:
            logger.exception(f"[DailyAttributionPanel] Brinson 归因失败: {exc}")
            status.status = MODULE_STATUS_ERROR
            status.reason = f"Brinson 异常: {type(exc).__name__}: {exc}"
            status.generation_time_ms = (time.time() - start) * 1000
            return {}, "", status

    def _run_factor(
        self,
        attribution_date: str,
        factor_input: FactorInput | None,
    ) -> tuple[dict[str, Any], str, ModuleStatus]:
        """执行 Barra 因子归因."""
        start = time.time()
        status = ModuleStatus(module_name="factor")

        if not self._enable_factor_cfg:
            status.status = MODULE_STATUS_SKIPPED
            status.reason = "配置 enable_factor=False"
            return {}, "", status

        if factor_input is None or factor_input.is_empty():
            status.status = MODULE_STATUS_EMPTY_INPUT
            status.reason = "Factor 输入为空"
            return {}, "", status

        try:
            if self._factor_mgr is None:
                self._factor_mgr = self._init_factor_manager()

            if self._factor_mgr is None:
                status.status = MODULE_STATUS_DEGRADED
                status.reason = "Factor Manager 初始化失败"
                return {}, "", status

            result = self._factor_mgr.attribute(
                portfolio_exposures=factor_input.portfolio_exposures,
                benchmark_exposures=factor_input.benchmark_exposures,
                factor_returns=factor_input.factor_returns,
                portfolio_value=factor_input.portfolio_value,
                specific_pnl=factor_input.specific_pnl,
                active_return=factor_input.active_return,
                attribution_date=attribution_date,
                benchmark_code=factor_input.benchmark_code,
                factor_cov_matrix=factor_input.factor_cov_matrix,
                active_weights=factor_input.active_weights,
                stock_specific_risks=factor_input.stock_specific_risks,
                ic_metrics=factor_input.ic_metrics,
            )

            status.generation_time_ms = (time.time() - start) * 1000
            status.feature_flag_enabled = self._is_factor_flag_enabled()
            status.has_data = True

            result_status = getattr(result, "status", "ok")
            if result_status == "feature_flag_disabled":
                status.status = MODULE_STATUS_DEGRADED
                status.reason = "Factor Feature Flag 关闭"
                return result.to_dict(), "", status
            elif result_status != "ok":
                status.status = MODULE_STATUS_DEGRADED
                status.reason = f"Factor 状态: {result_status}"
            else:
                status.status = MODULE_STATUS_OK

            return result.to_dict(), result.to_markdown(), status

        except (AttributeError, TypeError, ValueError, OSError) as exc:
            logger.exception(f"[DailyAttributionPanel] Factor 归因失败: {exc}")
            status.status = MODULE_STATUS_ERROR
            status.reason = f"Factor 异常: {type(exc).__name__}: {exc}"
            status.generation_time_ms = (time.time() - start) * 1000
            return {}, "", status

    def _run_tca(
        self,
        attribution_date: str,
        tca_input: TCAInput | None,
    ) -> tuple[dict[str, Any], str, ModuleStatus]:
        """执行 TCA 执行归因 (聚合层负责聚合)."""
        start = time.time()
        status = ModuleStatus(module_name="tca")

        if not self._enable_tca_cfg:
            status.status = MODULE_STATUS_SKIPPED
            status.reason = "配置 enable_tca=False"
            return {}, "", status

        if tca_input is None or tca_input.is_empty():
            status.status = MODULE_STATUS_EMPTY_INPUT
            status.reason = "TCA 输入为空"
            return {}, "", status

        try:
            # TCA 直接使用输入数据聚合 (不依赖 PostTradeAttribution 实例)
            tca_summary = self._aggregate_tca(tca_input)
            tca_md = self._build_tca_markdown(tca_summary, attribution_date)

            status.generation_time_ms = (time.time() - start) * 1000
            status.feature_flag_enabled = self._is_tca_flag_enabled()
            status.has_data = bool(tca_summary)
            status.status = MODULE_STATUS_OK

            return tca_summary, tca_md, status

        except (ValueError, TypeError, KeyError, AttributeError, OSError) as exc:
            logger.exception(f"[DailyAttributionPanel] TCA 归因失败: {exc}")
            status.status = MODULE_STATUS_ERROR
            status.reason = f"TCA 异常: {type(exc).__name__}: {exc}"
            status.generation_time_ms = (time.time() - start) * 1000
            return {}, "", status

    # ------------------------------------------------------------
    # 内部实现 — TCA 聚合
    # ------------------------------------------------------------

    def _aggregate_tca(self, tca_input: TCAInput) -> dict[str, Any]:
        """聚合 TCA 输入到组合级汇总.

        策略:
          1. 优先使用 tca_input.summary_dict (已聚合数据)
          2. 否则聚合 tca_input.pnl_attributions (List[dict])
          3. 否则从 tca_input.fill_records 推导 (无 PnL 拆分)
        """
        # 优先使用已聚合的 summarize
        if tca_input.summary_dict:
            return self._normalize_tca_summary(tca_input.summary_dict)

        # 聚合 PnLAttribution 列表
        if tca_input.pnl_attributions:
            return self._aggregate_pnl_list(tca_input.pnl_attributions)

        # 仅有 fill_records, 缺少 PnL 拆分
        if tca_input.fill_records:
            return self._aggregate_fills_only(tca_input.fill_records)

        return {}

    def _normalize_tca_summary(self, summary: dict[str, Any]) -> dict[str, Any]:
        """标准化 PostTradeAttribution.summarize() 输出."""
        normalized = {
            "source": "post_trade_summarize",
            "total_pnl": float(summary.get("total_pnl", 0.0) or 0.0),
            "alpha_pnl": float(summary.get("alpha_pnl", 0.0) or 0.0),
            "execution_pnl": float(summary.get("execution_pnl", 0.0) or 0.0),
            "risk_pnl": float(summary.get("risk_pnl", 0.0) or 0.0),
            "n_fills": int(summary.get("n_fills", 0) or 0),
            "n_symbols": int(summary.get("n_symbols", 0) or 0),
            "alpha_bps": float(summary.get("alpha_bps", 0.0) or 0.0),
            "execution_bps": float(summary.get("execution_bps", 0.0) or 0.0),
            "risk_bps": float(summary.get("risk_bps", 0.0) or 0.0),
        }
        # 残差验证 (total = alpha + execution + risk)
        normalized_dict = cast(dict[str, Any], normalized)
        residual = (
            normalized_dict["total_pnl"]
            - normalized_dict["alpha_pnl"]
            - normalized_dict["execution_pnl"]
            - normalized_dict["risk_pnl"]
        )
        if abs(residual) > self._residual_tolerance:
            logger.warning(f"[DailyAttributionPanel] TCA 残差 {residual:.6f} 超过容差 {self._residual_tolerance}")
        normalized["residual"] = round(residual, self._decimal_precision)
        return normalized

    def _aggregate_pnl_list(self, pnl_list: list[dict[str, Any]]) -> dict[str, Any]:
        """聚合 PnLAttribution dict 列表到组合级."""
        total_pnl = 0.0
        alpha_pnl = 0.0
        execution_pnl = 0.0
        risk_pnl = 0.0
        total_notional = 0.0
        symbols = set()

        for item in pnl_list:
            try:
                alpha_pnl += float(item.get("alpha_pnl", 0.0) or 0.0)
                execution_pnl += float(item.get("execution_pnl", 0.0) or 0.0)
                risk_pnl += float(item.get("risk_pnl", 0.0) or 0.0)
                total_pnl += float(item.get("total_pnl", 0.0) or 0.0)
                total_notional += float(item.get("notional", 0.0) or 0.0)
                if item.get("symbol"):
                    symbols.add(item["symbol"])
            except (TypeError, ValueError) as exc:
                logger.warning(f"[DailyAttributionPanel] PnL 项解析失败: {exc}, item={item}")
                continue

        # 计算 bps (避免除零)
        alpha_bps = (alpha_pnl / total_notional * 10000) if total_notional > 0 else 0.0
        execution_bps = (execution_pnl / total_notional * 10000) if total_notional > 0 else 0.0
        risk_bps = (risk_pnl / total_notional * 10000) if total_notional > 0 else 0.0

        residual = total_pnl - alpha_pnl - execution_pnl - risk_pnl
        return {
            "source": "aggregated_from_pnl_list",
            "total_pnl": round(total_pnl, 2),
            "alpha_pnl": round(alpha_pnl, 2),
            "execution_pnl": round(execution_pnl, 2),
            "risk_pnl": round(risk_pnl, 2),
            "alpha_bps": round(alpha_bps, self._bps_precision),
            "execution_bps": round(execution_bps, self._bps_precision),
            "risk_bps": round(risk_bps, self._bps_precision),
            "n_fills": len(pnl_list),
            "n_symbols": len(symbols),
            "residual": round(residual, self._decimal_precision),
        }

    def _aggregate_fills_only(self, fills: list[dict[str, Any]]) -> dict[str, Any]:
        """仅有 fill_records 时返回基础汇总 (无 PnL 拆分)."""
        symbols = set()
        total_notional = 0.0
        for fill in fills:
            try:
                shares = float(fill.get("shares", 0) or 0)
                price = float(fill.get("price", 0) or 0)
                total_notional += shares * price
                if fill.get("symbol"):
                    symbols.add(fill["symbol"])
            except (TypeError, ValueError):
                continue
        return {
            "source": "fills_only_no_pnl",
            "total_pnl": 0.0,
            "alpha_pnl": 0.0,
            "execution_pnl": 0.0,
            "risk_pnl": 0.0,
            "alpha_bps": 0.0,
            "execution_bps": 0.0,
            "risk_bps": 0.0,
            "n_fills": len(fills),
            "n_symbols": len(symbols),
            "total_notional": round(total_notional, 2),
            "residual": 0.0,
        }

    def _build_tca_markdown(self, tca_summary: dict[str, Any], attribution_date: str) -> str:
        """生成 TCA 章节 Markdown."""
        if not tca_summary:
            return ""

        lines: list[str] = []
        lines.append(f"- **归因日期**: {attribution_date}")
        lines.append(f"- **数据来源**: {tca_summary.get('source', 'unknown')}")
        lines.append(f"- **成交笔数**: {tca_summary.get('n_fills', 0)}")
        lines.append(f"- **标的数量**: {tca_summary.get('n_symbols', 0)}")
        lines.append("")
        lines.append("| PnL 拆分 | 金额 (¥) | bps | 占比 |")
        lines.append("|----------|-----------|-----|------|")

        total = tca_summary.get("total_pnl", 0.0)
        alpha_pnl = tca_summary.get("alpha_pnl", 0.0)
        execution_pnl = tca_summary.get("execution_pnl", 0.0)
        risk_pnl = tca_summary.get("risk_pnl", 0.0)
        alpha_bps = tca_summary.get("alpha_bps", 0.0)
        execution_bps = tca_summary.get("execution_bps", 0.0)
        risk_bps = tca_summary.get("risk_bps", 0.0)

        def pct(part: float, total: float) -> str:
            if abs(total) < 1e-12:
                return "N/A"
            return f"{part / total:.2%}"

        lines.append(f"| Alpha (决策) | {alpha_pnl:.2f} | {alpha_bps:.2f} | {pct(alpha_pnl, total)} |")
        lines.append(f"| Execution (执行) | {execution_pnl:.2f} | {execution_bps:.2f} | {pct(execution_pnl, total)} |")
        lines.append(f"| Risk (风险) | {risk_pnl:.2f} | {risk_bps:.2f} | {pct(risk_pnl, total)} |")
        lines.append(f"| **合计** | **{total:.2f}** | **{alpha_bps + execution_bps + risk_bps:.2f}** | **100.00%** |")

        residual = tca_summary.get("residual", 0.0)
        if abs(residual) > self._residual_tolerance:
            lines.append("")
            lines.append(f"> ⚠️ 残差 {residual:.6f} 超过容差, 请检查 PnL 拆分完整性")

        return "\n".join(lines)

    # ------------------------------------------------------------
    # 内部实现 — 子模块 Manager 初始化
    # ------------------------------------------------------------

    def _init_brinson_manager(self):
        """延迟初始化 BrinsonAttributionManager."""
        try:
            from utils.attribution.brinson_attribution import BrinsonAttributionManager

            return BrinsonAttributionManager()
        except (ImportError, AttributeError) as exc:
            logger.warning(f"[DailyAttributionPanel] Brinson Manager 初始化失败: {exc}")
            return None

    def _init_factor_manager(self):
        """延迟初始化 FactorAttributionManager."""
        try:
            from utils.attribution.factor_attribution import FactorAttributionManager

            return FactorAttributionManager()
        except (ImportError, AttributeError) as exc:
            logger.warning(f"[DailyAttributionPanel] Factor Manager 初始化失败: {exc}")
            return None

    # ------------------------------------------------------------
    # 内部实现 — Feature Flag 查询
    # ------------------------------------------------------------

    def _is_feature_flag_enabled(self) -> bool:
        """查询本面板的 Feature Flag."""
        if _FeatureFlags is None:
            return False
        try:
            return bool(_FeatureFlags.is_enabled(self._feature_flag_name))
        except (ValueError, TypeError, KeyError, AttributeError, OSError):
            return False

    def _is_brinson_flag_enabled(self) -> bool:
        """查询 Brinson 子模块 Feature Flag."""
        if _FeatureFlags is None:
            return False
        try:
            from utils.attribution.brinson_attribution import FLAG_NAME as BRINSON_FLAG

            return bool(_FeatureFlags.is_enabled(BRINSON_FLAG))
        except (ImportError, AttributeError):
            return False

    def _is_factor_flag_enabled(self) -> bool:
        """查询 Factor 子模块 Feature Flag."""
        if _FeatureFlags is None:
            return False
        try:
            from utils.attribution.factor_attribution import FLAG_NAME as FACTOR_FLAG

            return bool(_FeatureFlags.is_enabled(FACTOR_FLAG))
        except (ImportError, AttributeError):
            return False

    def _is_tca_flag_enabled(self) -> bool:
        """查询 TCA 子模块 Feature Flag."""
        if _FeatureFlags is None:
            return False
        try:
            return bool(_FeatureFlags.is_enabled("USE_TCA_POST_TRADE_ATTRIBUTION"))
        except (ValueError, TypeError, KeyError, AttributeError, OSError):
            return False

    # ------------------------------------------------------------
    # 内部实现 — 辅助方法
    # ------------------------------------------------------------

    def _populate_summary_from_factor(self, report: DailyAttributionReport) -> None:
        """从 factor_dict 提取汇总指标到 report 顶层字段."""
        if not report.factor_dict:
            return
        try:
            report.total_pnl = float(report.factor_dict.get("total_pnl", 0.0) or 0.0)
            report.active_return = float(report.factor_dict.get("active_return", 0.0) or 0.0)
            report.active_risk = float(report.factor_dict.get("active_risk", 0.0) or 0.0)
            ir = report.factor_dict.get("information_ratio", 0.0)
            report.information_ratio = float(ir) if ir is not None else 0.0
        except (TypeError, ValueError) as exc:
            logger.warning(f"[DailyAttributionPanel] 提取汇总指标失败: {exc}")

    def _evaluate_overall_status(self, module_statuses: list[ModuleStatus]) -> tuple[str, str]:
        """评估报告整体状态."""
        if not module_statuses:
            return STATUS_EMPTY_INPUT, "无子模块状态"

        ok_count = sum(1 for s in module_statuses if s.status == MODULE_STATUS_OK)
        degraded_count = sum(
            1
            for s in module_statuses
            if s.status in (MODULE_STATUS_DEGRADED, MODULE_STATUS_EMPTY_INPUT, MODULE_STATUS_SKIPPED)
        )
        error_count = sum(1 for s in module_statuses if s.status == MODULE_STATUS_ERROR)
        total = len(module_statuses)

        if ok_count == total:
            return STATUS_OK, "全部模块正常"
        if error_count == total:
            return STATUS_ERROR, "全部模块异常"
        if degraded_count == total:
            return STATUS_ALL_DEGRADED, "全部模块降级或无数据"
        if ok_count == 0:
            return STATUS_PARTIAL, f"无正常模块, {degraded_count} 降级, {error_count} 异常"
        return STATUS_PARTIAL, f"{ok_count} 正常, {degraded_count} 降级, {error_count} 异常"

    def _build_disabled_report(self, attribution_date: str, start_time: float = 0.0) -> DailyAttributionReport:
        """构建 Feature Flag 关闭时的降级报告."""
        elapsed_ms = (time.perf_counter() - start_time) * 1000 if start_time else 0.0
        return DailyAttributionReport(
            attribution_date=attribution_date or datetime.now().strftime("%Y-%m-%d"),
            benchmark_code=self._primary_benchmark,
            status=STATUS_FEATURE_FLAG_DISABLED,
            reason=f"Feature Flag {self._feature_flag_name}=False",
            generated_at=datetime.now().isoformat(timespec="seconds"),
            config_source=self._config_source,
            feature_flag_name=self._feature_flag_name,
            generation_time_ms=elapsed_ms,
        )

    def _build_empty_report(
        self, attribution_date: str, start_time: float = 0.0, use_perf_counter: bool = False
    ) -> DailyAttributionReport:
        """构建全部输入为空时的报告."""
        if start_time:
            if use_perf_counter:
                elapsed_ms = (time.perf_counter() - start_time) * 1000
            else:
                elapsed_ms = (time.time() - start_time) * 1000
        else:
            elapsed_ms = 0.0
        return DailyAttributionReport(
            attribution_date=attribution_date,
            benchmark_code=self._primary_benchmark,
            status=STATUS_EMPTY_INPUT,
            reason="全部模块输入为空",
            generated_at=datetime.now().isoformat(timespec="seconds"),
            config_source=self._config_source,
            feature_flag_name=self._feature_flag_name,
            generation_time_ms=elapsed_ms,
        )

    def _load_config(self, config_name: str) -> dict[str, Any]:
        """加载配置 (HC-5: ConfigManager 4 级优先级)."""
        try:
            cfg = get_config(config_name)
            if cfg:
                return cfg
        except (ValueError, TypeError, KeyError, AttributeError, OSError) as exc:
            logger.warning(f"[DailyAttributionPanel] 加载配置失败 (name={config_name}): {exc}")
        return {}

    def _resolve_config_source(self, config_name: str) -> str:
        """解析配置文件来源路径 (用于审计)."""
        try:
            from utils.config_manager import get_config_source

            source = get_config_source(config_name)
            return str(source) if source else "config_manager_miss"
        except (ImportError, AttributeError):
            return "config_manager_unavailable"


# ============================================================
# 模块级便捷函数
# ============================================================


def is_daily_panel_enabled() -> bool:
    """查询日级归因面板 Feature Flag 是否启用 (HC-1)."""
    if _FeatureFlags is None:
        return False
    try:
        return bool(_FeatureFlags.is_enabled(FLAG_NAME))
    except (ValueError, TypeError, KeyError, AttributeError, OSError):
        return False


def generate_daily_report(
    attribution_date: str = "",
    brinson_input: BrinsonInput | None = None,
    factor_input: FactorInput | None = None,
    tca_input: TCAInput | None = None,
    inputs: DailyReportInput | None = None,
    save: bool = True,
    report_dir: Path | None = None,
) -> tuple[DailyAttributionReport, dict[str, Path]]:
    """便捷函数: 生成 (并可选保存) 日级归因报告."""
    panel = DailyAttributionPanel()
    report = panel.generate(
        attribution_date=attribution_date,
        brinson_input=brinson_input,
        factor_input=factor_input,
        tca_input=tca_input,
        inputs=inputs,
    )
    paths: dict[str, Path] = {}
    if save:
        paths = panel.save(report, report_dir=report_dir)
    return report, paths


def create_default_panel() -> DailyAttributionPanel:
    """创建默认配置的日级归因面板."""
    return DailyAttributionPanel()