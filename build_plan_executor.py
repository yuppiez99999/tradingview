"""
建仓计划执行器 - Build Plan Executor
=====================================
功能：
  1. 读取 500万建仓计划 JSON 数据
  2. 根据日期自动判断当前所处建仓阶段
  3. 生成具体可执行的交易指令（含上下半场拆分）
  4. 输出 Markdown 交易指令单 + JSON 机器指令

用法：
  python build_plan_executor.py                       # 生成今日交易指令
  python build_plan_executor.py --date 2026-07-06     # 指定日期
  python build_plan_executor.py --date 2026-07-06 --format json  # JSON输出
  python build_plan_executor.py --check-status         # 查看建仓状态
"""

import json
import logging
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

from utils.data_types import normalize_stock_code, safe_float, safe_int

logger = logging.getLogger("build_plan_executor")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PLAN_FILE = os.path.join(BASE_DIR, "500万建仓计划_20260706.json")
OUTPUT_DIR = os.path.join(BASE_DIR, "reports")


@dataclass
class TradeOrder:
    """单笔交易指令"""

    priority: int
    code: str
    name: str
    session: str  # "morning" | "afternoon"
    shares: int
    est_price: float
    limit_price: float  # 实际下单限价（含缓冲）
    est_amount: float
    side: str = "BUY"
    order_type: str = "LIMIT"
    style: str = ""
    risk: str = ""
    note: str = ""
    technical_alpha: Optional[float] = None  # GTJA191 Alpha144 技术因子得分


@dataclass
class DailyTradeSheet:
    """单日交易指令单"""

    trade_date: str
    phase_name: str
    phase_number: int
    total_capital: float
    day_capital: float
    morning_orders: List[TradeOrder] = field(default_factory=list)
    afternoon_orders: List[TradeOrder] = field(default_factory=list)
    paused_orders: List[Dict] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


class BuildPlanExecutor:
    """
    建仓计划执行器

    核心逻辑：
    - 读取 JSON 建仓计划
    - 根据日期匹配当前阶段
    - 生成上下半场拆分交易指令
    - 应用价格缓冲和风控规则
    - v8.0 新增：对冲联动接口，自动触发期货/期权对冲评估
    """

    # 价格缓冲：限价单在预估价格基础上上浮此比例确保成交
    PRICE_BUFFER = 0.008  # 0.8%

    # 暂停阈值：若现价偏离预估价格超过此比例则暂停该标的当日执行
    PRICE_DEVIATION_SKIP = 0.10  # 10%

    # 上下半场拆分比例
    SESSION_SPLIT = 0.50

    # 风格 Beta 映射（用于对冲计算）
    STYLE_BETA_MAP = {
        "科技": 1.20,
        "金融": 0.90,
        "宽基": 0.95,
        "新能源": 1.15,
        "医药": 0.85,
        "资源": 1.10,
        "制造": 1.05,
        "顺周期": 1.10,
        "防御": 0.60,
        "default": 1.00,
    }

    def __init__(self, plan_path: Optional[str] = None):
        self.plan_path = plan_path or PLAN_FILE
        self.plan_data: Optional[Dict] = None
        self._load_plan()

    def _load_plan(self):
        """加载建仓计划 JSON"""
        if not os.path.exists(self.plan_path):
            raise FileNotFoundError(f"建仓计划文件不存在: {self.plan_path}")
        with open(self.plan_path, encoding="utf-8") as f:
            self.plan_data = json.load(f)

    # ---------------------------------------------------------------
    # 阶段匹配
    # ---------------------------------------------------------------

    def get_active_phase(self, target_date: Optional[date] = None) -> Tuple[Optional[Dict], int, str]:
        """
        获取指定日期的活跃建仓阶段

        Returns:
            (phase_summary, phase_index, phase_status)
            phase_status: "active" | "completed" | "not_started" | "during_gap" | "unknown"
        """
        if target_date is None:
            target_date = date.today()

        phase_summaries = self.plan_data["phase_summary"]  # type: ignore[index]
        # 从 plan_data metadata 加载阶段配置 (避免硬编码, 支持计划文件更新)
        metadata_build_phases = self.plan_data.get("metadata", {}).get("build_phases", [])
        if metadata_build_phases:
            build_phases_config = [
                {
                    "phase": i + 1,
                    "start": datetime.strptime(p["start"], "%Y-%m-%d").date(),
                    "duration": p["duration"],
                }
                for i, p in enumerate(metadata_build_phases)
            ]
        else:
            # 回退: 硬编码默认配置 (仅当 metadata 缺失时使用)
            build_phases_config = [
                {"phase": 1, "start": date(2026, 7, 6), "duration": 10},
                {"phase": 2, "start": date(2026, 7, 20), "duration": 15},
                {"phase": 3, "start": date(2026, 8, 10), "duration": 15},
                {"phase": 4, "start": date(2026, 9, 1), "duration": 20},
            ]

        for i, pc in enumerate(build_phases_config):
            phase_end = pc["start"] + timedelta(days=pc["duration"])  # type: ignore[index]
            if pc["start"] <= target_date <= phase_end:  # type: ignore[operator]
                return phase_summaries[i], i, "active"

        # 判断是已完成还是未开始
        if target_date < build_phases_config[0]["start"]:  # type: ignore[operator]
            # 还未开始 - 但返回第一阶段信息
            return phase_summaries[0], 0, "not_started"

        # 判断是否超过最后阶段
        last_phase_end = build_phases_config[-1]["start"] + timedelta(  # type: ignore[index]
            days=build_phases_config[-1]["duration"]
        )  # type: ignore[misc]
        if target_date > last_phase_end:  # type: ignore[operator]
            return None, -1, "completed"

        # 在阶段间隙中，返回最近的已完成阶段
        for i in range(len(build_phases_config) - 1, -1, -1):
            if target_date > build_phases_config[i]["start"]:  # type: ignore[operator]
                return phase_summaries[i], i, "during_gap"

        return None, -1, "unknown"

    # ---------------------------------------------------------------
    # 交易指令生成
    # ---------------------------------------------------------------

    def _adjust_shares_for_multiplier(self, total_shares: int, capital_multiplier: float, idx: int, code: str) -> tuple[int, list]:
        """资金倍率调整：返回 (调整后股数, 警告列表)"""
        warnings = []
        if capital_multiplier >= 1.0:
            return total_shares, warnings

        orig_shares = total_shares
        total_shares = max(0, int(total_shares * capital_multiplier))
        if total_shares == 0 and orig_shares > 0 and idx <= 5:
            total_shares = max(100, int(orig_shares * capital_multiplier))
        if total_shares != orig_shares:
            warnings.append(
                f"资本倍率调整: {code} {orig_shares:,}→{total_shares:,}股 (倍率{capital_multiplier:.0%})"
            )
        return total_shares, warnings

    def _check_price_deviation(self, current_price: Optional[float], est_price: float, code: str, name: str) -> tuple[bool, str]:
        """价格偏离检查：返回 (是否暂停, 暂停原因)"""
        if current_price is None or est_price <= 0:
            return False, ""

        deviation = (current_price - est_price) / est_price
        if abs(deviation) > self.PRICE_DEVIATION_SKIP:
            direction = "高于" if deviation > 0 else "低于"
            pause_reason = (
                f"现价{current_price:.3f}{direction}预估{est_price:.3f} "
                f"{abs(deviation) * 100:.1f}% > 10%阈值"
            )
            return True, pause_reason
        return False, ""

    def _calculate_session_shares(self, total_shares: int, lot_size: int) -> tuple[int, int]:
        """计算上下半场股数（满足最小交易单位）"""
        morning_shares = max(0, int(total_shares * self.SESSION_SPLIT))
        morning_shares = (morning_shares // lot_size) * lot_size

        remaining = total_shares - morning_shares
        afternoon_shares = (remaining // lot_size) * lot_size
        # 将取整余数并入下午批次, 避免丢失股数 (e.g. 250股/100手 → 上午100+下午150 而非 100+100)
        afternoon_shares += remaining % lot_size

        return morning_shares, afternoon_shares

    def _create_trade_order(self, idx: int, code: str, info: dict, session: str, shares: int, est_price: float, limit_price: float) -> TradeOrder:
        """创建 TradeOrder"""
        return TradeOrder(
            priority=idx,
            code=code,
            name=info.get("name", ""),
            session=session,
            shares=shares,
            est_price=est_price,
            limit_price=limit_price,
            est_amount=round(shares * est_price, 2),
            style=info.get("style", ""),
            risk=info.get("risk", ""),
            note=f"{'上午' if session == 'morning' else '下午'}批次 {'09:30-10:30' if session == 'morning' else '14:00-14:30'}",
            technical_alpha=self._calc_technical_alpha(code),
        )

    def _build_empty_sheet(self, target_date, phase_summary, phase_idx, status):
        """构建非活跃状态的空交易单"""
        sheet = DailyTradeSheet(
            trade_date=target_date.strftime("%Y-%m-%d"),
            phase_name="无活跃阶段",
            phase_number=phase_idx + 1 if phase_idx >= 0 else 0,
            total_capital=self.plan_data["metadata"]["total_capital"],
            day_capital=0,
        )
        if status == "completed":
            sheet.warnings.append("建仓计划已全部完成")
        elif status == "not_started":
            sheet.warnings.append("建仓计划尚未开始 (起始日: 2026-07-06)")
        elif status == "during_gap":
            sheet.warnings.append("当前处于阶段间隙，无新开仓指令")
        return sheet

    def _process_asset(self, idx, asset, plan, price_quotes, capital_multiplier):
        """处理单个资产：返回订单字典或 None（跳过）"""
        raw_code = asset["code"]
        code = normalize_stock_code(raw_code)
        info = plan.get(code, {}) or plan.get(raw_code, {})
        est_price = safe_float(info.get("est_price", 0), default=0.0)
        total_shares = safe_int(asset.get("shares"), default=0)

        if est_price <= 0 or total_shares is None or total_shares <= 0:
            return {"warnings": [f"{raw_code} 数据无效，跳过"]}

        total_shares, adj_warnings = self._adjust_shares_for_multiplier(total_shares, capital_multiplier, idx, raw_code)

        current_price = safe_float(price_quotes.get(code)) if price_quotes else None
        should_pause, pause_reason = self._check_price_deviation(current_price, est_price, code, info.get("name", ""))
        if should_pause:
            return {
                "paused": [{
                    "priority": idx,
                    "code": code,
                    "name": info.get("name", ""),
                    "shares": total_shares,
                    "est_price": est_price,
                    "current_price": current_price,
                    "reason": pause_reason,
                }],
                "warnings": [f"{code} {info.get('name', '')} {pause_reason}"],
            }

        target_info = self.plan_data.get("target_portfolio", {}).get(code, {})
        lot_size = safe_int(target_info.get("lots") or info.get("lots"), default=100)
        lot_size = lot_size if lot_size and lot_size > 0 else 100
        morning_shares, afternoon_shares = self._calculate_session_shares(total_shares, lot_size)

        limit_price = round(est_price * (1 + self.PRICE_BUFFER), 3)

        result = {"warnings": adj_warnings}
        if morning_shares > 0:
            result["morning"] = [self._create_trade_order(idx, code, info, "morning", morning_shares, est_price, limit_price)]
        if afternoon_shares > 0:
            result["afternoon"] = [self._create_trade_order(idx, code, info, "afternoon", afternoon_shares, est_price, limit_price)]
        return result

    def generate_daily_orders(
        self,
        target_date: Optional[date] = None,
        price_quotes: Optional[Dict[str, float]] = None,
        capital_multiplier: float = 1.0,
    ) -> DailyTradeSheet:
        """
        生成指定日期的完整交易指令单

        Args:
            target_date: 目标日期，默认今日
            price_quotes: {code: 最新价} 用于价格偏离检查，None则均按正常执行
            capital_multiplier: 建仓资金倍率（0=暂停, 0.5=减半, 1.0=正常）

        Returns:
            DailyTradeSheet 包含上下半场所有订单
        """
        if target_date is None:
            target_date = date.today()

        phase_summary, phase_idx, status = self.get_active_phase(target_date)
        if status != "active":
            return self._build_empty_sheet(target_date, phase_summary, phase_idx, status)

        plan = self.plan_data["position_plan"]  # type: ignore[index]
        phase_assets = phase_summary["assets"]  # type: ignore[index]
        sorted_assets = sorted([a for a in phase_assets if a["shares"] > 0], key=lambda x: -x["amount"])

        morning_orders = []
        afternoon_orders = []
        paused_orders = []
        warnings = []

        for idx, asset in enumerate(sorted_assets, 1):
            result = self._process_asset(idx, asset, plan, price_quotes, capital_multiplier)
            if result is None:
                continue
            morning_orders.extend(result.get("morning", []))
            afternoon_orders.extend(result.get("afternoon", []))
            paused_orders.extend(result.get("paused", []))
            warnings.extend(result.get("warnings", []))

        morning_total = sum(o.est_amount for o in morning_orders)
        afternoon_total = sum(o.est_amount for o in afternoon_orders)
        day_total = morning_total + afternoon_total

        return DailyTradeSheet(
            trade_date=target_date.strftime("%Y-%m-%d"),
            phase_name=phase_summary["name"],  # type: ignore[index]
            phase_number=phase_summary["phase"],  # type: ignore[index]
            total_capital=self.plan_data["metadata"]["total_capital"],  # type: ignore[index]
            day_capital=round(day_total, 2),
            morning_orders=morning_orders,
            afternoon_orders=afternoon_orders,
            paused_orders=paused_orders,
            warnings=warnings,
        )

    # ---------------------------------------------------------------
    # GTJA191 技术因子
    # ---------------------------------------------------------------

    @staticmethod
    def _calc_technical_alpha(code: str) -> Optional[float]:
        """
        计算 GTJA191 Alpha144 映射后的 technical_alpha 得分。

        若因子库或历史数据不可用，则返回 None，不影响正常下单流程。
        """
        try:
            from utils.data_provider import get_historical_data
            from utils.gtja191_factors import GTJA191Factors

            df = get_historical_data(code, period="6m")
            if df is None or df.empty or "close" not in df.columns or "amount" not in df.columns:
                return None

            factors = GTJA191Factors(lookback=20)
            value = factors.alpha144(df)
            if value is None:
                return None

            score = max(-1.0, min(1.0, 1.0 - float(value) * 1e8))
            return round(float(score), 4)
        except Exception as e:  # noqa: BLE001
            logger.exception(f"评分计算失败, 已降级返回 None: {e}")
            return None

    # ---------------------------------------------------------------
    # 报告输出
    # ---------------------------------------------------------------

    def format_trade_sheet_markdown(self, sheet: DailyTradeSheet) -> str:
        """将交易指令单格式化为Markdown报告"""
        lines = []

        lines.append(f"# 建仓交易指令单 — {sheet.trade_date}")
        lines.append("")
        lines.append(f"**阶段**: {sheet.phase_name} (第{sheet.phase_number}阶段)")
        lines.append(f"**日期**: {sheet.trade_date}")
        lines.append(f"**总资金**: {sheet.total_capital:,.0f} 元")
        lines.append(f"**当日计划金额**: {sheet.day_capital:,.0f} 元")
        lines.append(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("")

        # 告警
        if sheet.warnings:
            lines.append("## 告警")
            lines.append("")
            for w in sheet.warnings:
                lines.append(f"- {w}")
            lines.append("")

        # 上午批次
        if sheet.morning_orders:
            lines.append("## 上午批次 (09:30 — 10:30)")
            lines.append("")
            lines.append("| 优先级 | 代码 | 名称 | 买入股数 | 预估单价 | 限价 | 预估金额 | 风格 | technical_alpha |")
            lines.append("|:-------|:-----|:-----|--------:|--------:|------|--------:|:-----|--------:|")

            for o in sheet.morning_orders:
                alpha_str = f"{o.technical_alpha:.4f}" if o.technical_alpha is not None else "N/A"
                lines.append(
                    f"| {o.priority} | {o.code} | {o.name} | "
                    f"{o.shares:,} | {o.est_price:.3f} | {o.limit_price:.3f} | "
                    f"{o.est_amount:,.0f} | {o.style} | {alpha_str} |"
                )

            morning_total = sum(o.est_amount for o in sheet.morning_orders)
            lines.append(f"| | | **上午合计** | | | | **{morning_total:,.0f}** | |")
            lines.append("")

        # 下午批次
        if sheet.afternoon_orders:
            lines.append("## 下午批次 (14:00 — 14:30)")
            lines.append("")
            lines.append("| 优先级 | 代码 | 名称 | 买入股数 | 预估单价 | 限价 | 预估金额 | 风格 | technical_alpha |")
            lines.append("|:-------|:-----|:-----|--------:|--------:|------|--------:|:-----|--------:|")

            for o in sheet.afternoon_orders:
                alpha_str = f"{o.technical_alpha:.4f}" if o.technical_alpha is not None else "N/A"
                lines.append(
                    f"| {o.priority} | {o.code} | {o.name} | "
                    f"{o.shares:,} | {o.est_price:.3f} | {o.limit_price:.3f} | "
                    f"{o.est_amount:,.0f} | {o.style} | {alpha_str} |"
                )

            afternoon_total = sum(o.est_amount for o in sheet.afternoon_orders)
            lines.append(f"| | | **下午合计** | | | | **{afternoon_total:,.0f}** | |")
            lines.append("")

        # 暂停标的
        if sheet.paused_orders:
            lines.append("## 暂停执行标的")
            lines.append("")
            lines.append("| 代码 | 名称 | 计划股数 | 预估单价 | 现价 | 暂停原因 |")
            lines.append("|:-----|:-----|--------:|--------:|------:|:---------|")
            for p in sheet.paused_orders:
                cp = p.get("current_price", "N/A")
                if isinstance(cp, (int, float)):
                    cp = f"{cp:.3f}"
                lines.append(
                    f"| {p['code']} | {p['name']} | {p['shares']:,} | {p['est_price']:.3f} | {cp} | {p['reason']} |"
                )
            lines.append("")

        # 执行摘要
        lines.append("## 执行摘要")
        lines.append("")
        lines.append(f"- 上午订单: {len(sheet.morning_orders)} 笔")
        lines.append(f"- 下午订单: {len(sheet.afternoon_orders)} 笔")
        lines.append(f"- 暂停标的: {len(sheet.paused_orders)} 个")
        lines.append(f"- 当日总金额: {sheet.day_capital:,.0f} 元")
        lines.append("")

        # 执行检查清单
        lines.append("## 执行前检查清单")
        lines.append("")
        lines.append("- [ ] 确认账户可用资金充足")
        lines.append("- [ ] 确认所有标的交易权限正常")
        lines.append("- [ ] 09:20 查看集合竞价，确认市场开盘情绪")
        lines.append("- [ ] 09:25 记录集合竞价产生的开盘参考价")
        lines.append("- [ ] 09:30-10:30 按优先级顺序执行上午批次")
        lines.append("- [ ] 11:30 确认上午成交，记录实际成交价")
        lines.append("- [ ] 14:00-14:30 执行下午批次")
        lines.append("- [ ] 15:00 确认全天成交，记录实际成本")
        lines.append("- [ ] 15:00 将剩余资金转入短融ETF(511360)")
        lines.append("")

        lines.append("---")
        lines.append(f"*指令单生成: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")

        return "\n".join(lines)

    def format_trade_sheet_json(self, sheet: DailyTradeSheet) -> str:
        """将交易指令单输出为JSON"""
        data = {
            "trade_date": sheet.trade_date,
            "phase": {
                "name": sheet.phase_name,
                "number": sheet.phase_number,
            },
            "total_capital": sheet.total_capital,
            "day_capital": sheet.day_capital,
            "generated_at": datetime.now().isoformat(),
            "morning_orders": [{**asdict(o), "session": o.session} for o in sheet.morning_orders],
            "afternoon_orders": [{**asdict(o), "session": o.session} for o in sheet.afternoon_orders],
            "paused_orders": sheet.paused_orders,
            "warnings": sheet.warnings,
        }
        return json.dumps(data, ensure_ascii=False, indent=2)

    # ---------------------------------------------------------------
    # 建仓状态查询
    # ---------------------------------------------------------------

    def get_build_status(self) -> Dict:
        """获取建仓整体状态"""
        today = date.today()
        phase_summary, phase_idx, status = self.get_active_phase(today)

        # 已完成阶段统计
        completed_capital = 0.0
        if status == "completed":
            phase_range = range(len(self.plan_data["phase_summary"]))  # type: ignore[index]
        elif status == "during_gap":
            phase_range = range(phase_idx + 1)
        else:  # active / not_started
            phase_range = range(phase_idx)

        for i in phase_range:
            if i < len(self.plan_data["phase_summary"]):  # type: ignore[index]
                completed_capital += self.plan_data["phase_summary"][i]["capital_amount"]  # type: ignore[index]
        # 整体进度
        total_capital = self.plan_data["metadata"]["total_capital"]  # type: ignore[index]
        progress = min(completed_capital / total_capital, 1.0) if total_capital > 0 else 0

        status_info = {
            "date": today.strftime("%Y-%m-%d"),
            "status": status,
            "total_capital": total_capital,
            "completed_capital": completed_capital,
            "progress": round(progress * 100, 1),
            "current_phase": None,
            "target_count": self.plan_data["metadata"]["target_count"],  # type: ignore[index]
            "build_phases": self.plan_data["metadata"]["build_phases"],  # type: ignore[index]
        }

        # current_phase 仅在 active 状态下填充, during_gap 时不填充 (避免误导 API 消费者)
        if status == "active" and phase_summary:
            status_info["current_phase"] = {
                "phase": phase_summary["phase"],
                "name": phase_summary["name"],
                "start": phase_summary["start"],
                "capital_amount": phase_summary["capital_amount"],
                "capital_ratio": phase_summary["capital_ratio"],
                "asset_count": phase_summary["asset_count"],
            }

        return status_info

    # ---------------------------------------------------------------
    # 保存
    # ---------------------------------------------------------------

    def save_trade_sheet(self, sheet: DailyTradeSheet, output_dir: Optional[str] = None):
        """保存交易指令单到文件"""
        out_dir = output_dir or OUTPUT_DIR
        os.makedirs(out_dir, exist_ok=True)

        date_str = sheet.trade_date.replace("-", "")

        # Markdown 版本
        md_path = os.path.join(out_dir, f"trade_orders_{date_str}.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(self.format_trade_sheet_markdown(sheet))

        # JSON 版本
        json_path = os.path.join(out_dir, f"trade_orders_{date_str}.json")
        with open(json_path, "w", encoding="utf-8") as f:
            f.write(self.format_trade_sheet_json(sheet))

        return md_path, json_path

    # ---------------------------------------------------------------
    # 极端情景应对协议 (v2.0 新增)
    # ---------------------------------------------------------------

    def get_emergency_protocol(self, market_state: Dict) -> Dict:
        """
        获取紧急响应协议建议 (v7.1 增强版)

        基于市场状态 + ETF资金流向 + 宏观热度综合判断返回防御性操作清单

        Args:
            market_state: 市场状态字典，包含 vix_proxy, index_return_20d,
                          etf_flows, macro_heat_score 等

        Returns:
            紧急协议字典，包含建议操作和优先级
        """
        vix = market_state.get("vix_proxy", 20)
        ret_20d = market_state.get("index_return_20d", 0)
        ret_5d = market_state.get("index_return_5d", 0)
        margin_chg = market_state.get("margin_balance_change", 0)
        mfg_dd = market_state.get("sector_health", {}).get("high_end_manufacturing_20d", 0)

        # ---- v7.1 新增维度 ----
        # ETF资金流向信号
        etf_flows = market_state.get("etf_flows", {})
        etf_signal = etf_flows.get("overall_signal", "neutral")  # bullish/bearish/neutral
        etf_net_flow = etf_flows.get("net_flow_billion", 0)  # 净流入/出（亿）

        # 宏观实体经济热度 (0~100, >80=过热, <30=过冷)
        macro_heat = market_state.get("macro_heat_score", 50)
        macro_regime = market_state.get("macro_regime", "中性")

        protocol = {
            "level": 0,
            "level_name": "NORMAL",
            "day_capital_multiplier": 1.0,
            "actions": [],
            "hedge_suggestions": [],
            # v7.1 新增字段
            "etf_signal": etf_signal,
            "macro_heat_score": macro_heat,
            "macro_regime": macro_regime,
        }

        # ---- 极端：最高优先级 ----
        # VIX>=50 或 5日跌幅>12% 或 20日跌幅>25% 或 两融降>15%
        # v7.1: 宏观过热(>85)叠加ETF大幅流出亦触发极端
        if (
            vix >= 50
            or abs(ret_5d) > 0.12
            or abs(ret_20d) > 0.25
            or margin_chg < -0.15
            or (macro_heat > 85 and etf_signal == "bearish" and etf_net_flow < -100)
        ):
            protocol["level"] = 4
            protocol["level_name"] = "EXTREME"
            protocol["day_capital_multiplier"] = 0.0
            protocol["actions"] = [
                "1. 立即停止所有建仓操作",
                "2. 对已建仓位执行保护性止损（全部设限价止损单）",
                "3. 联系券商确认专项处置通道可用性",
                "4. 如持有科创50/半导体仓位，考虑买入平值Put保护",
                "5. 增加现金比例至30%以上",
                "6. 转入纯防御模式：仅持有国债ETF+黄金+现金",
            ]
            if macro_heat > 85:
                protocol["actions"].append(f"7. 宏观预警: 实体经济热度{macro_heat:.0f}，处于过热区间({macro_regime})")
            if etf_signal == "bearish":
                protocol["actions"].append(f"8. 资金预警: 国家队ETF净流出{abs(etf_net_flow):.0f}亿，主力撤离信号")
            protocol["hedge_suggestions"] = self._get_hedge_suggestions("extreme")

        # 红色：VIX>40 或 双周>15% 或 两融降>10%
        # v7.1: ETF持续流出（净流出>50亿）或宏观过热亦触发红色
        elif (
            vix > 40
            or abs(ret_20d) > 0.15
            or margin_chg < -0.10
            or (etf_signal == "bearish" and etf_net_flow < -50)
            or macro_heat > 80
        ):
            protocol["level"] = 3
            protocol["level_name"] = "CRITICAL"
            protocol["day_capital_multiplier"] = 0.0
            protocol["actions"] = [
                "1. 今日暂停所有建仓",
                "2. 现有仓位不动，设紧密止损（-5%触发）",
                "3. 对科创50仓位买入OTM Put对冲（行权价=当前价×0.92）",
                "4. 提高债券ETF和现金权重至组合25%",
                "5. 监控两融余额变化，若继续恶化则启动极端协议",
            ]
            if etf_signal == "bearish":
                protocol["actions"].append(f"6. 资金面预警: ETF净流出{abs(etf_net_flow):.0f}亿，主力机构在撤退")
            if macro_heat > 80:
                protocol["actions"].append(
                    f"7. 宏观预警: 实体经济热度{macro_heat:.0f}({macro_regime})，注意过热回调风险"
                )
            protocol["hedge_suggestions"] = self._get_hedge_suggestions("critical")

        # 橙色：VIX>35 或 单周>8%
        # v7.1: ETF小幅流出（净流出>20亿）亦触发橙色
        elif vix > 35 or abs(ret_5d) > 0.08 or (etf_signal == "bearish" and etf_net_flow < -20):
            protocol["level"] = 2
            protocol["level_name"] = "HIGH"
            protocol["day_capital_multiplier"] = 0.0
            protocol["actions"] = [
                "1. 今日暂停建仓，等待市场稳定",
                "2. 密切监控已建仓位表现",
                "3. 如高端制造板块单周跌幅>5%，启动行业轮出",
                "4. 准备科创50虚值Put（行权价=当前价×0.90）",
            ]
            if etf_signal == "bearish":
                protocol["actions"].append(f"5. 资金面: ETF净流出{abs(etf_net_flow):.0f}亿，关注持续性")
            protocol["hedge_suggestions"] = self._get_hedge_suggestions("high")

        # 黄色：VIX>30 或 单日>3% 或 两融降>5%
        # v7.1: ETF小幅流出或宏观偏冷也触发黄色
        elif (
            vix > 30
            or abs(ret_5d / 5) > 0.03
            or margin_chg < -0.05
            or (etf_signal == "bearish" and etf_net_flow < -5)
            or macro_heat < 30
        ):
            protocol["level"] = 1
            protocol["level_name"] = "MEDIUM"
            protocol["day_capital_multiplier"] = 0.50
            protocol["actions"] = [
                "1. 建仓金额减半，优先执行核心仓位",
                "2. 暂停高风险标的（单日波动率>3%的标的）的建仓",
                "3. 增加现金储备至10-15%",
                "4. 关注高端制造板块止盈/止损触发条件",
            ]
            if etf_signal == "bearish":
                protocol["actions"].append(f"5. 资金面: ETF小幅净流出{abs(etf_net_flow):.0f}亿，保持警惕")
            if macro_heat < 30:
                protocol["actions"].append(f"6. 宏观预警: 实体经济偏冷({macro_heat:.0f})，注意经济下行对市场的拖累")
            protocol["hedge_suggestions"] = self._get_hedge_suggestions("medium")

        # 行业集中度特殊检测
        if abs(mfg_dd) > 0.15 and protocol["level"] < 2:
            protocol["level"] = max(protocol["level"], 2)
            protocol["level_name"] = "HIGH"
            protocol["actions"].append(f"行业预警: 高端制造板块20日回撤{mfg_dd:.1%}，建议启动风格对冲")

        # v7.1: ETF流向与宏观背离信号检测（即使VIX较低也可能有隐忧）
        if etf_signal == "bearish" and macro_heat > 70 and protocol["level"] < 1:
            protocol["level"] = max(protocol["level"], 1)
            protocol["day_capital_multiplier"] = min(protocol["day_capital_multiplier"], 0.70)
            protocol["actions"].append(
                f"背离信号: 宏观偏热({macro_heat:.0f})但ETF资金流出{abs(etf_net_flow):.0f}亿，主力可能在获利了结"
            )

        return protocol

    def _get_hedge_suggestions(self, scenario: str) -> List[Dict]:
        """
        获取保护性对冲建议

        针对建仓组合的轻量级对冲方案（不需要v7.0的完整5层架构）

        Args:
            scenario: 场景级别 (medium/high/critical/extreme)

        Returns:
            对冲建议列表
        """
        suggestions = {
            "medium": [
                {
                    "type": "现金储备",
                    "target": "将现金/短融ETF提升至组合的10-15%",
                    "action": "减少当日建仓，增加511360短融ETF持有",
                    "cost": "无直接成本，机会成本约年化2%",
                    "protection": "提供流动性缓冲，极端行情可低位补仓",
                },
            ],
            "high": [
                {
                    "type": "现金储备",
                    "target": "将现金/短融ETF提升至组合的15-20%",
                    "action": "暂停建仓，转持511360短融ETF + 511260国债ETF",
                    "cost": "无直接成本，机会成本约年化2.5%",
                    "protection": "组合的流动性安全垫",
                },
                {
                    "type": "虚值看跌期权（科创50）",
                    "target": "对冲科创50ETF约30%名义价值的尾部风险",
                    "action": "买入科创50 ETF OTM Put，行权价≈当前价×0.90，期限3个月",
                    "cost": "权利金约对冲名义金额的2-3%（约3-5万元）",
                    "protection": "科创50跌超10%时提供非线性赔付",
                    "prerequisite": "需要期权交易权限",
                },
            ],
            "critical": [
                {
                    "type": "防御性调仓",
                    "target": "将组合防御比例提升至25-30%",
                    "action": "减仓高端制造ETF，增持511260国债ETF和518880黄金ETF",
                    "cost": "交易成本+可能卖出亏损",
                    "protection": "降低组合Beta，提高抗跌性",
                },
                {
                    "type": "实值看跌期权（科创50）",
                    "target": "对冲科创50ETF约50%名义价值",
                    "action": "买入科创50 ETF ATM Put，行权价≈当前价×0.95，期限3个月",
                    "cost": "权利金约对冲名义金额的5-7%（约8-15万元）",
                    "protection": "对科创50持仓提供接近1:1的下行保护",
                    "prerequisite": "需要期权交易权限",
                },
            ],
            "extreme": [
                {
                    "type": "全面防御模式",
                    "target": "仅保留国债ETF+黄金+现金",
                    "action": "系统性减仓：先减高风险，再减中风险，保留低风险",
                    "cost": "清仓损失+交易成本",
                    "protection": "最大限度保护剩余资金",
                },
                {
                    "type": "深度虚值Put（科创50+创业50）",
                    "target": "对冲剩余高Beta仓位",
                    "action": "若不全部清仓，对剩余仓位买入深度虚值Put保护",
                    "cost": "权利金约对冲金额的1-2%",
                    "protection": "极端下跌中的非线性收益（凸性保护）",
                    "prerequisite": "需要期权交易权限",
                },
            ],
        }

        return suggestions.get(scenario, [])

    # ---------------------------------------------------------------
    # 对冲联动接口 (v8.0 新增)
    # ---------------------------------------------------------------

    def generate_hedge_input(self, sheet: DailyTradeSheet) -> Dict:
        """
        生成对冲计算所需的输入数据

        将建仓指令转换为对冲引擎可用的格式，包含：
        - 组合持仓结构（代码、数量、价格）
        - 风格分布与组合 Beta
        - 行业集中度
        - 潜在风险暴露

        Args:
            sheet: 单日交易指令单

        Returns:
            对冲输入数据字典
        """
        positions = {}  # type: ignore[misc]
        prices = {}
        style_counts = {}  # type: ignore[misc]
        style_amounts = {}  # type: ignore[misc]
        all_orders = sheet.morning_orders + sheet.afternoon_orders
        for order in all_orders:
            code = order.code
            positions[code] = positions.get(code, 0) + order.shares
            prices[code] = order.est_price
            style = order.style or "default"
            style_counts[style] = style_counts.get(style, 0) + 1
            style_amounts[style] = style_amounts.get(style, 0) + order.est_amount

        portfolio_value = sum(positions.get(c, 0) * prices.get(c, 0) for c in positions)

        portfolio_beta = 0.0
        for code, shares in positions.items():
            amt = shares * prices.get(code, 0)
            order = next((o for o in all_orders if o.code == code), None)  # type: ignore[misc]
            style = order.style if order else "default"
            beta = self.STYLE_BETA_MAP.get(style, 1.0)
            portfolio_beta += (amt / portfolio_value) * beta if portfolio_value > 0 else 0

        style_weights = {
            style: round(amt / portfolio_value * 100, 1) for style, amt in style_amounts.items() if portfolio_value > 0
        }

        top_style = max(style_weights, key=style_weights.get, default="")  # type: ignore[misc]
        max_style_weight = style_weights.get(top_style, 0)

        hedge_input = {
            "trade_date": sheet.trade_date,
            "phase": sheet.phase_name,
            "total_value": round(portfolio_value, 2),
            "position_count": len(positions),
            "portfolio_beta": round(portfolio_beta, 4),
            "style_weights": style_weights,
            "top_style": top_style,
            "max_style_weight": max_style_weight,
            "is_concentrated": max_style_weight > 30,
            "positions": {
                code: {
                    "shares": positions[code],
                    "price": prices[code],
                    "amount": round(positions[code] * prices[code], 2),
                    "style": next((o.style for o in all_orders if o.code == code), "default"),
                    "beta": self.STYLE_BETA_MAP.get(
                        next((o.style for o in all_orders if o.code == code), "default"), 1.0
                    ),
                }
                for code in positions
            },
            "order_count": len(all_orders),
            "morning_order_count": len(sheet.morning_orders),
            "afternoon_order_count": len(sheet.afternoon_orders),
        }

        return hedge_input

    def generate_complete_plan(
        self,
        target_date: Optional[date] = None,
        price_quotes: Optional[Dict[str, float]] = None,
        market_state: Optional[Dict] = None,
        capital_multiplier: float = 1.0,
    ) -> Dict:
        """
        生成完整的建仓+对冲联合计划

        v8.0 核心接口：一次性生成建仓指令 + 对冲建议 + 风险评估

        Args:
            target_date: 目标日期
            price_quotes: 实时价格字典
            market_state: 市场状态字典（用于紧急协议和对冲策略）
            capital_multiplier: 资金倍率

        Returns:
            完整计划字典，包含建仓指令和对冲计划
        """
        if target_date is None:
            target_date = date.today()

        if market_state is None:
            market_state = {}

        sheet = self.generate_daily_orders(
            target_date=target_date,
            price_quotes=price_quotes,
            capital_multiplier=capital_multiplier,
        )

        protocol = self.get_emergency_protocol(market_state)

        hedge_input = self.generate_hedge_input(sheet)

        complete_plan = {
            "trade_date": sheet.trade_date,
            "phase": {
                "name": sheet.phase_name,
                "number": sheet.phase_number,
            },
            "emergency_protocol": protocol,
            "build_plan": {
                "total_capital": sheet.total_capital,
                "day_capital": sheet.day_capital,
                "morning_orders": [asdict(o) for o in sheet.morning_orders],
                "afternoon_orders": [asdict(o) for o in sheet.afternoon_orders],
                "paused_orders": sheet.paused_orders,
                "warnings": sheet.warnings,
            },
            "hedge_input": hedge_input,
            "generated_at": datetime.now().isoformat(),
        }

        return complete_plan


# ================================================================
# CLI 入口
# ================================================================
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="建仓计划执行器 - 生成可执行交易指令",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  python build_plan_executor.py                           # 生成今日交易指令
  python build_plan_executor.py --date 2026-07-06         # 生成7月6日指令
  python build_plan_executor.py --date 2026-07-06 --json  # JSON格式输出
  python build_plan_executor.py --check-status            # 查看建仓状态
        """,
    )
    parser.add_argument("--date", "-d", type=str, default=None, help="目标日期 YYYY-MM-DD (默认: 今日)")
    parser.add_argument("--json", action="store_true", help="以 JSON 格式输出")
    parser.add_argument("--check-status", action="store_true", help="查看建仓当前状态")
    parser.add_argument("--plan-file", type=str, default=None, help="建仓计划 JSON 文件路径")

    args = parser.parse_args()

    executor = BuildPlanExecutor(args.plan_file)

    # 建仓状态查询
    if args.check_status:
        status = executor.get_build_status()
        print(json.dumps(status, ensure_ascii=False, indent=2))
        sys.exit(0)

    # 解析目标日期
    if args.date:
        target_date = datetime.strptime(args.date, "%Y-%m-%d").date()
    else:
        target_date = date.today()

    # 生成交易指令
    sheet = executor.generate_daily_orders(target_date)

    # 保存
    md_path, json_path = executor.save_trade_sheet(sheet)

    # 输出
    if args.json:
        print(executor.format_trade_sheet_json(sheet))
    else:
        print(executor.format_trade_sheet_markdown(sheet))

    print("\n文件已保存:", file=sys.stderr)
    print(f"  Markdown: {md_path}", file=sys.stderr)
    print(f"  JSON:     {json_path}", file=sys.stderr)
