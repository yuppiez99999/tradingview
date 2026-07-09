#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
 7月6日交易执行方案 — v7.0 对冲整合
 Trading Execution Plan for July 6, 2026 — v7.0 Hedge Integration
================================================================================

功能:
  1. 读取 500万建仓计划，生成 7月6日建仓交易指令
  2. 执行 v7.0 对冲方案 (期货 + 期权双层保护)
  3. 输出完整执行报告 (建仓明细 + 对冲方案 + 资金分配 + 检查清单)

用法:
  python trading_execution_v7_20260706.py                 # 完整执行报告
  python trading_execution_v7_20260706.py --date 2026-07-06  # 指定日期
  python trading_execution_v7_20260706.py --hedge-only     # 仅对冲方案
  python trading_execution_v7_20260706.py --build-only     # 仅建仓指令
"""

import os
import sys
import json
import math
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional

# 添加项目根目录到路径
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

# 导入建仓执行器
from build_plan_executor import BuildPlanExecutor, DailyTradeSheet

# 导入 v7.0 对冲系统
from comprehensive_quant_system_v7 import (
    ComprehensiveQuantSystemV7,
    ProtectiveOptionsHedge,
    EnhancedFuturesHedge,
    EnhancedCoveredWrite,
    MarketRegime,
    MarketRegimeDetector,
)


class TradingExecutionV7:
    """
    7月6日交易执行引擎 — 建仓 + v7.0对冲整合

    7月6日资金分配:
      总资金: 5,000,000 元
      ├── 首批建仓:     1,747,558 元 (35%)  → 权益多头
      ├── v7.0对冲预留:   700,000 元 (14%)  → 期货保证金 + 期权权利金
      └── 剩余现金:     2,552,442 元 (51%)  → 短融ETF (后续建仓+对冲)

    对冲启动策略 (建仓首日):
      - 期货对冲: 因权益仓位仅35%, 投入比例相应缩小
        → 对冲比率目标: 15-20% (后续随仓位增加逐步提升)
      - 期权保护: 启动 Put Spread Collar (零成本领口)
        → 建仓初期市场判断不明, 优先低成本保护
      - 备兑开仓: 建仓完成前不启动 (持仓未建立)
      - 波动率套利: 监控模式 (仅观察, 不交易)
    """

    # 7月6日资金配置
    TOTAL_CAPITAL = 5_000_000
    BUILD_CAPITAL = 1_747_558       # 首批建仓金额
    HEDGE_RESERVE = 700_000          # 对冲预留
    CASH_REMAINING = TOTAL_CAPITAL - BUILD_CAPITAL - HEDGE_RESERVE  # ~2,552,442

    def __init__(self, target_date: Optional[date] = None):
        self.target_date = target_date or date(2026, 7, 6)
        self.executor = BuildPlanExecutor()
        self.v7_system = ComprehensiveQuantSystemV7(total_capital=self.TOTAL_CAPITAL)

    def generate_build_orders(self) -> Dict:
        """生成建仓交易指令"""
        sheet = self.executor.generate_daily_orders(self.target_date)

        if not sheet.morning_orders and not sheet.afternoon_orders:
            phase_summary, _, status = self.executor.get_active_phase(self.target_date)
            return {
                'status': status,
                'phase': phase_summary,
                'warning': f'当前阶段状态: {status}',
                'orders': []
            }

        morning_orders = [
            {
                'priority': o.priority,
                'code': o.code,
                'name': o.name,
                'shares': o.shares,
                'est_price': o.est_price,
                'limit_price': o.limit_price,
                'est_amount': o.est_amount,
                'session': 'morning',
                'time': '09:30-10:30',
                'style': o.style,
            }
            for o in sheet.morning_orders
        ]

        afternoon_orders = [
            {
                'priority': o.priority,
                'code': o.code,
                'name': o.name,
                'shares': o.shares,
                'est_price': o.est_price,
                'limit_price': o.limit_price,
                'est_amount': o.est_amount,
                'session': 'afternoon',
                'time': '14:00-14:30',
                'style': o.style,
            }
            for o in sheet.afternoon_orders
        ]

        all_orders = morning_orders + afternoon_orders
        total_amount = sum(o['est_amount'] for o in all_orders)

        return {
            'status': 'active',
            'phase': {
                'name': sheet.phase_name,
                'number': sheet.phase_number,
            },
            'total_amount': total_amount,
            'morning_count': len(morning_orders),
            'afternoon_count': len(afternoon_orders),
            'orders': all_orders,
            'morning_orders': morning_orders,
            'afternoon_orders': afternoon_orders,
            'warnings': sheet.warnings,
        }

    def generate_hedge_plan(self, build_result: Dict) -> Dict:
        """
        生成 7月6日对冲方案

        建仓首日特殊考虑:
          - 权益仓位仅 35%, 对冲比例相应缩小
          - 优先低成本对冲 (Put Spread Collar)
          - 期货保证金预留充足
          - 备兑开仓暂不启动
        """
        build_amount = build_result.get('total_amount', self.BUILD_CAPITAL)

        # 建仓首日市场状态假设为震荡 (数据不足无法判断)
        market_regime = MarketRegime.SIDEWAYS

        # ---- Layer 1: 期货Delta对冲 (首日轻量) ----
        futures_hedge = EnhancedFuturesHedge(capital=self.HEDGE_RESERVE * 0.40)
        hedge_decision = futures_hedge.calculate_optimal_hedge(
            portfolio_beta=1.0,
            portfolio_value=build_amount,    # 仅对冲已建仓部分
            market_regime=market_regime,
            market_vol=0.20,
            momentum_signal=0.0
        )

        # 计算期货合约 (沪深300股指期货, 假设指数点位4000)
        idx_level = 4000
        contracts_value = hedge_decision['hedge_ratio'] * build_amount
        futures_contracts = max(0, int(contracts_value / (300 * idx_level)))
        futures_margin = futures_contracts * 300 * idx_level * 0.12

        # ---- Layer 2: 期权保护性对冲 (首日启动 Collar) ----
        options_hedge = ProtectiveOptionsHedge(capital=self.HEDGE_RESERVE * 0.35)
        collar_result = options_hedge.calculate_put_spread_collar(
            portfolio_value=build_amount,
            index_level=idx_level,
            volatility=0.20,
            days_to_expiry=45
        )

        # ---- Layer 5: 备兑开仓 — 暂不启动 ----
        # 建仓首日持仓未建立, 不卖出 Call

        # ---- 资金汇总 ----
        hedge_detail = {
            'market_regime': market_regime.value,
            'build_amount': build_amount,
            'hedge_reserve': self.HEDGE_RESERVE,
            'cash_remaining': self.CASH_REMAINING,
            'layer1_futures': {
                'strategy': '沪深300股指期货Delta对冲',
                'hedge_ratio': hedge_decision['hedge_ratio'],
                'target_beta': hedge_decision['target_beta'],
                'contracts': futures_contracts,
                'notional_short': contracts_value,
                'margin_required': futures_margin,
                'annual_cost_estimate': futures_hedge.estimate_annual_cost(),
                'note': '建仓初期轻量对冲, 随仓位增加逐步提高'
            },
            'layer2_options': {
                'strategy': '看跌价差领口 (Put Spread Collar)',
                'long_put_strike': collar_result['long_put']['strike'],
                'short_put_strike': collar_result['short_put']['strike'],
                'short_call_strike': collar_result['short_call']['strike'],
                'net_cost': collar_result['net_cost'],
                'contracts': collar_result['contracts'],
                'protection_zone': f"{collar_result['long_put']['strike']:.0f} - {collar_result['short_put']['strike']:.0f}",
                'note': '零成本领口, 保护5-15%温和下跌'
            },
            'layer3_volatility': {
                'strategy': '波动率套利',
                'status': 'MONITOR_ONLY',
                'note': '建仓初期仅监控IV/RV偏离, 不实际交易'
            },
            'layer4_absolute_return': {
                'strategy': '绝对收益/市场中性',
                'status': 'PENDING',
                'note': '建仓完成后再启动配对交易'
            },
            'layer5_covered_write': {
                'strategy': '备兑开仓',
                'status': 'NOT_STARTED',
                'note': '建仓完成且持仓稳定后再启动'
            },
        }

        # 成本汇总
        hedge_detail['cost_summary'] = {
            'futures_annual_cost': hedge_detail['layer1_futures']['annual_cost_estimate'],
            'options_one_time_cost': collar_result['net_cost'],
            'total_initial_hedge_cost': futures_margin + abs(collar_result['net_cost']),
            'remaining_hedge_reserve': self.HEDGE_RESERVE - futures_margin - abs(collar_result['net_cost']),
        }

        return hedge_detail

    def generate_capital_flow(self) -> Dict:
        """生成 7月6日资金流向"""
        return {
            'date': '2026-07-06',
            'total_capital': self.TOTAL_CAPITAL,
            'flows': [
                {
                    'account': '建仓资金',
                    'amount': self.BUILD_CAPITAL,
                    'pct': self.BUILD_CAPITAL / self.TOTAL_CAPITAL,
                    'usage': '13只标的首批建仓 (35%), 详见建仓指令单',
                    'execution': '09:30-14:30 分上下半场执行'
                },
                {
                    'account': '对冲保证金',
                    'amount': 336_000,
                    'pct': 0.067,
                    'usage': '期货保证金 (~200万名义空头, 12%保证金率)',
                    'execution': '建仓完成后同步建立期货空头'
                },
                {
                    'account': '期权权利金',
                    'amount': 50_000,
                    'pct': 0.01,
                    'usage': 'Put Spread Collar 净权利金 (可能接近零成本)',
                    'execution': '与建仓同步'
                },
                {
                    'account': '剩余对冲备用',
                    'amount': 314_000,
                    'pct': 0.063,
                    'usage': '预留后续阶段对冲资金 (波动率套利/绝对收益/增加期货)',
                    'execution': '存放于短融ETF(511360)'
                },
                {
                    'account': '未建仓资金',
                    'amount': self.CASH_REMAINING,
                    'pct': self.CASH_REMAINING / self.TOTAL_CAPITAL,
                    'usage': '后续三阶段建仓资金 + 现金管理',
                    'execution': '全部转入短融ETF(511360), 获取约2%年化'
                },
            ]
        }

    def generate_checklist(self) -> List[str]:
        """生成 7月6日执行检查清单"""
        return [
            # 盘前准备 (07-06 09:00前)
            '确认券商账户资金 500万元已到账',
            '确认科创板/创业板/商品ETF交易权限正常',
            '确认期货账户已开通 (沪深300股指期货)',
            '确认期权账户已开通 (沪深300ETF期权/50ETF期权)',
            '确认期货保证金充足 (预留 35万+)',
            '09:15 查看股指期货开盘, 确认基差水平',
            '09:20 查看集合竞价, 确认各标的开盘参考价',
            '09:25 记录集合竞价产生的开盘价',

            # 上午执行 (09:30-10:30)
            '09:30-09:45 优先买入科创50ETF(588000) 和 半导体ETF(512480)',
            '09:45-10:00 买入高端装备ETF(516160) 和 新能源车ETF(515030)',
            '10:00-10:15 买入创业板ETF(159915) 和 创新药ETF(159992)',
            '10:15-10:30 买入医药ETF(512010)、有色ETF(512400)、中国神华(601088)',
            '10:30 确认上午批次成交, 记录实际成交价',

            # 对冲建立 (10:30-11:30)
            '10:30-11:00 计算建仓后组合Beta',
            '11:00-11:15 建立股指期货空头头寸 (对冲比率 ~15%)',
            '11:15-11:30 买入 Put Spread Collar 组合 (95%/85% Put, 110% Call卖)',

            # 午间休息
            '11:30-13:00 午间休市, 复核上午成交与对冲',

            # 下午执行 (13:00-15:00)
            '13:00-14:00 观察市场走势, 必要时微调期货对冲比率',
            '14:00-14:30 执行下午批次建仓 (剩余50%)',
            '14:30-14:50 最终确认对冲头寸, 记录总持仓',
            '14:50-15:00 剩余资金买入短融ETF(511360)',

            # 盘后确认 (15:00后)
            '15:00 后确认全天成交, 导出持仓清单',
            '15:30 生成首日执行报告, 记录实际成本与对冲状态',
            '17:00 复核各账户资金, 确保无异常',
            '保存所有交易记录到 reports/ 目录',

            # v7.0 对冲状态复核
            '确认期货空头名义金额与权益仓位匹配',
            '确认期权领口组合完整 (Long Put + Short Put + Short Call)',
            '记录对冲成本基线, 后续跟踪对冲效率',
        ]

    def run_full_report(self) -> str:
        """生成完整执行报告"""
        lines = []
        dt_str = self.target_date.strftime("%Y-%m-%d")

        lines.append(f"# 交易执行方案 — {dt_str} (v7.0 对冲整合)")
        lines.append("")
        lines.append(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"**系统版本**: v7.0 (期货+期权双层对冲)")
        lines.append(f"**执行日期**: {dt_str} (建仓首日)")
        lines.append(f"**总资金**: {self.TOTAL_CAPITAL:,} 元")
        lines.append("")

        # ================================================================
        # 一、建仓执行指令
        # ================================================================
        lines.append("---")
        lines.append("")
        lines.append("## 一、建仓执行指令 (首批 35%)")
        lines.append("")

        build_result = self.generate_build_orders()

        if build_result['status'] != 'active':
            lines.append(f"**状态**: {build_result['status']}")
            lines.append(f"**说明**: {build_result.get('warning', '')}")
        else:
            lines.append(f"**阶段**: {build_result['phase']['name']}")
            lines.append(f"**计划金额**: {build_result['total_amount']:,.0f} 元")
            lines.append(f"**标的数量**: 13 只")
            lines.append("")

            # 上午批次
            if build_result['morning_orders']:
                lines.append("### 上午批次 (09:30 — 10:30)")
                lines.append("")
                lines.append("| 优先级 | 代码 | 名称 | 买入股数 | 限价 | 预估金额 | 风格 |")
                lines.append("|:-------|:-----|:-----|--------:|------:|--------:|:-----|")
                for o in build_result['morning_orders']:
                    lines.append(f"| {o['priority']} | {o['code']} | {o['name']} | "
                                 f"{o['shares']:,} | {o['limit_price']:.3f} | "
                                 f"{o['est_amount']:,.0f} | {o['style']} |")
                morning_sum = sum(o['est_amount'] for o in build_result['morning_orders'])
                lines.append(f"| | | **上午合计** | | | **{morning_sum:,.0f}** | |")
                lines.append("")

            # 下午批次
            if build_result['afternoon_orders']:
                lines.append("### 下午批次 (14:00 — 14:30)")
                lines.append("")
                lines.append("| 优先级 | 代码 | 名称 | 买入股数 | 限价 | 预估金额 | 风格 |")
                lines.append("|:-------|:-----|:-----|--------:|------:|--------:|:-----|")
                for o in build_result['afternoon_orders']:
                    lines.append(f"| {o['priority']} | {o['code']} | {o['name']} | "
                                 f"{o['shares']:,} | {o['limit_price']:.3f} | "
                                 f"{o['est_amount']:,.0f} | {o['style']} |")
                afternoon_sum = sum(o['est_amount'] for o in build_result['afternoon_orders'])
                lines.append(f"| | | **下午合计** | | | **{afternoon_sum:,.0f}** | |")
                lines.append("")

        # ================================================================
        # 二、v7.0 对冲方案
        # ================================================================
        lines.append("---")
        lines.append("")
        lines.append("## 二、v7.0 对冲方案")
        lines.append("")

        hedge_result = self.generate_hedge_plan(build_result)

        lines.append(f"**市场状态**: {hedge_result['market_regime']} (建仓首日默认震荡)")
        lines.append(f"**对冲预留资金**: {hedge_result['hedge_reserve']:,} 元")
        lines.append("")

        # Layer 1
        l1 = hedge_result['layer1_futures']
        lines.append("### Layer 1: 股指期货Delta对冲")
        lines.append("")
        lines.append(f"- 对冲比率: {l1['hedge_ratio']:.1%}")
        lines.append(f"- 目标Beta: {l1['target_beta']:.2f}")
        lines.append(f"- 期货合约: {l1['contracts']} 手 (沪深300)")
        lines.append(f"- 名义空头: {l1['notional_short']:,.0f} 元")
        lines.append(f"- 保证金需求: {l1['margin_required']:,.0f} 元")
        lines.append(f"- 说明: {l1['note']}")
        lines.append("")

        # Layer 2
        l2 = hedge_result['layer2_options']
        lines.append("### Layer 2: 期权保护性对冲 (Put Spread Collar)")
        lines.append("")
        lines.append(f"- 结构: Long {l2['long_put_strike']:.0f} Put + Short {l2['short_put_strike']:.0f} Put + Short {l2['short_call_strike']:.0f} Call")
        lines.append(f"- 净成本: {l2['net_cost']:,.0f} 元")
        lines.append(f"- 合约数: {l2['contracts']} 张")
        lines.append(f"- 保护区: {l2['protection_zone']} (5-15%下跌保护)")
        lines.append(f"- 说明: {l2['note']}")
        lines.append("")

        # Layers 3-5
        lines.append("### Layer 3-5: 后续启动")
        lines.append("")
        for layer in ['layer3_volatility', 'layer4_absolute_return', 'layer5_covered_write']:
            l = hedge_result[layer]
            lines.append(f"- **{l['strategy']}**: {l['status']} — {l['note']}")
        lines.append("")

        # 成本汇总
        cs = hedge_result['cost_summary']
        lines.append("### 对冲成本汇总")
        lines.append("")
        lines.append(f"- 期货保证金占用: {cs['futures_annual_cost']:,.0f} 元/年")
        lines.append(f"- 期权一次性成本: {cs['options_one_time_cost']:,.0f} 元")
        lines.append(f"- 初始对冲总占用: {cs['total_initial_hedge_cost']:,.0f} 元")
        lines.append(f"- 对冲剩余备用: {cs['remaining_hedge_reserve']:,.0f} 元")
        lines.append("")

        # ================================================================
        # 三、资金流向
        # ================================================================
        lines.append("---")
        lines.append("")
        lines.append("## 三、资金流向")
        lines.append("")

        capital_flow = self.generate_capital_flow()
        lines.append("| 用途 | 金额 | 占比 | 说明 |")
        lines.append("|:-----|------:|-----:|:-----|")
        for flow in capital_flow['flows']:
            lines.append(f"| {flow['account']} | {flow['amount']:,.0f} | {flow['pct']:.1%} | {flow['usage']} |")
        lines.append(f"| **合计** | **{sum(f['amount'] for f in capital_flow['flows']):,.0f}** | **100%** | |")
        lines.append("")

        # ================================================================
        # 四、风险管理
        # ================================================================
        lines.append("---")
        lines.append("")
        lines.append("## 四、风险管理 (7月6日适用)")
        lines.append("")

        lines.append("### 止损规则")
        lines.append("")
        lines.append("| 风险等级 | 止损线 | 适用标的 |")
        lines.append("|:---------|:------|:---------|")
        lines.append("| 高风险 | -15% | 科创50、半导体、高端装备、新能源车、创业板、创新药、有色金属ETF |")
        lines.append("| 中风险 | -12% | 医药ETF、黄金ETF、中国神华 |")
        lines.append("| 低风险 | -5% | 国债、政金债、短融ETF |")
        lines.append("")

        lines.append("### 组合层面风控")
        lines.append("")
        lines.append("| 级别 | 回撤阈值 | 措施 |")
        lines.append("|:-----|:--------|:-----|")
        lines.append("| 黄色 | 6% | 关注, 不操作 |")
        lines.append("| 橙色 | 8% | 减仓至70%, 增持短融 |")
        lines.append("| 红色 | 12% | 清仓高风险标的, 仅保留黄金+债券 |")
        lines.append("")

        lines.append("### 对冲风控")
        lines.append("")
        lines.append("- 期货对冲比率: 初始15%, 上限70%")
        lines.append("- 期权净成本: 不超过权益市值的2%/年")
        lines.append("- 单日期货保证金占用: 不超过总资金8%")
        lines.append("")

        # ================================================================
        # 五、执行检查清单
        # ================================================================
        lines.append("---")
        lines.append("")
        lines.append("## 五、执行检查清单")
        lines.append("")

        checklist = self.generate_checklist()
        for item in checklist:
            lines.append(f"- [ ] {item}")
        lines.append("")

        # ================================================================
        # 六、后续计划
        # ================================================================
        lines.append("---")
        lines.append("")
        lines.append("## 六、后续阶段对冲升级计划")
        lines.append("")

        lines.append("| 阶段 | 时间 | 建仓进度 | 对冲升级 |")
        lines.append("|:-----|:-----|:---------|:---------|")
        lines.append("| 第一阶段 | 07-06 → 07-17 | 35% | 期货15% + Put Spread Collar (初始) |")
        lines.append("| 第二阶段 | 07-20 → 08-07 | 65% | 期货提至30% + 增加 Put Ladder 轻量 |")
        lines.append("| 第三阶段 | 08-10 → 08-28 | 85% | 期货提至40% + 启动波动率套利 + 绝对收益 |")
        lines.append("| 第四阶段 | 09-01 → 09-26 | 100% | 期货45% + 备兑开仓启动 + 完整五层对冲 |")
        lines.append("")

        lines.append("---")
        lines.append(f"*报告生成: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | 系统版本: v7.0*")

        return "\n".join(lines)

    def save_report(self, report: str) -> str:
        """保存执行报告"""
        report_dir = os.path.join(BASE_DIR, "reports")
        os.makedirs(report_dir, exist_ok=True)

        dt_str = self.target_date.strftime("%Y%m%d")
        path = os.path.join(report_dir, f"execution_plan_v7_{dt_str}.md")

        with open(path, 'w', encoding='utf-8') as f:
            f.write(report)

        return path


# ================================================================
# CLI 入口
# ================================================================
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="7月6日交易执行方案 — v7.0 对冲整合",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  python trading_execution_v7_20260706.py                    # 完整执行报告
  python trading_execution_v7_20260706.py --date 2026-07-06  # 指定日期
  python trading_execution_v7_20260706.py --hedge-only       # 仅对冲方案
  python trading_execution_v7_20260706.py --build-only       # 仅建仓指令
        """
    )
    parser.add_argument("--date", "-d", type=str, default="2026-07-06",
                        help="执行日期 (默认: 2026-07-06)")
    parser.add_argument("--hedge-only", action="store_true",
                        help="仅输出 v7.0 对冲方案")
    parser.add_argument("--build-only", action="store_true",
                        help="仅输出建仓指令")
    parser.add_argument("--output", "-o", type=str, default=None,
                        help="输出文件路径")

    args = parser.parse_args()

    target_date = datetime.strptime(args.date, "%Y-%m-%d").date()
    engine = TradingExecutionV7(target_date)

    if args.hedge_only:
        # 仅对冲方案
        build_result = engine.generate_build_orders()
        hedge = engine.generate_hedge_plan(build_result)
        print(json.dumps(hedge, ensure_ascii=False, indent=2))

    elif args.build_only:
        # 仅建仓指令
        build_result = engine.generate_build_orders()
        executor = BuildPlanExecutor()
        sheet = executor.generate_daily_orders(target_date)
        print(executor.format_trade_sheet_markdown(sheet))

        # 同时保存
        md_path, json_path = executor.save_trade_sheet(sheet)
        print(f"\n文件已保存:", file=sys.stderr)
        print(f"  Markdown: {md_path}", file=sys.stderr)
        print(f"  JSON:     {json_path}", file=sys.stderr)

    else:
        # 完整报告
        report = engine.run_full_report()
        print(report)

        # 保存
        path = engine.save_report(report)
        print(f"\n执行报告已保存: {path}", file=sys.stderr)

        # 同时保存建仓指令单
        executor = BuildPlanExecutor()
        sheet = executor.generate_daily_orders(target_date)
        md_path, json_path = executor.save_trade_sheet(sheet)
        print(f"建仓指令单: {md_path}", file=sys.stderr)
        print(f"建仓指令JSON: {json_path}", file=sys.stderr)
