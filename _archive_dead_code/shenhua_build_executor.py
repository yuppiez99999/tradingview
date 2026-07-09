#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
中国神华(601088) 建仓执行器
==========================

基于 `中国神华建仓计划_YYYYMMDD.json` 生成每日交易订单。

核心职责：
  1. 加载建仓计划 JSON
  2. 根据当前日期匹配建仓阶段
  3. 根据当前价格判断是否符合档位触发条件
  4. 生成 DailyTradeSheet (上午/下午订单 + 暂停订单)
  5. 保存 Markdown + JSON 报告

设计原则：
  - 与 BuildPlanExecutor 接口兼容（generate_daily_orders / save_trade_sheet / get_build_status）
  - 单标的特化逻辑：价格档位判断 + 股息率锚定
  - 非阻塞降级：JSON 缺失/数据异常时返回空订单
"""

import os
import json
import glob
import logging
from datetime import datetime, date, timedelta
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Tuple, Any


logger = logging.getLogger("ShenhuaBuildExecutor")

# 默认建仓计划文件名前缀
PLAN_FILE_PREFIX = "中国神华建仓计划_"

# 价格偏离阈值
PRICE_DISCOUNT_BUY = 0.02      # 价格低于中枢 2% 积极买入
PRICE_PREMIUM_SKIP = 0.03      # 价格高于中枢 3% 跳过
PRICE_DEVIATION_BLOCK = 0.08   # 价格偏离中枢 8% 以上阻断（异常行情）


# =============================================================
# 数据类定义
# =============================================================
@dataclass
class Order:
    """单笔订单"""
    code: str
    name: str
    direction: str           # buy / sell / skip
    shares: int
    price_ref: float         # 参考价（档位中枢）
    price_limit: Optional[float] = None  # 限价（None 表示市价）
    amount: float = 0.0
    tier: int = 0            # 触发的档位
    reason: str = ""


@dataclass
class DailyTradeSheet:
    """每日交易单"""
    trade_date: str
    code: str
    name: str
    morning_orders: List[Order] = field(default_factory=list)
    afternoon_orders: List[Order] = field(default_factory=list)
    paused_orders: List[Order] = field(default_factory=list)
    current_price: Optional[float] = None
    current_tier: Optional[int] = None
    capital_multiplier: float = 1.0
    notes: str = ""


# =============================================================
# 执行器主类
# =============================================================
class ShenhuaBuildExecutor:
    """中国神华建仓执行器"""

    def __init__(self, plan_path: Optional[str] = None):
        """
        初始化执行器

        参数:
            plan_path: 建仓计划 JSON 路径。
                      None 时自动查找最新的 中国神华建仓计划_*.json
        """
        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        self.plan_path = plan_path or self._find_latest_plan()
        self.plan: Dict[str, Any] = {}
        self._load_plan()

    # -----------------------------------------------------------
    # 计划加载
    # -----------------------------------------------------------
    def _find_latest_plan(self) -> str:
        """查找最新的建仓计划 JSON 文件"""
        pattern = os.path.join(self.base_dir, f"{PLAN_FILE_PREFIX}*.json")
        files = glob.glob(pattern)
        if not files:
            # 兼容英文文件名
            pattern2 = os.path.join(self.base_dir, "shenhua_build_plan_*.json")
            files = glob.glob(pattern2)
        if not files:
            return ""
        # 按修改时间倒序
        files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
        return files[0]

    def _load_plan(self) -> None:
        """加载建仓计划 JSON"""
        if not self.plan_path or not os.path.exists(self.plan_path):
            logger.warning(f"建仓计划文件不存在: {self.plan_path}")
            self.plan = {}
            return

        try:
            with open(self.plan_path, "r", encoding="utf-8") as f:
                self.plan = json.load(f)
            logger.info(f"已加载建仓计划: {self.plan_path}")
        except Exception as e:
            logger.error(f"加载建仓计划失败: {e}")
            self.plan = {}

    # -----------------------------------------------------------
    # 阶段匹配
    # -----------------------------------------------------------
    def get_active_phase(self, target_date: date) -> Dict[str, Any]:
        """
        根据日期匹配当前建仓阶段

        返回:
            {
                "status": "active" | "completed" | "not_started" | "during_gap",
                "phase": phase_dict | None,
                "tier": tier_dict | None
            }
        """
        if not self.plan or "phase_summary" not in self.plan:
            return {"status": "not_started", "phase": None, "tier": None}

        phases = self.plan["phase_summary"]
        if not phases:
            return {"status": "not_started", "phase": None, "tier": None}

        # 解析所有阶段日期
        for phase in phases:
            start = datetime.strptime(phase["start_date"], "%Y-%m-%d").date()
            end = datetime.strptime(phase["end_date"], "%Y-%m-%d").date()
            if start <= target_date <= end:
                tier = self._get_tier_by_ref(phase.get("tier_ref"))
                return {"status": "active", "phase": phase, "tier": tier}

        # 检查是否在所有阶段之前
        first_start = datetime.strptime(phases[0]["start_date"], "%Y-%m-%d").date()
        if target_date < first_start:
            return {"status": "not_started", "phase": None, "tier": None}

        # 检查是否在所有阶段之后
        last_end = datetime.strptime(phases[-1]["end_date"], "%Y-%m-%d").date()
        if target_date > last_end:
            return {"status": "completed", "phase": None, "tier": None}

        # 阶段间隙
        return {"status": "during_gap", "phase": None, "tier": None}

    def _get_tier_by_ref(self, tier_ref: int) -> Optional[Dict[str, Any]]:
        """根据 tier_ref 获取档位配置"""
        if not self.plan or "build_tiers" not in self.plan:
            return None
        for tier in self.plan["build_tiers"]:
            if tier.get("tier") == tier_ref:
                return tier
        return None

    # -----------------------------------------------------------
    # 订单生成
    # -----------------------------------------------------------
    def generate_daily_orders(
        self,
        target_date: date,
        current_price: Optional[float] = None,
        capital_multiplier: float = 1.0,
    ) -> DailyTradeSheet:
        """
        生成每日交易订单

        参数:
            target_date: 目标日期
            current_price: 当前股价（None 时使用估值快照中的当前价）
            capital_multiplier: 资金倍率（紧急状态下降低，0.5 = 半仓执行）

        返回:
            DailyTradeSheet 对象
        """
        sheet = DailyTradeSheet(
            trade_date=target_date.isoformat(),
            code=self.plan.get("metadata", {}).get("code", "601088"),
            name=self.plan.get("metadata", {}).get("name", "中国神华"),
            capital_multiplier=capital_multiplier,
        )

        if not self.plan:
            sheet.notes = "建仓计划未加载，无订单生成"
            return sheet

        # 获取估值快照中的当前价
        if current_price is None:
            snapshot = self.plan.get("valuation_snapshot", {})
            current_price = snapshot.get("current_price", 40.70)
        sheet.current_price = current_price

        # 阶段匹配
        phase_info = self.get_active_phase(target_date)
        sheet.current_tier = phase_info["tier"]["tier"] if phase_info.get("tier") else None

        if phase_info["status"] == "not_started":
            sheet.notes = "建仓计划尚未开始"
            return sheet
        if phase_info["status"] == "completed":
            sheet.notes = "建仓计划已全部完成"
            return sheet
        if phase_info["status"] == "during_gap":
            sheet.notes = "处于阶段间隙，无订单"
            return sheet

        phase = phase_info["phase"]
        tier = phase_info["tier"]
        if not phase or not tier:
            sheet.notes = "无法匹配阶段或档位"
            return sheet

        # 资金倍率为 0 时直接暂停
        if capital_multiplier <= 0:
            sheet.paused_orders.append(Order(
                code=sheet.code, name=sheet.name, direction="skip",
                shares=0, price_ref=tier["price_mid"],
                tier=tier["tier"],
                reason=f"资金倍率为0(紧急状态)，本档暂停",
            ))
            sheet.notes = "资金倍率为0，本日暂停建仓"
            return sheet

        # 价格档位判断
        price_low = tier["price_low"]
        price_high = tier["price_high"]
        price_mid = tier["price_mid"]

        # 价格异常波动检查
        deviation = abs(current_price - price_mid) / price_mid
        if deviation >= PRICE_DEVIATION_BLOCK:
            sheet.paused_orders.append(Order(
                code=sheet.code, name=sheet.name, direction="skip",
                shares=0, price_ref=price_mid, tier=tier["tier"],
                reason=f"价格异常波动(偏离中枢{deviation*100:.1f}%)，暂停建仓",
            ))
            sheet.notes = f"价格异常波动，本日暂停建仓"
            return sheet

        # 计算本日应执行的股数（按资金倍率调整）
        target_shares = int(phase["shares"] * capital_multiplier)
        min_lots = self.plan.get("execution_rules", {}).get("min_lots", 100)
        target_shares = max((target_shares // min_lots) * min_lots, min_lots)

        # 价格偏离判断
        if current_price < price_mid * (1 - PRICE_DISCOUNT_BUY):
            # 折价买入：积极执行（上午完成全部）
            order = Order(
                code=sheet.code, name=sheet.name, direction="buy",
                shares=target_shares, price_ref=price_mid,
                price_limit=price_high,  # 限价 = 档位上限
                amount=target_shares * current_price,
                tier=tier["tier"],
                reason=f"折价买入(现价{current_price:.2f} < 中枢{price_mid:.2f}的{PRICE_DISCOUNT_BUY*100:.0f}%下方)",
            )
            sheet.morning_orders.append(order)
            sheet.notes = f"折价触发，上午执行 {target_shares} 股"
        elif current_price > price_mid * (1 + PRICE_PREMIUM_SKIP):
            # 溢价跳过：本日不执行
            sheet.paused_orders.append(Order(
                code=sheet.code, name=sheet.name, direction="skip",
                shares=0, price_ref=price_mid, tier=tier["tier"],
                reason=f"溢价跳过(现价{current_price:.2f} > 中枢{price_mid:.2f}的{PRICE_PREMIUM_SKIP*100:.0f}%上方)",
            ))
            sheet.notes = f"价格偏高，本日跳过"
        elif price_low <= current_price <= price_high:
            # 正常区间：拆分上下午
            morning_shares = max((target_shares // 2 // min_lots) * min_lots, min_lots)
            afternoon_shares = max(target_shares - morning_shares, min_lots)
            sheet.morning_orders.append(Order(
                code=sheet.code, name=sheet.name, direction="buy",
                shares=morning_shares, price_ref=price_mid,
                price_limit=price_high,
                amount=morning_shares * current_price,
                tier=tier["tier"],
                reason=f"上午建仓(档位{tier['tier']}区间内)",
            ))
            sheet.afternoon_orders.append(Order(
                code=sheet.code, name=sheet.name, direction="buy",
                shares=afternoon_shares, price_ref=price_mid,
                price_limit=price_high,
                amount=afternoon_shares * current_price,
                tier=tier["tier"],
                reason=f"下午建仓(档位{tier['tier']}区间内)",
            ))
            sheet.notes = f"正常建仓: 上午{morning_shares}股 + 下午{afternoon_shares}股"
        else:
            # 不在档位区间内（但偏离未达阻断阈值）
            sheet.paused_orders.append(Order(
                code=sheet.code, name=sheet.name, direction="skip",
                shares=0, price_ref=price_mid, tier=tier["tier"],
                reason=f"价格{current_price:.2f}不在档位区间[{price_low}-{price_high}]内",
            ))
            sheet.notes = f"价格不在本档位区间，跳过"

        return sheet

    # -----------------------------------------------------------
    # 状态查询
    # -----------------------------------------------------------
    def get_build_status(self) -> Dict[str, Any]:
        """获取建仓整体进度"""
        if not self.plan:
            return {"status": "no_plan", "progress": 0.0}

        today = date.today()
        phase_info = self.get_active_phase(today)

        return {
            "status": phase_info["status"],
            "current_phase": phase_info["phase"],
            "current_tier": phase_info["tier"],
            "plan_path": self.plan_path,
            "total_capital": self.plan.get("metadata", {}).get("total_capital", 0),
            "total_shares": self.plan.get("metadata", {}).get("total_shares", 0),
            "avg_cost": self.plan.get("metadata", {}).get("avg_cost", 0),
            "start_date": self.plan.get("metadata", {}).get("start_date"),
            "end_date": self.plan.get("metadata", {}).get("end_date"),
        }

    # -----------------------------------------------------------
    # 报告输出
    # -----------------------------------------------------------
    def format_trade_sheet_markdown(self, sheet: DailyTradeSheet) -> str:
        """格式化交易单为 Markdown"""
        md = []
        md.append(f"# 中国神华({sheet.code}) 交易单 - {sheet.trade_date}\n\n")
        md.append(f"**当前股价**: {sheet.current_price:.2f} 元  \n")
        md.append(f"**当前档位**: 第{sheet.current_tier}档  \n")
        md.append(f"**资金倍率**: {sheet.capital_multiplier:.2f}  \n")
        md.append(f"**备注**: {sheet.notes}\n\n")

        if sheet.morning_orders:
            md.append("## 上午订单 (09:35-10:15)\n\n")
            md.append("| 方向 | 股数 | 参考价 | 限价 | 金额(元) | 档位 | 原因 |\n")
            md.append("|------|------|--------|------|---------|------|------|\n")
            for o in sheet.morning_orders:
                limit_str = f"{o.price_limit:.2f}" if o.price_limit else "市价"
                md.append(f"| {o.direction} | {o.shares} | {o.price_ref:.2f} | "
                          f"{limit_str} | {o.amount:,.0f} | T{o.tier} | {o.reason} |\n")
            md.append("\n")

        if sheet.afternoon_orders:
            md.append("## 下午订单 (14:00-14:30)\n\n")
            md.append("| 方向 | 股数 | 参考价 | 限价 | 金额(元) | 档位 | 原因 |\n")
            md.append("|------|------|--------|------|---------|------|------|\n")
            for o in sheet.afternoon_orders:
                limit_str = f"{o.price_limit:.2f}" if o.price_limit else "市价"
                md.append(f"| {o.direction} | {o.shares} | {o.price_ref:.2f} | "
                          f"{limit_str} | {o.amount:,.0f} | T{o.tier} | {o.reason} |\n")
            md.append("\n")

        if sheet.paused_orders:
            md.append("## 暂停订单\n\n")
            md.append("| 原因 |\n|------|\n")
            for o in sheet.paused_orders:
                md.append(f"| {o.reason} |\n")

        return "".join(md)

    def format_trade_sheet_json(self, sheet: DailyTradeSheet) -> Dict[str, Any]:
        """格式化交易单为 JSON 字典"""
        return {
            "trade_date": sheet.trade_date,
            "code": sheet.code,
            "name": sheet.name,
            "current_price": sheet.current_price,
            "current_tier": sheet.current_tier,
            "capital_multiplier": sheet.capital_multiplier,
            "notes": sheet.notes,
            "morning_orders": [asdict(o) for o in sheet.morning_orders],
            "afternoon_orders": [asdict(o) for o in sheet.afternoon_orders],
            "paused_orders": [asdict(o) for o in sheet.paused_orders],
            "generated_at": datetime.now().isoformat(),
        }

    def save_trade_sheet(
        self,
        sheet: DailyTradeSheet,
        output_dir: Optional[str] = None,
    ) -> Tuple[str, str]:
        """
        保存交易单到文件

        返回:
            (md_path, json_path)
        """
        if output_dir is None:
            output_dir = os.path.join(self.base_dir, "reports", sheet.trade_date)
        os.makedirs(output_dir, exist_ok=True)

        date_str = sheet.trade_date.replace("-", "")
        md_path = os.path.join(output_dir, f"shenhua_orders_{date_str}.md")
        json_path = os.path.join(output_dir, f"shenhua_orders_{date_str}.json")

        with open(md_path, "w", encoding="utf-8") as f:
            f.write(self.format_trade_sheet_markdown(sheet))
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(self.format_trade_sheet_json(sheet), f, ensure_ascii=False, indent=2)

        logger.info(f"交易单已保存: {md_path} / {json_path}")
        return md_path, json_path


# =============================================================
# CLI 入口
# =============================================================
def main():
    import argparse
    parser = argparse.ArgumentParser(description="中国神华建仓执行器")
    parser.add_argument("--date", type=str, default=None,
                        help="目标日期(YYYY-MM-DD)，默认今日")
    parser.add_argument("--price", type=float, default=None,
                        help="当前股价，默认使用计划中的快照价")
    parser.add_argument("--capital-multiplier", type=float, default=1.0,
                        help="资金倍率(0-1)，默认 1.0")
    parser.add_argument("--check-status", action="store_true",
                        help="仅查询建仓状态")
    parser.add_argument("--plan-path", type=str, default=None,
                        help="建仓计划 JSON 路径")
    args = parser.parse_args()

    # 初始化执行器
    executor = ShenhuaBuildExecutor(plan_path=args.plan_path)

    if args.check_status:
        status = executor.get_build_status()
        print(json.dumps(status, ensure_ascii=False, indent=2, default=str))
        return

    # 解析日期
    target_date = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else date.today()

    # 生成订单
    sheet = executor.generate_daily_orders(
        target_date=target_date,
        current_price=args.price,
        capital_multiplier=args.capital_multiplier,
    )

    # 保存报告
    md_path, json_path = executor.save_trade_sheet(sheet)

    # 控制台输出
    print("\n" + "=" * 60)
    print(f"中国神华交易单 - {sheet.trade_date}")
    print("=" * 60)
    print(f"当前股价: {sheet.current_price:.2f} 元")
    print(f"当前档位: T{sheet.current_tier}")
    print(f"资金倍率: {sheet.capital_multiplier:.2f}")
    print(f"备注: {sheet.notes}")
    print("-" * 60)
    if sheet.morning_orders:
        print("上午订单:")
        for o in sheet.morning_orders:
            print(f"  {o.direction} {o.shares}股 @ {o.price_ref:.2f}元 (限价{o.price_limit}) - {o.reason}")
    if sheet.afternoon_orders:
        print("下午订单:")
        for o in sheet.afternoon_orders:
            print(f"  {o.direction} {o.shares}股 @ {o.price_ref:.2f}元 (限价{o.price_limit}) - {o.reason}")
    if sheet.paused_orders:
        print("暂停订单:")
        for o in sheet.paused_orders:
            print(f"  SKIP - {o.reason}")
    print("-" * 60)
    print(f"Markdown: {md_path}")
    print(f"JSON:     {json_path}")


if __name__ == "__main__":
    main()
