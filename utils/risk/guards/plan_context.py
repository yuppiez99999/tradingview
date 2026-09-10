"""风控守卫 — 计划上下文基座 (审计 item 11, 2026-09-10 拆自 utils/risk_guard_integrator.py)。

拆解口径: **零行为变更**, 仅搬移成员与常量, 不改逻辑/字段/落盘路径。

职责:
    - 单一事实源的落盘路径常量 (LOGS_DIR / REPORTS_DIR / TRADE_PLANS_DIR / DAILY_REPORT_DIR);
    - 模块级 logger (名称保持 ``"risk_guard_integrator"``, 日志路由与过滤不因拆包改变);
    - ``PlanContextMixin``: ``_log`` / 盈亏报告与交易计划 IO / 底层标的代码映射。

**硬契约 (后续所有 guard 模块必须遵守)**:

    1. 路径常量一律以 ``plan_context.XXX`` **属性式**访问, 严禁在其它模块写
       ``from utils.risk.guards.plan_context import LOGS_DIR`` —— 后者会把常量值
       固化成模块级副本, 使测试的 ``monkeypatch.setattr("...plan_context.LOGS_DIR", tmp)``
       **静默失效**, 测试产物会写进生产目录 (``logs/`` / ``v8.3_institutional/trade_plans/``)。
       拆解前全仓已有 20+ 处 monkeypatch 依赖这四个常量, 是本批次最高风险点。
    2. ``BASE_DIR`` 由本文件位置反推: ``utils/risk/guards/plan_context.py`` → ``parents[3]``。
       拆解前该值由 ``utils/risk_guard_integrator.py`` 的 ``parents[1]`` 算出,
       两者必须指向同一项目根目录, 由 test_g7_risk_guard_integrator_boost2.py 的
       路径断言保护。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, cast

from utils.datetime_utils import now_bj

logger = logging.getLogger("risk_guard_integrator")

# ============================================================
# 落盘路径 (唯一事实源)
#
# 拆解前: utils/risk_guard_integrator.py 位于 utils/ 下 → parents[1] = 项目根
# 拆解后: utils/risk/guards/plan_context.py 位于三层下 → parents[3] = 项目根
# ============================================================
BASE_DIR = Path(__file__).resolve().parents[3]
TRADE_PLANS_DIR = BASE_DIR / "v8.3_institutional" / "trade_plans"
# v8.6.9 P0 FIX (2026-07-26): 报告路径修正
# 原始 bug: REPORTS_DIR 指向 v8.3_institutional/reports/, 但生产环境实际报告在
# 每日报告归档/{date}/daily_pnl_report_{date}.json
# 影响: _load_pnl_report() 永远找不到报告 → 返回 None → guard_drawdown 跳过回撤检查 ("无有效成本数据")
#       导致回撤防护失效 + vol_target 用空数据计算
# 修复: 保留旧路径作为回退, 优先在 每日报告归档/{date}/ 下查找
REPORTS_DIR = BASE_DIR / "v8.3_institutional" / "reports"
DAILY_REPORT_DIR = BASE_DIR / "每日报告归档"
LOGS_DIR = BASE_DIR / "logs"


class PlanContextMixin:
    """所有 guard mixin 的公共基座: 日志 / 数据 IO / 底层代码映射。

    作为 MRO 首位混入 ``RiskGuardIntegrator``, 统一提供 ``self._log``, 避免各 guard
    mixin 互相引用 ``self._log`` 时产生 mypy 类型债。
    """

    # 由宿主类 (RiskGuardIntegrator.__init__) 赋值的运行期状态。
    # 在本基座声明类型: ① 使 mixin 内 self.report_date/self.log_entries 通过 mypy;
    # ② 明确契约 —— 任何混入本 mixin 的类必须提供这两个属性。
    report_date: str
    total_capital: float
    log_entries: list[str]

    # v7.7: 底层标的代码映射 - 用于对冲引擎与认沽保护引擎的去重
    # HedgeExecutionEngine 用描述性名称 (如 "510050 Put")，
    # ProtectivePutEngine 直接用代码 (如 "510050")
    # 两者需统一映射到 6 位代码以进行去重比较
    UNDERLYING_CODE_MAP = {
        # 上证50
        "510050": "510050",
        "上证50": "510050",
        "50etf": "510050",
        "上证50etf": "510050",
        "sz50": "510050",
        # 科创50
        "588080": "588080",
        "科创50": "588080",
        "科创50etf": "588080",
        "kc50": "588080",
        "588000": "588080",
        # 创业板
        "159915": "159915",
        "创业板": "159915",
        "创业板etf": "159915",
        "cyb": "159915",
        # 沪深300
        "510300": "510300",
        "沪深300": "510300",
        "300etf": "510300",
        "hs300": "510300",
        # 中证500
        "510500": "510500",
        "中证500": "510500",
        "500etf": "510500",
        "zz500": "510500",
        # 中证1000
        "512100": "512100",
        "中证1000": "512100",
        "1000etf": "512100",
        "zz1000": "512100",
    }

    @classmethod
    def _extract_underlying_code(cls, instrument_name: str) -> str | None:
        """从订单的 instrument/underlying 字段提取6位底层代码

        HedgeExecutionEngine 格式: "510050 Put", "科创50ETF Put"
        ProtectivePutEngine 格式: "510050"
        """
        if not instrument_name:
            return None
        name_lower = instrument_name.lower().strip()
        # 直接查映射表
        if name_lower in cls.UNDERLYING_CODE_MAP:
            return cls.UNDERLYING_CODE_MAP[name_lower]
        # 尝试从名称中提取数字 (如 "510050 Put" → "510050")
        import re

        codes = re.findall(r"\b(\d{6})\b", name_lower)
        if codes:
            return str(codes[0])
        # 模糊匹配: 最长优先, 避免 "50etf" 误匹配 "科创50ETF"
        for key in sorted(cls.UNDERLYING_CODE_MAP.keys(), key=len, reverse=True):
            if key in name_lower:
                return cls.UNDERLYING_CODE_MAP[key]
        return None

    def _log(self, msg: str) -> None:
        """记录日志"""
        ts = now_bj().strftime("%Y-%m-%d %H:%M:%S")
        entry = f"[{ts}] [RiskGuard] {msg}"
        self.log_entries.append(entry)
        # 安全打印 — Win GBK 兼容
        # P2 修复 (2026-07-30): logger.info 不接受 flush 参数 (会抛 TypeError),
        # flush 仅属于 logger.debug("---"). 原代码 logger.info(entry, flush=True) 会让整个
        # RiskGuardIntegrator 在首次 _log() 调用时崩溃. 改用 encode 安全降级.
        try:
            logger.info(entry)
        except UnicodeEncodeError:
            # 跨平台安全降级: 用日志处理器实际流编码 (Mac=utf-8, Win=gbk/utf-8) 重新编码,
            # 避免把 emoji/生僻字等合法 UTF-8 字符误删 (原 gbk 编码会在 Mac 上丢字符)
            _enc = "utf-8"
            for _h in logger.handlers:
                _stream = getattr(_h, "stream", None)
                if _stream is not None:
                    _enc = getattr(_stream, "encoding", None) or _enc
                    break
            try:
                safe = entry.encode(_enc, errors="replace").decode(_enc, errors="ignore")
            except (UnicodeEncodeError, LookupError):
                safe = entry.encode("utf-8", errors="replace").decode("utf-8", errors="ignore")
            logger.info(safe)

    def _load_pnl_report(self) -> dict[str, Any] | None:
        """加载当日盈亏报告 (v8.6.9 P0 FIX: 多路径查找)

        查找顺序:
            1. 每日报告归档/{report_date}/daily_pnl_report_{report_date}.json  (生产格式)
            2. 每日报告归档/{report_date}/daily_pnl_report_{report_date无横杠}.json  (兼容)
            3. v8.3_institutional/reports/daily_pnl_report_{report_date}.json  (旧格式回退)
            4. v8.3_institutional/reports/daily_pnl_report_{report_date无横杠}.json  (旧格式回退)
        """
        date_str = self.report_date
        date_no_dash = date_str.replace("-", "")

        # 候选路径列表 (优先级降序)
        # 注: 此处直接引用本模块全局名, 运行时按模块 __dict__ 解析 →
        # monkeypatch.setattr(plan_context, "REPORTS_DIR", ...) 依然生效。
        candidates = [
            DAILY_REPORT_DIR / date_str / f"daily_pnl_report_{date_str}.json",
            DAILY_REPORT_DIR / date_str / f"daily_pnl_report_{date_no_dash}.json",
            REPORTS_DIR / f"daily_pnl_report_{date_str}.json",
            REPORTS_DIR / f"daily_pnl_report_{date_no_dash}.json",
        ]

        for json_path in candidates:
            if json_path.exists():
                try:
                    with open(json_path, encoding="utf-8") as f:
                        self._log(f"[P0-FIX] 已加载盈亏报告: {json_path.name} (path={json_path.parent})")
                        data = json.load(f)
                        return cast(dict[Any, Any], data)
                except (
                    ValueError,
                    KeyError,
                    TypeError,
                    AttributeError,
                    OSError,
                    RuntimeError,
                ) as e:
                    self._log(f"加载盈亏报告失败 ({json_path}): {e}")

        self._log(f"[WARN] 未找到当日盈亏报告, 查找路径: {[str(p) for p in candidates]}")
        return None

    def _get_pnl_summary(self, pnl_report: dict[str, Any]) -> dict[str, Any]:
        """从盈亏报告中提取汇总数据 (v7.7修正: 适配 portfolio_pnl.summary 嵌套结构)"""
        portfolio_pnl = pnl_report.get("portfolio_pnl", {})
        return cast(dict[Any, Any], portfolio_pnl.get("summary", {}))

    def _extract_positions(self, pnl_report: dict[str, Any]) -> list[Any]:
        """从 pnl_report 提取 positions 列表 (v8.6.6: 兼容三种数据位置)

        完整格式 (带横杠文件名 daily_pnl_report_YYYY-MM-DD.json):
            1. pnl_report['portfolio_pnl']['positions'] — 某些版本
            2. pnl_report['portfolio_pnl']['details']   — 当前生产格式 (26 标的)

        简化格式 (无横杠文件名 daily_pnl_report_YYYYMMDD.json):
            3. pnl_report['positions'] — 顶层 (list of dicts)

        Returns:
            positions 列表 (始终为 list, 即使原始是 dict 也会转成 list)
        """
        pp = pnl_report.get("portfolio_pnl", {})
        if isinstance(pp, dict):
            # 1. 完整格式: portfolio_pnl.positions
            positions = pp.get("positions", [])
            if positions:
                if isinstance(positions, dict):
                    return list(positions.values())
                if isinstance(positions, list):
                    return positions
            # 2. 完整格式: portfolio_pnl.details (当前生产格式)
            details = pp.get("details", [])
            if details:
                if isinstance(details, dict):
                    return list(details.values())
                if isinstance(details, list):
                    return details
        # 3. 简化格式: 顶层 positions
        positions = pnl_report.get("positions", [])
        if isinstance(positions, dict):
            return list(positions.values())
        return positions if isinstance(positions, list) else []

    def _extract_summary(self, pnl_report: dict[str, Any]) -> dict[str, Any]:
        """从 pnl_report 提取 summary (v8.6.6: 兼容两种报告格式)

        完整格式: pnl_report['portfolio_pnl']['summary']
        简化格式: pnl_report['summary'] (顶层)
        """
        # 1. 完整格式
        portfolio_pnl = pnl_report.get("portfolio_pnl", {})
        summary = cast(dict[Any, Any], portfolio_pnl.get("summary", {}))
        if summary:
            return summary
        # 2. 简化格式
        return cast(dict[Any, Any], pnl_report.get("summary", {}))

    def _load_next_trade_plan(self, next_date: str) -> dict[str, Any] | None:
        """加载次日交易计划"""
        plan_path = TRADE_PLANS_DIR / f"trade_plan_{next_date.replace('-', '')}.json"
        if not plan_path.exists():
            return None
        try:
            with open(plan_path, encoding="utf-8") as f:
                return cast(dict[str, Any], json.load(f))
        except (
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            OSError,
            RuntimeError,
        ) as e:
            self._log(f"加载次日计划失败: {e}")
            return None

    def _save_trade_plan(self, plan: dict[str, Any], next_date: str) -> None:
        """保存修改后的交易计划"""
        plan_path = TRADE_PLANS_DIR / f"trade_plan_{next_date.replace('-', '')}.json"
        # 先备份
        if plan_path.exists():
            bak_path = plan_path.with_suffix(f".json.bak_{now_bj():%H%M%S}")
            plan_path.rename(bak_path)
        with open(plan_path, "w", encoding="utf-8") as f:
            json.dump(plan, f, ensure_ascii=False, indent=2)
        self._log(f"次日计划已更新: {plan_path.name}")


__all__ = [
    "BASE_DIR",
    "DAILY_REPORT_DIR",
    "LOGS_DIR",
    "PlanContextMixin",
    "REPORTS_DIR",
    "TRADE_PLANS_DIR",
    "logger",
]
