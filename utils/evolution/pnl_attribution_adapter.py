"""P&L 归因适配器 — 将 Brinson/因子归因结果接入 FeedbackLoop.

模块整合 8.4 — ARCHITECTURE_自我进化框架 §6.4 (v2.0 合并版)
任务编号: T2.2 (Phase 2 反馈闭环)

职责:
    桥接归因层 (utils/attribution/) 与进化反馈层 (FeedbackLoop).
    将因子归因结果转换为 FeedbackLoop 所需的 factor_contributions 格式.

输入:
    1. FactorAttributionResult (来自 utils/attribution.factor_attribution)
    2. AttributionResult (来自 utils.pnl_attribution_engine)
    3. reports/attribution/daily_*.json (持久化归因文件)

输出:
    factor_contributions dict: {factor_name: contribution_to_pnl}
    可直接传入 FeedbackLoop.update_weights(factor_contributions=...)

核心算法:
    1. 从归因结果提取 style_factor + sector_factor 的 contribution_to_pnl
    2. 按因子类别映射: 风格因子 → "style_{name}", 行业因子 → "sector_{name}"
    3. 缺失数据降级: 返回全零贡献 (中性, 不调整权重)

设计原则:
    - 纯函数核心: 适配核心逻辑为纯函数, 易于测试
    - 零耦合: 不依赖归因引擎的运行时状态, 只处理结果对象
    - 双向兼容: 同时支持新旧两个归因引擎的输出格式

硬约束:
    - HC-2: 归因缺失时返回中性 (不调整, 不抛异常)
    - 保持不可变性: 不修改输入对象

用法:
    from utils.evolution.pnl_attribution_adapter import (
        PnLAttributionAdapter,
        from_factor_attribution_result,
        from_attribution_result,
        from_report_file,
        convert_to_feedback_loop_format,
    )

    # 方式 1: 从 FactorAttributionResult 转换
    result = from_factor_attribution_result(factor_attribution_result)
    contributions = result.to_feedback_loop_format()

    # 方式 2: 从报告文件读取
    result = from_report_file("reports/attribution/daily_2026-08-02.json")
    loop.update_weights(daily_pnl=0.002, factor_contributions=contributions)

    # 方式 3: 纯函数转换 (无适配器实例)
    contributions = convert_to_feedback_loop_format(
        style_contributions={"momentum": 0.001, "value": -0.0005},
        sector_contributions={"tech": 0.002, "defensive": -0.001},
    )
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 项目根目录
_PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 默认归因报告目录
_DEFAULT_REPORT_DIR = _PROJECT_ROOT / "reports" / "attribution"

# 映射前缀: 风格因子 → "style_", 行业因子 → "sector_"
PREFIX_STYLE = "style_"
PREFIX_SECTOR = "sector_"

# 降级状态
STATUS_OK = "ok"
STATUS_DEGRADED = "degraded"
STATUS_EMPTY = "empty"


# ============================================================
# 数据类
# ============================================================


@dataclass
class AttributionConversionResult:
    """归因转换结果.

    将各种归因引擎的输出统一转换为 FeedbackLoop 可用格式.

    Attributes:
        factor_contributions: {factor_name: contribution_to_pnl}
        total_pnl: 总 P&L (从归因结果透传)
        daily_pnl_pct: 日收益率百分比 (total_pnl / portfolio_value)
        attribution_date: 归因日期
        n_factors: 因子总数
        n_style_factors: 风格因子数
        n_sector_factors: 行业因子数
        status: 状态 (ok / degraded / empty)
        reason: 状态说明
        source: 数据来源 (factor_attribution / pnl_engine / report_file)
        original_result: 原始归因结果 (保持向后兼容)
    """

    factor_contributions: dict[str, float] = field(default_factory=dict)
    total_pnl: float = 0.0
    daily_pnl_pct: float = 0.0
    attribution_date: str = ""
    n_factors: int = 0
    n_style_factors: int = 0
    n_sector_factors: int = 0
    status: str = STATUS_OK
    reason: str = ""
    source: str = ""
    original_result: dict[str, Any] = field(default_factory=dict)

    def to_feedback_loop_format(self) -> dict[str, float]:
        """转换为 FeedbackLoop.update_weights() 所需的 factor_contributions 格式.

        Returns:
            {factor_name: contribution_to_pnl} 字典
        """
        return dict(self.factor_contributions)

    def to_dict(self) -> dict[str, Any]:
        """转为字典 (用于序列化/审计)."""
        return {
            "factor_contributions": {
                k: round(v, 6) for k, v in self.factor_contributions.items()
            },
            "total_pnl": round(self.total_pnl, 2),
            "daily_pnl_pct": round(self.daily_pnl_pct, 6),
            "attribution_date": self.attribution_date,
            "n_factors": self.n_factors,
            "n_style_factors": self.n_style_factors,
            "n_sector_factors": self.n_sector_factors,
            "status": self.status,
            "reason": self.reason,
            "source": self.source,
        }

    def is_degraded(self) -> bool:
        """是否降级 (数据缺失或转换异常)."""
        return self.status != STATUS_OK


# ============================================================
# 核心转换函数 (纯函数, 无副作用)
# ============================================================


def convert_to_feedback_loop_format(
    style_contributions: dict[str, float] | None = None,
    sector_contributions: dict[str, float] | None = None,
    total_pnl: float = 0.0,
    attribution_date: str = "",
    prefix_style: str = PREFIX_STYLE,
    prefix_sector: str = PREFIX_SECTOR,
) -> AttributionConversionResult:
    """将风格因子和行业因子贡献转换为 FeedbackLoop 格式.

    核心转换逻辑:
        1. 风格因子 → "{prefix_style}{factor_name}": contribution
        2. 行业因子 → "{prefix_sector}{factor_name}": contribution
        3. 缺失数据 → 返回全零 (中性), 不抛异常

    Args:
        style_contributions: {风格因子名: 对 PnL 的贡献}
        sector_contributions: {行业因子名: 对 PnL 的贡献}
        total_pnl: 总 P&L 金额 (元)
        attribution_date: 归因日期 (YYYY-MM-DD)
        prefix_style: 风格因子前缀 (默认 "style_")
        prefix_sector: 行业因子前缀 (默认 "sector_")

    Returns:
        AttributionConversionResult 转换结果
    """
    style_contributions = style_contributions or {}
    sector_contributions = sector_contributions or {}

    factor_contributions: dict[str, float] = {}

    # 1. 风格因子: 添加前缀映射
    for name, contrib in style_contributions.items():
        if not isinstance(name, str) or not name:
            continue
        key = f"{prefix_style}{name}"
        factor_contributions[key] = float(contrib)

    # 2. 行业因子: 添加前缀映射
    for name, contrib in sector_contributions.items():
        if not isinstance(name, str) or not name:
            continue
        key = f"{prefix_sector}{name}"
        factor_contributions[key] = float(contrib)

    n_style = sum(1 for name in style_contributions if isinstance(name, str) and name)
    n_sector = sum(1 for name in sector_contributions if isinstance(name, str) and name)
    n_total = n_style + n_sector

    # 3. 缺失处理: 因子数为 0 时标记为 empty
    if n_total == 0:
        return AttributionConversionResult(
            status=STATUS_EMPTY,
            reason="归因结果为空: 无风格因子和行业因子贡献",
            attribution_date=attribution_date,
            source="convert_to_feedback_loop_format",
        )

    return AttributionConversionResult(
        factor_contributions=factor_contributions,
        total_pnl=total_pnl,
        attribution_date=attribution_date,
        n_factors=n_total,
        n_style_factors=n_style,
        n_sector_factors=n_sector,
        status=STATUS_OK,
        source="convert_to_feedback_loop_format",
    )


# ============================================================
# 从 FactorAttributionResult 转换
# ============================================================


def from_factor_attribution_result(
    result: Any,
    attribution_date: str = "",
) -> AttributionConversionResult:
    """从 FactorAttributionResult 转换为 FeedbackLoop 格式.

    FactorAttributionResult 包含:
        - style_factor_attributions: list[FactorAttribution]
        - sector_factor_attributions: list[FactorAttribution]
        - total_pnl: 总 P&L
        - attribution_date: 归因日期

    每个 FactorAttribution 对象包含:
        - factor_name: 因子名称
        - contribution_to_pnl: 对 PnL 的贡献 (绝对值)

    Args:
        result: FactorAttributionResult 实例或字典
        attribution_date: 覆盖归因日期 (空则使用 result 中的值)

    Returns:
        AttributionConversionResult 转换结果
    """
    if result is None:
        return AttributionConversionResult(
            status=STATUS_DEGRADED,
            reason="归因结果为空: result is None",
            source="from_factor_attribution_result",
        )

    # 兼容 dict 输入 (如从 JSON 文件反序列化)
    if isinstance(result, dict):
        return _from_factor_attribution_dict(result, attribution_date)

    # 从对象属性提取
    try:
        # 提取风格因子贡献
        style_contributions: dict[str, float] = {}
        style_factors = getattr(result, "style_factor_attributions", None) or []
        for fa in style_factors:
            name = getattr(fa, "factor_name", "")
            contrib = getattr(fa, "contribution_to_pnl", 0.0)
            if name:
                style_contributions[name] = float(contrib)

        # 提取行业因子贡献
        sector_contributions: dict[str, float] = {}
        sector_factors = getattr(result, "sector_factor_attributions", None) or []
        for fa in sector_factors:
            name = getattr(fa, "factor_name", "")
            contrib = getattr(fa, "contribution_to_pnl", 0.0)
            if name:
                sector_contributions[name] = float(contrib)

        # 提取总 P&L 和日期
        total_pnl = float(getattr(result, "total_pnl", 0.0))
        result_date = getattr(result, "attribution_date", "")
        date = attribution_date or result_date or ""

        conv = convert_to_feedback_loop_format(
            style_contributions=style_contributions,
            sector_contributions=sector_contributions,
            total_pnl=total_pnl,
            attribution_date=date,
        )
        conv.source = "from_factor_attribution_result"
        return conv

    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:

        # 数值计算/数据处理异常: 格式/类型/字段/属性/运行时/IO/超时/网络
        logger.warning("FactorAttributionResult 转换异常: %s", e)
        return AttributionConversionResult(
            status=STATUS_DEGRADED,
            reason=f"FactorAttributionResult 转换异常: {e}",
            source="from_factor_attribution_result",
        )


def _from_factor_attribution_dict(
    data: dict[str, Any],
    attribution_date: str = "",
) -> AttributionConversionResult:
    """从字典格式的 FactorAttributionResult 转换."""
    try:
        style_contributions: dict[str, float] = {}
        for fa in data.get("style_factor_attributions", []):
            name = fa.get("factor_name", "")
            contrib = fa.get("contribution_to_pnl", 0.0)
            if name:
                style_contributions[name] = float(contrib)

        sector_contributions: dict[str, float] = {}
        for fa in data.get("sector_factor_attributions", []):
            name = fa.get("factor_name", "")
            contrib = fa.get("contribution_to_pnl", 0.0)
            if name:
                sector_contributions[name] = float(contrib)

        total_pnl = float(data.get("total_pnl", 0.0))
        date = attribution_date or data.get("attribution_date", "")

        conv = convert_to_feedback_loop_format(
            style_contributions=style_contributions,
            sector_contributions=sector_contributions,
            total_pnl=total_pnl,
            attribution_date=date,
        )
        conv.source = "from_factor_attribution_result(dict)"
        return conv

    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:

        # 数值计算/数据处理异常: 格式/类型/字段/属性/运行时/IO/超时/网络
        logger.warning("FactorAttributionResult dict 转换异常: %s", e)
        return AttributionConversionResult(
            status=STATUS_DEGRADED,
            reason=f"FactorAttributionResult dict 转换异常: {e}",
            source="from_factor_attribution_result(dict)",
        )


# ============================================================
# 从报告文件读取
# ============================================================


def from_report_file(
    file_path: str | Path,
    attribution_date: str = "",
) -> AttributionConversionResult:
    """从归因报告文件读取因子贡献.

    支持格式:
        - FactorAttributionResult 的 to_dict() 输出 (含 style_factor_attributions 等)
        - 扁平化 factor_contributions 格式 (含 factor_contributions 字段)

    Args:
        file_path: 报告文件路径 (如 reports/attribution/daily_2026-08-02.json)
        attribution_date: 覆盖归因日期

    Returns:
        AttributionConversionResult 转换结果
    """
    path = Path(file_path) if isinstance(file_path, str) else file_path

    if not path.exists():
        logger.warning("归因报告文件不存在: %s", path)
        return AttributionConversionResult(
            status=STATUS_DEGRADED,
            reason=f"报告文件不存在: {path.name}",
            source="from_report_file",
        )

    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("归因报告文件读取失败: %s (%s)", path, e)
        return AttributionConversionResult(
            status=STATUS_DEGRADED,
            reason=f"报告文件读取失败: {e}",
            source="from_report_file",
        )

    if not isinstance(data, dict):
        return AttributionConversionResult(
            status=STATUS_DEGRADED,
            reason="报告文件内容非 dict",
            source="from_report_file",
        )

    # 检查格式: 扁平化 factor_contributions 格式
    if "factor_contributions" in data:
        try:
            fc = data["factor_contributions"]
            if not isinstance(fc, dict):
                fc = {}
            total_pnl = float(data.get("total_pnl", 0.0))
            date = attribution_date or data.get("attribution_date", "")

            return AttributionConversionResult(
                factor_contributions={k: float(v) for k, v in fc.items()},
                total_pnl=total_pnl,
                attribution_date=date,
                n_factors=len(fc),
                status=STATUS_OK if fc else STATUS_EMPTY,
                reason="" if fc else "归因贡献为空",
                source="from_report_file(flat)",
                original_result=data,
            )
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            # 数值计算/数据处理异常: 格式/类型/字段/属性/运行时/IO/超时/网络
            return AttributionConversionResult(
                status=STATUS_DEGRADED,
                reason=f"扁平化格式解析异常: {e}",
                source="from_report_file",
            )

    # 格式: FactorAttributionResult 的 to_dict() 输出
    return _from_factor_attribution_dict(data, attribution_date)


# ============================================================
# 从 PnLAttributionEngine 的 AttributionResult 转换
# ============================================================


def from_attribution_result(
    result: Any,
    attribution_date: str = "",
) -> AttributionConversionResult:
    """从 PnLAttributionEngine 的 AttributionResult 转换.

    AttributionResult 包含 factor_contributions (list[FactorContribution]):
        - factor_name: 因子名称
        - contribution: 绝对贡献 (元或%)
        - contribution_pct: 占总 P&L 比例

    Args:
        result: AttributionResult 实例或字典
        attribution_date: 覆盖归因日期

    Returns:
        AttributionConversionResult 转换结果
    """
    if result is None:
        return AttributionConversionResult(
            status=STATUS_DEGRADED,
            reason="归因结果为空: result is None",
            source="from_attribution_result",
        )

    if isinstance(result, dict):
        return _from_attribution_result_dict(result, attribution_date)

    try:
        # 提取因子贡献
        factor_contributions_raw: dict[str, float] = {}
        fc_list = getattr(result, "factor_contributions", None) or []
        for fc in fc_list:
            name = getattr(fc, "factor_name", "")
            contrib = getattr(fc, "contribution", 0.0)
            if name:
                factor_contributions_raw[name] = float(contrib)

        total_pnl = float(getattr(result, "total_pnl", 0.0))
        result_date = getattr(result, "attribution_date", "")
        date = attribution_date or result_date or ""

        return AttributionConversionResult(
            factor_contributions=factor_contributions_raw,
            total_pnl=total_pnl,
            attribution_date=date,
            n_factors=len(factor_contributions_raw),
            status=STATUS_OK if factor_contributions_raw else STATUS_EMPTY,
            source="from_attribution_result",
        )

    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:

        # 数值计算/数据处理异常: 格式/类型/字段/属性/运行时/IO/超时/网络
        logger.warning("AttributionResult 转换异常: %s", e)
        return AttributionConversionResult(
            status=STATUS_DEGRADED,
            reason=f"AttributionResult 转换异常: {e}",
            source="from_attribution_result",
        )


def _from_attribution_result_dict(
    data: dict[str, Any],
    attribution_date: str = "",
) -> AttributionConversionResult:
    """从字典格式的 AttributionResult 转换."""
    try:
        factor_contributions_raw: dict[str, float] = {}
        for fc in data.get("factor_contributions", []):
            name = fc.get("factor_name", "")
            contrib = fc.get("contribution", 0.0)
            if name:
                factor_contributions_raw[name] = float(contrib)

        total_pnl = float(data.get("total_pnl", 0.0))
        date = attribution_date or data.get("attribution_date", "")

        return AttributionConversionResult(
            factor_contributions=factor_contributions_raw,
            total_pnl=total_pnl,
            attribution_date=date,
            n_factors=len(factor_contributions_raw),
            status=STATUS_OK if factor_contributions_raw else STATUS_EMPTY,
            source="from_attribution_result(dict)",
        )

    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:

        # 数值计算/数据处理异常: 格式/类型/字段/属性/运行时/IO/超时/网络
        logger.warning("AttributionResult dict 转换异常: %s", e)
        return AttributionConversionResult(
            status=STATUS_DEGRADED,
            reason=f"AttributionResult dict 转换异常: {e}",
            source="from_attribution_result(dict)",
        )


# ============================================================
# 适配器类 (带状态, 适合 EOD 工作流使用)
# ============================================================


class PnLAttributionAdapter:
    """P&L 归因适配器 — 将归因结果接入 FeedbackLoop.

    带状态管理, 适合在 EOD 工作流中长期使用:
        - 记录最近 N 次归因转换历史
        - 自动寻找报告文件
        - 提供降级检测

    Usage:
        from utils.evolution.pnl_attribution_adapter import PnLAttributionAdapter

        adapter = PnLAttributionAdapter()
        conv = adapter.load_from_report("2026-08-02")
        if conv.is_degraded():
            logger.warning("归因数据缺失, 跳过权重更新")
            return

        # 直接传入 FeedbackLoop
        loop.update_weights(
            daily_pnl=conv.daily_pnl_pct,
            factor_contributions=conv.to_feedback_loop_format(),
        )
    """

    def __init__(
        self,
        report_dir: str | Path | None = None,
        max_history: int = 30,
    ) -> None:
        """初始化适配器.

        Args:
            report_dir: 归因报告目录 (默认 reports/attribution/)
            max_history: 最大历史记录数
        """
        self._report_dir = Path(report_dir) if report_dir else _DEFAULT_REPORT_DIR
        self._max_history = max_history
        self._history: list[AttributionConversionResult] = []

    @property
    def report_dir(self) -> Path:
        """归因报告目录."""
        return self._report_dir

    @property
    def history(self) -> list[AttributionConversionResult]:
        """历史转换记录 (最近 N 次)."""
        return list(self._history)

    # ============================================================
    # 加载 API
    # ============================================================

    def load_from_report(
        self,
        attribution_date: str,
    ) -> AttributionConversionResult:
        """从报告文件加载因子贡献.

        Args:
            attribution_date: 归因日期 (YYYY-MM-DD)

        Returns:
            AttributionConversionResult 转换结果
        """
        # 尝试多个文件路径
        candidates = [
            self._report_dir / f"daily_{attribution_date}.json",
            self._report_dir / f"factor_attribution_{attribution_date}.json",
            self._report_dir / f"{attribution_date}.json",
        ]

        for path in candidates:
            if path.exists():
                result = from_report_file(path, attribution_date)
                self._append_history(result)
                return result

        # 所有路径都不存在
        result = AttributionConversionResult(
            status=STATUS_DEGRADED,
            reason=f"未找到归因报告: {attribution_date} (尝试了 {len(candidates)} 个路径)",
            attribution_date=attribution_date,
            source="PnLAttributionAdapter.load_from_report",
        )
        self._append_history(result)
        return result

    def load_from_result(
        self,
        attribution_result: Any,
        source: str = "factor_attribution",
        attribution_date: str = "",
    ) -> AttributionConversionResult:
        """从归因结果对象加载.

        Args:
            attribution_result: FactorAttributionResult 或 AttributionResult
            source: 数据来源标识
            attribution_date: 覆盖归因日期

        Returns:
            AttributionConversionResult 转换结果
        """
        if source == "factor_attribution" or source.startswith("factor"):
            result = from_factor_attribution_result(attribution_result, attribution_date)
        else:
            result = from_attribution_result(attribution_result, attribution_date)

        self._append_history(result)
        return result

    def load_dict(
        self,
        factor_contributions: dict[str, float],
        total_pnl: float = 0.0,
        attribution_date: str = "",
    ) -> AttributionConversionResult:
        """直接从贡献字典加载 (用于测试/手工).

        Args:
            factor_contributions: {factor_name: contribution_to_pnl}
            total_pnl: 总 P&L
            attribution_date: 归因日期

        Returns:
            AttributionConversionResult 转换结果
        """
        result = AttributionConversionResult(
            factor_contributions=dict(factor_contributions),
            total_pnl=total_pnl,
            attribution_date=attribution_date,
            n_factors=len(factor_contributions),
            status=STATUS_OK if factor_contributions else STATUS_EMPTY,
            source="PnLAttributionAdapter.load_dict",
        )
        self._append_history(result)
        return result

    # ============================================================
    # 查询 API
    # ============================================================

    def get_latest(self) -> AttributionConversionResult | None:
        """获取最近一次转换结果."""
        if not self._history:
            return None
        return self._history[-1]

    def clear_history(self) -> None:
        """清空历史记录."""
        self._history.clear()

    # ============================================================
    # 内部方法
    # ============================================================

    def _append_history(self, result: AttributionConversionResult) -> None:
        """追加历史记录 (保持 max_history)."""
        self._history.append(result)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]