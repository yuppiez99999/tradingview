# -*- coding: utf-8 -*-
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

import os
import sys
import json
import math
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field, asdict

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PLAN_FILE = os.path.join(BASE_DIR, "500万建仓计划_20260706.json")
OUTPUT_DIR = os.path.join(BASE_DIR, "reports")


@dataclass
class TradeOrder:
    """单笔交易指令"""
    priority: int
    code: str
    name: str
    session: str           # "morning" | "afternoon"
    shares: int
    est_price: float
    limit_price: float     # 实际下单限价（含缓冲）
    est_amount: float
    side: str = "BUY"
    order_type: str = "LIMIT"
    style: str = ""
    risk: str = ""
    note: str = ""


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
    """

    # 价格缓冲：限价单在预估价格基础上上浮此比例确保成交
    PRICE_BUFFER = 0.008   # 0.8%

    # 暂停阈值：若现价偏离预估价格超过此比例则暂停该标的当日执行
    PRICE_DEVIATION_SKIP = 0.10   # 10%

    # 上下半场拆分比例
    SESSION_SPLIT = 0.50

    def __init__(self, plan_path: Optional[str] = None):
        self.plan_path = plan_path or PLAN_FILE
        self.plan_data: Optional[Dict] = None
        self._load_plan()

    def _load_plan(self):
        """加载建仓计划 JSON"""
        if not os.path.exists(self.plan_path):
            raise FileNotFoundError(f"建仓计划文件不存在: {self.plan_path}")
        with open(self.plan_path, 'r', encoding='utf-8') as f:
            self.plan_data = json.load(f)

    # ---------------------------------------------------------------
    # 阶段匹配
    # ---------------------------------------------------------------

    def get_active_phase(self, target_date: Optional[date] = None
                         ) -> Tuple[Optional[Dict], int, str]:
        """
        获取指定日期的活跃建仓阶段

        Returns:
            (phase_summary, phase_index, phase_status)
            phase_status: "active" | "completed" | "not_started"
        """
        if target_date is None:
            target_date = date.today()

        phase_summaries = self.plan_data["phase_summary"]

        # 检查是否在某个阶段范围内（阶段起始 + 持续天数）
        build_phases_config = [
            {"phase": 1, "start": date(2026, 7, 6),  "duration": 10},
            {"phase": 2, "start": date(2026, 7, 20), "duration": 15},
            {"phase": 3, "start": date(2026, 8, 10),  "duration": 15},
            {"phase": 4, "start": date(2026, 9, 1),  "duration": 20},
        ]

        for i, pc in enumerate(build_phases_config):
            phase_end = pc["start"] + timedelta(days=pc["duration"])
            if pc["start"] <= target_date <= phase_end:
                return phase_summaries[i], i, "active"

        # 判断是已完成还是未开始
        if target_date < build_phases_config[0]["start"]:
            # 还未开始 - 但返回第一阶段信息
            return phase_summaries[0], 0, "not_started"

        # 判断是否超过最后阶段
        last_phase_end = build_phases_config[-1]["start"] + timedelta(
            days=build_phases_config[-1]["duration"])
        if target_date > last_phase_end:
            return None, -1, "completed"

        # 在阶段间隙中，返回最近的已完成阶段
        for i in range(len(build_phases_config) - 1, -1, -1):
            if target_date > build_phases_config[i]["start"]:
                return phase_summaries[i], i, "during_gap"

        return None, -1, "unknown"

    # ---------------------------------------------------------------
    # 交易指令生成
    # ---------------------------------------------------------------

    def generate_daily_orders(self,
                               target_date: Optional[date] = None,
                               price_quotes: Optional[Dict[str, float]] = None,
                               capital_multiplier: float = 1.0
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

        # 处理非活跃状态
        if status != "active":
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

        # 获取阶段内所有标的
        plan = self.plan_data["position_plan"]
        phase_assets = phase_summary["assets"]

        # 按金额降序排列（大权重优先执行）
        sorted_assets = sorted(
            [a for a in phase_assets if a["shares"] > 0],
            key=lambda x: -x["amount"]
        )

        # 生成订单
        morning_orders = []
        afternoon_orders = []
        paused_orders = []
        warnings = []

        for idx, asset in enumerate(sorted_assets, 1):
            code = asset["code"]
            info = plan.get(code, {})
            est_price = float(info.get("est_price", 0))
            total_shares = int(asset["shares"])

            # ---- 资金倍率调整：紧急响应时按比例缩减 ----
            if capital_multiplier < 1.0:
                orig_shares = total_shares
                total_shares = int(total_shares * capital_multiplier)
                # 确保至少保留高优先级标的的部分仓位
                if total_shares == 0 and orig_shares > 0 and idx <= 5:
                    total_shares = max(100, int(orig_shares * 0.10))  # 最少10%保留
                if total_shares != orig_shares:
                    warnings.append(
                        f"资本倍率调整: {code} {info.get('name','')} "
                        f"{orig_shares:,}→{total_shares:,}股 (倍率{capital_multiplier:.0%})"
                    )

            # 检查是否有实时价格
            current_price = None
            if price_quotes and code in price_quotes:
                current_price = price_quotes[code]

            # 价格偏离检查
            should_pause = False
            pause_reason = ""
            if current_price and est_price > 0:
                deviation = (current_price - est_price) / est_price
                if abs(deviation) > self.PRICE_DEVIATION_SKIP:
                    should_pause = True
                    direction = "高于" if deviation > 0 else "低于"
                    pause_reason = (
                        f"现价{current_price:.3f}{direction}预估{est_price:.3f}"
                        f" {abs(deviation)*100:.1f}% > 10%阈值"
                    )
                    warnings.append(f"{code} {info.get('name', '')} {pause_reason}")

            if should_pause:
                paused_orders.append({
                    "priority": idx,
                    "code": code,
                    "name": info.get("name", ""),
                    "shares": total_shares,
                    "est_price": est_price,
                    "current_price": current_price,
                    "reason": pause_reason,
                })
                continue

            # 获取最小交易单位 (优先从 target_portfolio 读取)
            target_info = self.plan_data.get("target_portfolio", {}).get(code, {})
            lot_size = int(target_info.get("lots") or info.get("lots") or 100)

            # 计算上下半场股数，并确保满足最小交易单位
            morning_shares = max(0, int(total_shares * self.SESSION_SPLIT))
            morning_shares = (morning_shares // lot_size) * lot_size

            # 下午批次：总股数减上午(整手调整后)，再对齐
            remaining = total_shares - morning_shares
            afternoon_shares = (remaining // lot_size) * lot_size

            # 计算限价（预估价格上浮缓冲）
            limit_price = round(est_price * (1 + self.PRICE_BUFFER), 3)

            # 名称
            name = info.get("name", "")
            style = info.get("style", "")
            risk = info.get("risk", "")

            if morning_shares > 0:
                morning_orders.append(TradeOrder(
                    priority=idx,
                    code=code,
                    name=name,
                    session="morning",
                    shares=morning_shares,
                    est_price=est_price,
                    limit_price=limit_price,
                    est_amount=round(morning_shares * est_price, 2),
                    style=style,
                    risk=risk,
                    note=f"上午批次 09:30-10:30",
                ))

            if afternoon_shares > 0:
                afternoon_orders.append(TradeOrder(
                    priority=idx,
                    code=code,
                    name=name,
                    session="afternoon",
                    shares=afternoon_shares,
                    est_price=est_price,
                    limit_price=limit_price,
                    est_amount=round(afternoon_shares * est_price, 2),
                    style=style,
                    risk=risk,
                    note=f"下午批次 14:00-14:30",
                ))

        # 计算金额汇总
        morning_total = sum(o.est_amount for o in morning_orders)
        afternoon_total = sum(o.est_amount for o in afternoon_orders)
        day_total = morning_total + afternoon_total

        return DailyTradeSheet(
            trade_date=target_date.strftime("%Y-%m-%d"),
            phase_name=phase_summary["name"],
            phase_number=phase_summary["phase"],
            total_capital=self.plan_data["metadata"]["total_capital"],
            day_capital=round(day_total, 2),
            morning_orders=morning_orders,
            afternoon_orders=afternoon_orders,
            paused_orders=paused_orders,
            warnings=warnings,
        )

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
            lines.append("| 优先级 | 代码 | 名称 | 买入股数 | 预估单价 | 限价 | 预估金额 | 风格 |")
            lines.append("|:-------|:-----|:-----|--------:|--------:|------|--------:|:-----|")

            for o in sheet.morning_orders:
                lines.append(f"| {o.priority} | {o.code} | {o.name} | "
                             f"{o.shares:,} | {o.est_price:.3f} | {o.limit_price:.3f} | "
                             f"{o.est_amount:,.0f} | {o.style} |")

            morning_total = sum(o.est_amount for o in sheet.morning_orders)
            lines.append(f"| | | **上午合计** | | | | **{morning_total:,.0f}** | |")
            lines.append("")

        # 下午批次
        if sheet.afternoon_orders:
            lines.append("## 下午批次 (14:00 — 14:30)")
            lines.append("")
            lines.append("| 优先级 | 代码 | 名称 | 买入股数 | 预估单价 | 限价 | 预估金额 | 风格 |")
            lines.append("|:-------|:-----|:-----|--------:|--------:|------|--------:|:-----|")

            for o in sheet.afternoon_orders:
                lines.append(f"| {o.priority} | {o.code} | {o.name} | "
                             f"{o.shares:,} | {o.est_price:.3f} | {o.limit_price:.3f} | "
                             f"{o.est_amount:,.0f} | {o.style} |")

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
                lines.append(f"| {p['code']} | {p['name']} | {p['shares']:,} | "
                             f"{p['est_price']:.3f} | {cp} | {p['reason']} |")
            lines.append("")

        # 执行摘要
        total_orders = len(sheet.morning_orders) + len(sheet.afternoon_orders)
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
            "morning_orders": [
                {**asdict(o), "session": o.session}
                for o in sheet.morning_orders
            ],
            "afternoon_orders": [
                {**asdict(o), "session": o.session}
                for o in sheet.afternoon_orders
            ],
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
        for i in range(phase_idx):
            if i < len(self.plan_data["phase_summary"]):
                completed_capital += self.plan_data["phase_summary"][i]["capital_amount"]

        # 整体进度
        total_capital = self.plan_data["metadata"]["total_capital"]
        progress = min(completed_capital / total_capital, 1.0) if total_capital > 0 else 0

        status_info = {
            "date": today.strftime("%Y-%m-%d"),
            "status": status,
            "total_capital": total_capital,
            "completed_capital": completed_capital,
            "progress": round(progress * 100, 1),
            "current_phase": None,
            "target_count": self.plan_data["metadata"]["target_count"],
            "build_phases": self.plan_data["metadata"]["build_phases"],
        }

        if phase_summary:
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

    def save_trade_sheet(self, sheet: DailyTradeSheet,
                         output_dir: Optional[str] = None):
        """保存交易指令单到文件"""
        out_dir = output_dir or OUTPUT_DIR
        os.makedirs(out_dir, exist_ok=True)

        date_str = sheet.trade_date.replace("-", "")

        # Markdown 版本
        md_path = os.path.join(out_dir, f"trade_orders_{date_str}.md")
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write(self.format_trade_sheet_markdown(sheet))

        # JSON 版本
        json_path = os.path.join(out_dir, f"trade_orders_{date_str}.json")
        with open(json_path, 'w', encoding='utf-8') as f:
            f.write(self.format_trade_sheet_json(sheet))

        return md_path, json_path

    # ---------------------------------------------------------------
    # 极端情景应对协议 (v2.0 新增)
    # ---------------------------------------------------------------

    def get_emergency_protocol(self, market_state: Dict) -> Dict:
        """
        获取紧急响应协议建议

        基于市场状态返回具体的防御性操作清单

        Args:
            market_state: 市场状态字典，包含 vix_proxy, index_return_20d 等

        Returns:
            紧急协议字典，包含建议操作和优先级
        """
        vix = market_state.get("vix_proxy", 20)
        ret_20d = market_state.get("index_return_20d", 0)
        ret_5d = market_state.get("index_return_5d", 0)
        margin_chg = market_state.get("margin_balance_change", 0)
        mfg_dd = market_state.get("sector_health", {}).get(
            "high_end_manufacturing_20d", 0)

        protocol = {
            "level": 0,
            "level_name": "NORMAL",
            "day_capital_multiplier": 1.0,
            "actions": [],
            "hedge_suggestions": [],
        }

        # ---- 极端：最高优先级 ----
        # VIX>=50 或 5日跌幅>12% 或 20日跌幅>25% 或 两融降>15%
        if (vix >= 50 or abs(ret_5d) > 0.12
                or abs(ret_20d) > 0.25 or margin_chg < -0.15):
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
            protocol["hedge_suggestions"] = self._get_hedge_suggestions("extreme")

        # 红色：VIX>40 或 双周>15% 或 两融降>10%
        elif vix > 40 or abs(ret_20d) > 0.15 or margin_chg < -0.10:
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
            protocol["hedge_suggestions"] = self._get_hedge_suggestions("critical")

        # 橙色：VIX>35 或 单周>8%
        elif vix > 35 or abs(ret_5d) > 0.08:
            protocol["level"] = 2
            protocol["level_name"] = "HIGH"
            protocol["day_capital_multiplier"] = 0.0
            protocol["actions"] = [
                "1. 今日暂停建仓，等待市场稳定",
                "2. 密切监控已建仓位表现",
                "3. 如高端制造板块单周跌幅>5%，启动行业轮出",
                "4. 准备科创50虚值Put（行权价=当前价×0.90）",
            ]
            protocol["hedge_suggestions"] = self._get_hedge_suggestions("high")

        # 黄色：VIX>30 或 单日>3% 或 两融降>5%
        elif vix > 30 or abs(ret_5d / 5) > 0.03 or margin_chg < -0.05:
            protocol["level"] = 1
            protocol["level_name"] = "MEDIUM"
            protocol["day_capital_multiplier"] = 0.50
            protocol["actions"] = [
                "1. 建仓金额减半，优先执行核心仓位",
                "2. 暂停高风险标的（单日波动率>3%的标的）的建仓",
                "3. 增加现金储备至10-15%",
                "4. 关注高端制造板块止盈/止损触发条件",
            ]
            protocol["hedge_suggestions"] = self._get_hedge_suggestions("medium")

        # 行业集中度特殊检测
        if abs(mfg_dd) > 0.15 and protocol["level"] < 2:
            protocol["level"] = max(protocol["level"], 2)
            protocol["level_name"] = "HIGH"
            protocol["actions"].append(
                f"行业预警: 高端制造板块20日回撤{mfg_dd:.1%}，建议启动风格对冲")

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
        """
    )
    parser.add_argument("--date", "-d", type=str, default=None,
                        help="目标日期 YYYY-MM-DD (默认: 今日)")
    parser.add_argument("--json", action="store_true",
                        help="以 JSON 格式输出")
    parser.add_argument("--check-status", action="store_true",
                        help="查看建仓当前状态")
    parser.add_argument("--plan-file", type=str, default=None,
                        help="建仓计划 JSON 文件路径")

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

    print(f"\n文件已保存:", file=sys.stderr)
    print(f"  Markdown: {md_path}", file=sys.stderr)
    print(f"  JSON:     {json_path}", file=sys.stderr)
