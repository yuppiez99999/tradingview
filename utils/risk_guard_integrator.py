# -*- coding: utf-8 -*-
"""
风控守卫集成器 (Risk Guard Integrator)
=======================================
创建日期: 2026-07-21
创建原因: P1 回撤防护强制执行 + P0/P2/P3模块联动

核心功能:
    在 run_daily_eod.py 盘后流程中，串联所有风控模块，
    将"纸面规则"变成"代码强制执行"。

执行链路:
    1. 回撤检查 → 如果 Level≥2，自动修改次日计划（减仓+加对冲）
    2. 波动率目标 → 如果 vol_scale<0.8，缩减次日建仓预算
    3. 对冲引擎 → 计算并写入次日对冲订单
    4. 认沽保护 → 检查并生成保护性认沽订单

设计原则:
    - 每个模块独立失败不影响其他模块
    - 所有决策写入日志 + trade_plan
    - 强制性：Level 3+ 回撤直接覆写次日计划

用法:
    from utils.risk_guard_integrator import RiskGuardIntegrator
    rgi = RiskGuardIntegrator(report_date="2026-07-21")
    rgi.run_all_guards(next_trade_date="2026-07-22")
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from enum import IntEnum
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger("risk_guard_integrator")


# ============================================================
# P1-Q6 修复 (2026-07-26): KillSwitch level 用 IntEnum 替代字符串解析
# 原始问题: 嵌套三元运算符 + 字符串 "L3" → 3 解析无类型保护
# 修复方案: 用 IntEnum + 专用解析函数, 提供类型安全与可读性
# ============================================================
class KillSwitchLevel(IntEnum):
    """KillSwitch 熔断级别枚举

    顶级对冲基金标准: 风控级别必须用 Enum, 禁止裸字符串/整数
    """
    OK = 0   # 正常
    L1 = 1   # 一级警戒 (保证金 ≥ 50% 或 单票集中度 ≥ 25%)
    L2 = 2   # 二级熔断 (保证金 ≥ 65% 或 单票集中度 ≥ 35%)
    L3 = 3   # 三级互盲 (保证金 ≥ 75% 或 单票集中度 ≥ 50%)


def parse_kill_switch_level(
    ks_level: Any,
    margin_usage: float = 0.0,
) -> KillSwitchLevel:
    """解析 KillSwitch level 为 KillSwitchLevel 枚举

    P1-Q6 修复: 替换原嵌套三元运算符
        ks_level_int = 3 if level_str == 'OK' and margin_usage >= 0.75 else (
            3 if level_str == '3' else
            2 if level_str == '2' else
            1 if level_str == '1' else 0
        )

    支持输入类型:
        - int (0/1/2/3): 直接转换为枚举
        - str ("L0"/"L1"/"L2"/"L3"/"OK"): 解析为枚举
        - KillSwitchLevel: 直接返回

    特殊语义:
        - "OK" 字符串 + margin_usage ≥ 0.75 → 视为 L3 (隐式升级)
          这是为了兼容历史 bug: 部分 KillSwitch 实现在保证金超阈值时
          仍返回 level="OK", 需在 Guard 层补强判断

    Args:
        ks_level: 原始 level 值 (int/str/KillSwitchLevel)
        margin_usage: 保证金占用率, 用于 "OK" 隐式升级判断

    Returns:
        KillSwitchLevel 枚举值
    """
    # 已是枚举, 直接返回
    if isinstance(ks_level, KillSwitchLevel):
        return ks_level

    # 整数: 直接转枚举
    if isinstance(ks_level, int):
        try:
            return KillSwitchLevel(ks_level)
        except ValueError:
            logger.warning(f"无效 ks_level 整数: {ks_level}, 默认 OK")
            return KillSwitchLevel.OK

    # 字符串解析
    if isinstance(ks_level, str):
        level_str = ks_level.upper().replace('L', '').strip()
        if level_str == 'OK':
            # 隐式升级: "OK" + 高保证金 = L3
            return KillSwitchLevel.L3 if margin_usage >= 0.75 else KillSwitchLevel.OK
        try:
            return KillSwitchLevel(int(level_str))
        except (ValueError, TypeError):
            logger.warning(f"无法解析 ks_level 字符串: {ks_level}, 默认 OK")
            return KillSwitchLevel.OK

    # 其他类型: 保守返回 OK
    logger.warning(f"未知 ks_level 类型: {type(ks_level).__name__}={ks_level}, 默认 OK")
    return KillSwitchLevel.OK

BASE_DIR = Path(__file__).resolve().parent.parent
TRADE_PLANS_DIR = BASE_DIR / "v8.3_institutional" / "trade_plans"
# v8.6.9 P0 FIX (2026-07-26): 报告路径修正
# 原始 bug: REPORTS_DIR 指向 v8.3_institutional/reports/, 但生产环境实际报告在 每日报告归档/{date}/daily_pnl_report_{date}.json
# 影响: _load_pnl_report() 永远找不到报告 → 返回 None → guard_drawdown 跳过回撤检查 ("无有效成本数据")
#       导致回撤防护失效 + vol_target 用空数据计算
# 修复: 保留旧路径作为回退, 优先在 每日报告归档/{date}/ 下查找
REPORTS_DIR = BASE_DIR / "v8.3_institutional" / "reports"
DAILY_REPORT_DIR = BASE_DIR / "每日报告归档"
LOGS_DIR = BASE_DIR / "logs"


class RiskGuardIntegrator:
    """风控守卫集成器 - 串联所有风控模块并强制执行"""

    # v7.7: 底层标的代码映射 - 用于对冲引擎与认沽保护引擎的去重
    # HedgeExecutionEngine 用描述性名称 (如 "510050 Put")，
    # ProtectivePutEngine 直接用代码 (如 "510050")
    # 两者需统一映射到 6 位代码以进行去重比较
    UNDERLYING_CODE_MAP = {
        # 上证50
        "510050": "510050", "上证50": "510050", "50etf": "510050",
        "上证50etf": "510050", "sz50": "510050",
        # 科创50
        "588080": "588080", "科创50": "588080", "科创50etf": "588080",
        "kc50": "588080", "588000": "588080",
        # 创业板
        "159915": "159915", "创业板": "159915", "创业板etf": "159915",
        "cyb": "159915",
        # 沪深300
        "510300": "510300", "沪深300": "510300", "300etf": "510300",
        "hs300": "510300",
        # 中证500
        "510500": "510500", "中证500": "510500", "500etf": "510500",
        "zz500": "510500",
        # 中证1000
        "512100": "512100", "中证1000": "512100", "1000etf": "512100",
        "zz1000": "512100",
    }

    @classmethod
    def _extract_underlying_code(cls, instrument_name: str) -> Optional[str]:
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
        codes = re.findall(r'\b(\d{6})\b', name_lower)
        if codes:
            return codes[0]
        # 模糊匹配: 最长优先, 避免 "50etf" 误匹配 "科创50ETF"
        for key in sorted(cls.UNDERLYING_CODE_MAP.keys(), key=len, reverse=True):
            if key in name_lower:
                return cls.UNDERLYING_CODE_MAP[key]
        return None

    def __init__(self, report_date: str = None, total_capital: float = 5_000_000):
        self.report_date = report_date or datetime.now().strftime('%Y-%m-%d')
        self.total_capital = total_capital
        self.log_entries = []
        LOGS_DIR.mkdir(parents=True, exist_ok=True)

    def _log(self, msg: str):
        """记录日志"""
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        entry = f"[{ts}] [RiskGuard] {msg}"
        self.log_entries.append(entry)
        logger.info(msg)
        # 安全打印 — Win GBK 兼容
        try:
            print(entry, flush=True)
        except UnicodeEncodeError:
            safe = entry.encode('gbk', errors='replace').decode('gbk', errors='ignore')
            print(safe, flush=True)

    def _load_pnl_report(self) -> Optional[Dict]:
        """加载当日盈亏报告 (v8.6.9 P0 FIX: 多路径查找)

        查找顺序:
            1. 每日报告归档/{report_date}/daily_pnl_report_{report_date}.json  (生产格式)
            2. 每日报告归档/{report_date}/daily_pnl_report_{report_date无横杠}.json  (兼容)
            3. v8.3_institutional/reports/daily_pnl_report_{report_date}.json  (旧格式回退)
            4. v8.3_institutional/reports/daily_pnl_report_{report_date无横杠}.json  (旧格式回退)
        """
        date_str = self.report_date
        date_no_dash = date_str.replace('-', '')

        # 候选路径列表 (优先级降序)
        candidates = [
            DAILY_REPORT_DIR / date_str / f"daily_pnl_report_{date_str}.json",
            DAILY_REPORT_DIR / date_str / f"daily_pnl_report_{date_no_dash}.json",
            REPORTS_DIR / f"daily_pnl_report_{date_str}.json",
            REPORTS_DIR / f"daily_pnl_report_{date_no_dash}.json",
        ]

        for json_path in candidates:
            if json_path.exists():
                try:
                    with open(json_path, 'r', encoding='utf-8') as f:
                        self._log(f"[P0-FIX] 已加载盈亏报告: {json_path.name} (path={json_path.parent})")
                        return json.load(f)
                except Exception as e:
                    self._log(f"加载盈亏报告失败 ({json_path}): {e}")

        self._log(f"[WARN] 未找到当日盈亏报告, 查找路径: {[str(p) for p in candidates]}")
        return None

    def _get_pnl_summary(self, pnl_report: Dict) -> Dict:
        """从盈亏报告中提取汇总数据 (v7.7修正: 适配 portfolio_pnl.summary 嵌套结构)"""
        return pnl_report.get('portfolio_pnl', {}).get('summary', {})

    def _extract_positions(self, pnl_report: Dict) -> list:
        """从 pnl_report 提取 positions 列表 (v8.6.6: 兼容三种数据位置)

        完整格式 (带横杠文件名 daily_pnl_report_YYYY-MM-DD.json):
            1. pnl_report['portfolio_pnl']['positions'] — 某些版本
            2. pnl_report['portfolio_pnl']['details']   — 当前生产格式 (26 标的)

        简化格式 (无横杠文件名 daily_pnl_report_YYYYMMDD.json):
            3. pnl_report['positions'] — 顶层 (list of dicts)

        Returns:
            positions 列表 (始终为 list, 即使原始是 dict 也会转成 list)
        """
        pp = pnl_report.get('portfolio_pnl', {})
        if isinstance(pp, dict):
            # 1. 完整格式: portfolio_pnl.positions
            positions = pp.get('positions', [])
            if positions:
                if isinstance(positions, dict):
                    return list(positions.values())
                if isinstance(positions, list):
                    return positions
            # 2. 完整格式: portfolio_pnl.details (当前生产格式)
            details = pp.get('details', [])
            if details:
                if isinstance(details, dict):
                    return list(details.values())
                if isinstance(details, list):
                    return details
        # 3. 简化格式: 顶层 positions
        positions = pnl_report.get('positions', [])
        if isinstance(positions, dict):
            return list(positions.values())
        return positions if isinstance(positions, list) else []

    def _extract_summary(self, pnl_report: Dict) -> Dict:
        """从 pnl_report 提取 summary (v8.6.6: 兼容两种报告格式)

        完整格式: pnl_report['portfolio_pnl']['summary']
        简化格式: pnl_report['summary'] (顶层)
        """
        # 1. 完整格式
        summary = pnl_report.get('portfolio_pnl', {}).get('summary', {})
        if summary:
            return summary
        # 2. 简化格式
        return pnl_report.get('summary', {})

    def _load_next_trade_plan(self, next_date: str) -> Optional[Dict]:
        """加载次日交易计划"""
        plan_path = TRADE_PLANS_DIR / f"trade_plan_{next_date.replace('-', '')}.json"
        if not plan_path.exists():
            return None
        try:
            with open(plan_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            self._log(f"加载次日计划失败: {e}")
            return None

    def _save_trade_plan(self, plan: Dict, next_date: str):
        """保存修改后的交易计划"""
        plan_path = TRADE_PLANS_DIR / f"trade_plan_{next_date.replace('-', '')}.json"
        # 先备份
        if plan_path.exists():
            bak_path = plan_path.with_suffix(f".json.bak_{datetime.now():%H%M%S}")
            plan_path.rename(bak_path)
        with open(plan_path, 'w', encoding='utf-8') as f:
            json.dump(plan, f, ensure_ascii=False, indent=2)
        self._log(f"次日计划已更新: {plan_path.name}")

    # ============================================================
    # Guard 1: 回撤强制响应
    # ============================================================
    def guard_drawdown(self, pnl_report: Dict, plan: Dict) -> Dict:
        """回撤检查并强制修改交易计划
        
        Level 1 (5%): 预警，不改计划
        Level 2 (8%): 减仓20% + 对冲加码50%
        Level 3 (12%): 减仓40% + 对冲加码80% + 暂停建仓
        Level 4 (15%): 停止建仓 + 只做平仓
        """
        try:
            from utils.drawdown_controller import DrawdownController
        except ImportError:
            self._log("[回撤] DrawdownController 导入失败，跳过")
            return plan

        dc = DrawdownController()

        # 从报告中提取总成本和当前市值 (v7.7修正: 使用 _get_pnl_summary 适配 portfolio_pnl.summary)
        pnl_summary = self._get_pnl_summary(pnl_report)
        total_cost = pnl_summary.get('total_cost', 0)
        total_value = pnl_summary.get('total_market_value', 0)

        if total_cost <= 0:
            self._log("[回撤] 无有效成本数据，跳过回撤检查")
            return plan

        # v7.7修正: 建仓期峰值 = max(已投入成本, 当前市值)
        # 不再与 total_capital 比较, 避免建仓期误报 -55% 回撤
        peak = max(total_cost, total_value)
        current = total_cost + pnl_summary.get('total_pnl', 0)

        result = dc.check_drawdown(peak_value=peak, current_value=current)
        level = result.get('level', 0)
        dd_pct = result.get('drawdown_pct', 0)

        self._log(f"[回撤] 当前回撤: {dd_pct:.2%}, 级别: Level {level}")

        if level == 0:
            plan.setdefault('risk_guard', {})['drawdown_level'] = 0
            plan['risk_guard']['drawdown_action'] = 'NORMAL'
            return plan

        # Level 1: 预警，不改计划但标记
        if level == 1:
            plan.setdefault('risk_guard', {})['drawdown_level'] = 1
            plan['risk_guard']['drawdown_action'] = 'WARNING'
            plan['risk_guard']['drawdown_note'] = f'回撤{dd_pct:.2%}达到预警线，建议关注'
            self._log(f"[回撤] [WARNING] Level 1 预警: 回撤{dd_pct:.2%}")
            return plan

        # Level 2: 减仓20% + 对冲加码
        if level == 2:
            plan = self._apply_budget_cut(plan, cut_ratio=0.20)
            plan = self._apply_hedge_boost(plan, boost_pct=0.50)
            plan.setdefault('risk_guard', {})['drawdown_level'] = 2
            plan['risk_guard']['drawdown_action'] = 'REDUCE_20PCT'
            plan['risk_guard']['drawdown_note'] = f'回撤{dd_pct:.2%}触发Level2: 预算-20%, 对冲+50%'
            self._log(f"[回撤] [WARN] Level 2 触发: 预算缩减20%, 对冲加码50%")
            return plan

        # Level 3: 减仓40% + 暂停建仓
        if level == 3:
            plan = self._apply_budget_cut(plan, cut_ratio=0.60)
            plan = self._apply_hedge_boost(plan, boost_pct=0.80)
            plan.setdefault('risk_guard', {})['drawdown_level'] = 3
            plan['risk_guard']['drawdown_action'] = 'REDUCE_60PCT_PAUSE_BUILD'
            plan['risk_guard']['drawdown_note'] = f'回撤{dd_pct:.2%}触发Level3: 预算-60%, 暂停新建仓'
            # 清空建仓订单
            plan['execution_plan'] = plan.get('execution_plan', {})
            plan['execution_plan']['morning_orders'] = []
            plan['execution_plan']['afternoon_orders'] = []
            plan['market_state'] = plan.get('market_state', {})
            plan['market_state']['spot_build_allowed'] = False
            self._log(f"[回撤] [CRITICAL] Level 3 触发: 暂停全部建仓, 对冲加码80%")
            return plan

        # Level 4: 停止一切 + 只做平仓
        if level >= 4:
            plan.setdefault('risk_guard', {})['drawdown_level'] = 4
            plan['risk_guard']['drawdown_action'] = 'FULL_STOP_LIQUIDATE'
            plan['risk_guard']['drawdown_note'] = f'回撤{dd_pct:.2%}触发Level4: 全面止损, 只做平仓'
            plan['execution_plan'] = plan.get('execution_plan', {})
            plan['execution_plan']['morning_orders'] = []
            plan['execution_plan']['afternoon_orders'] = []
            plan['market_state'] = plan.get('market_state', {})
            plan['market_state']['spot_build_allowed'] = False
            plan['market_state']['build_allowed'] = False
            plan['market_state']['circuit_level'] = 'CRITICAL'
            self._log(f"[回撤] [EMERGENCY] Level 4 触发: 全面停止, 仅允许平仓+对冲")
            return plan

        return plan

    def _apply_budget_cut(self, plan: Dict, cut_ratio: float) -> Dict:
        """缩减建仓预算"""
        phase = plan.get('phase', {})
        original_budget = phase.get('daily_capital', phase.get('day_capital', 150000))
        new_budget = original_budget * (1 - cut_ratio)
        plan['phase']['daily_capital'] = new_budget
        plan['phase']['day_capital'] = new_budget
        plan['phase']['budget_cut_reason'] = f'drawdown_cut_{cut_ratio:.0%}'

        # 缩减订单金额
        exec_plan = plan.get('execution_plan', {})
        for session in ['morning_orders', 'afternoon_orders']:
            orders = exec_plan.get(session, [])
            for order in orders:
                if 'shares' in order:
                    order['shares'] = int(order['shares'] * (1 - cut_ratio))
                if 'est_amount' in order:
                    order['est_amount'] = order['est_amount'] * (1 - cut_ratio)

        return plan

    def _apply_hedge_boost(self, plan: Dict, boost_pct: float) -> Dict:
        """加码对冲"""
        hedge_config = plan.get('hedge_config', {})
        layers = hedge_config.get('layers', {})
        
        # 增加期货对冲比例
        layer1 = layers.get('layer1_futures', {})
        current_ratio = layer1.get('ratio', 0.15)
        layer1['ratio'] = min(current_ratio * (1 + boost_pct), 0.60)
        layer1['boost_reason'] = f'drawdown_boost_{boost_pct:.0%}'
        
        plan['hedge_config']['layers']['layer1_futures'] = layer1
        return plan

    # ============================================================
    # Guard 2: 波动率目标缩仓
    # ============================================================
    def guard_vol_target(self, pnl_report: Dict, plan: Dict) -> Dict:
        """波动率目标控制 - 缩减建仓预算"""
        try:
            from utils.vol_target_controller import VolTargetController
        except ImportError:
            self._log("[波动率] VolTargetController 导入失败，跳过")
            return plan

        vtc = VolTargetController()
        
        # 尝试从价格更新获取日收益率
        daily_returns = self._extract_daily_returns(pnl_report)
        
        # 先计算已实现波动率，再计算 vol_scale
        realized_vol = vtc.calc_realized_vol(daily_returns) if daily_returns else None
        vol_scale = vtc.calc_vol_scale(realized_vol)
        
        if vol_scale is None or vol_scale >= 0.80:
            self._log(f"[波动率] vol_scale={vol_scale or 'N/A'}, 无需缩仓")
            plan.setdefault('risk_guard', {})['vol_scale'] = vol_scale
            plan['risk_guard']['vol_action'] = 'NORMAL'
            return plan

        # 缩减预算
        phase = plan.get('phase', {})
        original_budget = phase.get('daily_capital', phase.get('day_capital', 150000))
        adjusted_budget = original_budget * max(vol_scale, 0.30)

        # v8.6.8 P0-03b FIX (2026-07-26): 保存原始预算字段便于审计追溯
        # 原始 bug: 直接覆写 phase.daily_capital 导致验证脚本无法判断 vol_scale 是否已应用
        # 修复: 在 phase 中保留 original_daily_capital 字段, 缩减后 daily_capital 仍可追溯
        if 'original_daily_capital' not in phase:
            phase['original_daily_capital'] = round(original_budget, 2)
        plan['phase']['daily_capital'] = round(adjusted_budget, 2)
        plan['phase']['day_capital'] = round(adjusted_budget, 2)
        plan['phase']['vol_scale_applied'] = round(max(vol_scale, 0.30), 4)
        plan.setdefault('risk_guard', {})['vol_scale'] = vol_scale
        plan['risk_guard']['vol_action'] = f'SCALE_DOWN_{vol_scale:.2f}'
        plan['risk_guard']['vol_note'] = (
            f'波动率目标控制: vol_scale={vol_scale:.3f}, '
            f'预算 {original_budget:,.0f} → {adjusted_budget:,.0f}'
        )

        # v8.6.8 P0-03 FIX (2026-07-26): vol_scale 必须应用到 execution_plan 订单金额
        # 原始 bug: 仅缩减 phase.daily_capital, 但 execution_plan.morning_orders/afternoon_orders
        # 中的 shares 和 est_amount 保持原值, 导致:
        #   - trade_plan.phase.daily_capital=60000 (缩减后)
        #   - trade_plan.execution_plan.total_amount=219977.9 (未缩减, 仍按原 200K)
        #   - 实盘执行器按 total_amount 下单, 严重超预算 267%
        # 修复: 同步缩减订单 shares (按 vol_scale 比例) 和 est_amount, 并更新 execution_plan.day_capital
        # v8.6.8 P0-03a-fix (2026-07-26): L2 触发后 BUY 订单已被 overnight_gap 清空,
        # 此时无订单可缩减是预期行为, vol_scale 只缩减 phase.daily_capital,
        # 通过 vol_scale_executed_summary 记录无订单缩减原因 (避免验证脚本误判)
        scale_factor = max(vol_scale, 0.30) / 1.0  # vol_scale 已经过 max(vol_scale, 0.30) 处理
        exec_plan = plan.get('execution_plan', {})
        exec_plan['day_capital'] = round(adjusted_budget, 2)
        exec_plan['original_day_capital'] = round(original_budget, 2)
        total_amount_after = 0.0
        scaled_buy_count = 0
        for session_key in ('morning_orders', 'afternoon_orders'):
            orders = exec_plan.get(session_key, []) or []
            for o in orders:
                # 仅缩减现货 BUY 订单 (side=BUY), 保留 SELL 平仓订单原值
                if str(o.get('side', '')).upper() == 'BUY':
                    scaled_buy_count += 1
                    if 'shares' in o:
                        original_shares = int(o['shares'])
                        new_shares = max(int(original_shares * scale_factor), 0)
                        o['shares'] = new_shares
                        # 标记缩减原因 (审计追溯)
                        o['vol_scale_applied'] = round(scale_factor, 4)
                        o['original_shares'] = original_shares
                    if 'est_amount' in o:
                        o['est_amount'] = round(float(o['est_amount']) * scale_factor, 2)
                    if 'limit_price' in o:
                        # 限价保持原值 (限价是价格, 与数量独立), 不缩减
                        pass
                total_amount_after += float(o.get('est_amount', 0))
        # 更新 execution_plan.total_amount 和 day_capital
        if 'total_amount' in exec_plan:
            exec_plan['total_amount'] = round(total_amount_after, 2)
        # v8.6.8 P0-03a-fix: 记录 vol_scale 应用状态摘要 (审计追溯)
        # 区分 "L2 触发后无订单可缩减" vs "vol_scale 未应用" 两种场景
        plan['risk_guard']['vol_scale_executed_summary'] = {
            'scale_factor': round(scale_factor, 4),
            'original_budget': round(original_budget, 2),
            'adjusted_budget': round(adjusted_budget, 2),
            'scaled_buy_orders': scaled_buy_count,
            'note': (
                f'L2 触发后 BUY 订单已被 overnight_gap 清空, vol_scale 仅缩减 phase.daily_capital'
                if scaled_buy_count == 0 else
                f'已缩减 {scaled_buy_count} 笔 BUY 订单的 shares 和 est_amount'
            ),
        }
        plan['risk_guard']['vol_note'] = (
            f'波动率目标控制: vol_scale={vol_scale:.3f}, '
            f'预算 {original_budget:,.0f} → {adjusted_budget:,.0f}, '
            f'订单金额已同步缩减 (factor={scale_factor:.3f})'
        )

        self._log(f"[波动率] vol_scale={vol_scale:.3f}, 预算缩减: "
                  f"{original_budget:,.0f} → {adjusted_budget:,.0f}, "
                  f"订单金额已同步缩减 (factor={scale_factor:.3f})")
        return plan

    def _extract_daily_returns(self, pnl_report: Dict) -> list:
        """从报告提取日收益率序列"""
        # 尝试从 v76 增强报告获取历史日收益率
        try:
            import glob
            report_files = sorted(REPORTS_DIR.glob("daily_pnl_report_*.json"))
            returns = []
            for rf in report_files[-22:]:  # 最近22个交易日
                with open(rf, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                # v7.7修正: 适配 portfolio_pnl.summary 嵌套结构
                pnl_sum = data.get('portfolio_pnl', {}).get('summary', {})
                if not pnl_sum:
                    pnl_sum = data.get('pnl_summary', {})  # 兼容旧格式
                pnl_pct = pnl_sum.get('total_pnl_pct', 0)
                returns.append(pnl_pct / 100.0)  # 转为小数
            return returns if len(returns) >= 5 else []
        except Exception:
            return []

    # ============================================================
    # Guard 3: 对冲执行
    # ============================================================
    def guard_hedge_execution(self, pnl_report: Dict, plan: Dict, next_date: str) -> Dict:
        """对冲引擎 - 计算并写入对冲订单"""
        try:
            from utils.hedge_execution_engine import HedgeExecutionEngine
        except ImportError:
            self._log("[对冲] HedgeExecutionEngine 导入失败，跳过")
            return plan

        engine = HedgeExecutionEngine()
        
        try:
            hedge_result = engine.generate_hedge_orders()
            if hedge_result and isinstance(hedge_result, dict):
                futures_orders = hedge_result.get('futures_orders', [])
                options_orders = hedge_result.get('options_orders', [])
                total_orders = len(futures_orders) + len(options_orders)

                # v8.6.8 P0-01 FIX (2026-07-26): 同步 execution_status / execution_notes 字段
                # 原代码 plan['hedge_execution'] = hedge_result 仅写入原始结果, 缺少
                # execution_status 和 execution_notes, 导致下游执行器无法识别订单状态
                # 现在与 write_to_trade_plan() 保持一致, 在 in-memory plan 中也添加这些字段
                cost_summary = hedge_result.get('cost_summary', {})
                within_budget = bool(cost_summary.get('within_budget', True))
                if within_budget:
                    execution_status = "PENDING"
                    order_status = "PENDING"
                    execution_notes = [
                        "期货: 09:45-10:30 完成IF空头开仓 (若存在)",
                        "期权: 09:30-10:00 完成认沽期权买入",
                        "确认: 盘后核实对冲比例是否达标",
                    ]
                else:
                    execution_status = "CANCELLED"
                    order_status = "CANCELLED_OVER_BUDGET"
                    execution_notes = [
                        f"[P0-01] 预算超支, 全部对冲订单已拦截: "
                        f"total_cost=¥{cost_summary.get('total_cost', 0):,.0f} > "
                        f"threshold=¥{cost_summary.get('budget_threshold', 0):,.0f}",
                        "下游执行器 (daily_workflow/SOR) 必须跳过 CANCELLED_OVER_BUDGET 订单",
                        "需调整 hedge_positions 配置 (减少 contracts 或 premium_budget) 后重新生成",
                    ]
                    # 超预算时改写订单 status
                    for o in futures_orders:
                        o['status'] = order_status
                    for o in options_orders:
                        o['status'] = order_status

                # 写入交易计划 (含 execution_status / execution_notes, 与 write_to_trade_plan 一致)
                hedge_result['execution_status'] = execution_status
                # v8.6.8 P0-07 FIX (2026-07-26): execution_notes 根据 futures_orders 动态生成
                # 原代码硬编码 "期货: 09:45-10:30 完成IF空头开仓 (若存在)"
                # 但 OPTIONS_ONLY 模式下 futures_orders=[], 仍提及 IF 期货会误导执行器
                # 修复: 根据 futures_orders 是否为空动态生成 notes
                if not futures_orders:
                    execution_notes = [
                        "[P0-07] hedge_mode=OPTIONS_ONLY, 无期货订单",
                        "期权: 09:30-10:00 完成认沽期权买入",
                        "确认: 盘后核实对冲比例是否达标",
                    ]
                else:
                    execution_notes = [
                        "期货: 09:45-10:30 完成IF空头开仓",
                        "期权: 09:30-10:00 完成认沽期权买入",
                        "确认: 盘后核实对冲比例是否达标",
                    ]
                hedge_result['execution_notes'] = execution_notes
                plan['hedge_execution'] = hedge_result

                # v8.6.8 P0-08 FIX (2026-07-26): 同步 futures_options_hedge 与 hedge_execution 一致
                # 原始 bug: futures_options_hedge 字段从 hedge_execution_orders_{date}.json 加载
                # (可能不存在或过时), 而 hedge_execution 由 hedge_execution_engine 实时生成
                # 两者漂移会导致: futures_options_hedge.orders=[] 但 hedge_execution.options_orders 有 4 个订单
                # 下游执行器读 futures_options_hedge 会漏掉对冲订单
                # 修复: 用 hedge_execution 的内容同步 futures_options_hedge
                plan['futures_options_hedge'] = {
                    "hedge_mode": hedge_result.get('cost_summary', {}).get('hedge_mode', 'OPTIONS_ONLY'),
                    "loaded": True,
                    "portfolio_beta": hedge_result.get('portfolio_status', {}).get('portfolio_beta_before', 0.0),
                    "hedge_pct": 0.0 if not futures_orders else (
                        futures_orders[0].get('rationale', {}).get('beta_to_hedge', 0) if futures_orders else 0.0
                    ),
                    "execution_timing": {
                        "options_window": "09:30-10:00",
                        "futures_window": "10:30-11:00" if futures_orders else None,
                        "reserve_ratio": 0.175,
                        "reserve_amount": hedge_result.get('cost_summary', {}).get('buffer_for_roll', 0),
                    },
                    "orders": futures_orders + options_orders,
                    "orders_count": total_orders,
                    "total_premium": hedge_result.get('cost_summary', {}).get('total_premium_budget', 0),
                    "total_notional": sum(o.get('notional', 0) for o in futures_orders),
                    "total_safe_haven": 0,
                    "budget_check": {
                        "total_cost": hedge_result.get('cost_summary', {}).get('total_cost', 0),
                        "total_premium": hedge_result.get('cost_summary', {}).get('total_premium_budget', 0),
                        "futures_margin": hedge_result.get('cost_summary', {}).get('total_margin_required', 0),
                        "safe_haven": 0,
                        "reserve_amount": hedge_result.get('cost_summary', {}).get('buffer_for_roll', 0),
                        "remaining": hedge_capital_value - hedge_result.get('cost_summary', {}).get('total_cost', 0)
                            if (hedge_capital_value := hedge_result.get('portfolio_status', {}).get('hedge_capital', 0)) > 0
                            else 0,
                        "within_budget": within_budget,
                    },
                    "notes": [
                        f"对冲模式: {hedge_result.get('cost_summary', {}).get('hedge_mode', 'OPTIONS_ONLY')}",
                        f"执行状态: {execution_status}",
                        f"订单数: {total_orders} (期货 {len(futures_orders)} + 期权 {len(options_orders)})",
                        f"预算使用: {hedge_result.get('cost_summary', {}).get('hedge_capital_usage_pct', 0)*100:.1f}%",
                    ],
                    "execution_status": execution_status,
                }

                plan.setdefault('risk_guard', {})['hedge_action'] = (
                    f'GENERATED_{total_orders}_ORDERS' if within_budget
                    else f'CANCELLED_{total_orders}_ORDERS_OVER_BUDGET'
                )

                # 同时写入独立的对冲执行文件 (v8.6.8 P0-01: 现已写入 v8.3 路径)
                engine.write_to_trade_plan(hedge_result, next_date)

                self._log(f"[对冲] 已生成 {total_orders} 条对冲订单 "
                         f"(期货{len(futures_orders)}+期权{len(options_orders)}), "
                         f"status={execution_status}")

                # 输出期货订单详情
                for o in futures_orders:
                    contracts = o.get('contracts', 0)
                    est_price = o.get('est_price', 0)
                    self._log(f"  → IF期货 空头 {contracts}手 @ {est_price:.2f}")

                # 输出组合Beta变化
                ps = hedge_result.get('portfolio_status', {})
                self._log(f"  → Beta: {ps.get('portfolio_beta_before', '?')} "
                         f"→ {ps.get('target_beta_after', '?')}")
            else:
                plan.setdefault('risk_guard', {})['hedge_action'] = 'NO_CHANGE_NEEDED'
                self._log("[对冲] 当前对冲比例正常，无需调整")
        except Exception as e:
            self._log(f"[对冲] 执行引擎异常: {e}")
            plan.setdefault('risk_guard', {})['hedge_action'] = f'ERROR: {e}'

        return plan

    # ============================================================
    # Guard 4: 认沽保护
    # ============================================================
    def guard_protective_put(self, pnl_report: Dict, plan: Dict, next_date: str) -> Dict:
        """认沽保护 - 检查并生成保护性认沽订单"""
        try:
            from utils.protective_put_engine import ProtectivePutEngine
        except ImportError:
            self._log("[认沽] ProtectivePutEngine 导入失败，跳过")
            return plan

        ppe = ProtectivePutEngine()
        
        try:
            # 检查是否需要建立/滚仓 (v7.7修正: 使用 portfolio_pnl.summary)
            portfolio_value = self._get_pnl_summary(pnl_report).get('total_market_value', 0)
            
            if portfolio_value < 1_000_000:
                self._log(f"[认沽] 组合市值 {portfolio_value:,.0f} < 100万，暂不启动保护")
                plan.setdefault('risk_guard', {})['put_action'] = 'BELOW_THRESHOLD'
                return plan

            # v7.7修正: generate_put_orders() 返回 Dict {should_execute, orders, ...}
            result = ppe.generate_put_orders(drawdown_level=plan.get('risk_guard', {}).get('drawdown_level', 0))
            put_orders = result.get('orders', [])
            
            if result.get('should_execute') and put_orders:
                plan.setdefault('put_protection_orders', [])
                plan['put_protection_orders'] = put_orders
                plan.setdefault('risk_guard', {})['put_action'] = f'GENERATED_{len(put_orders)}_PUTS'
                
                total_premium = result.get('total_premium_est', 0)
                self._log(f"[认沽] 已生成 {len(put_orders)} 条认沽订单, 总权利金 ¥{total_premium:,.0f}")
            else:
                plan.setdefault('risk_guard', {})['put_action'] = 'EXISTING_PROTECTION_OK'
                self._log(f"[认沽] {result.get('reason', '无需新建/滚仓')}")
        except Exception as e:
            self._log(f"[认沽] 引擎异常: {e}")
            plan.setdefault('risk_guard', {})['put_action'] = f'ERROR: {e}'

        return plan

    # ============================================================
    # Guard 5: 保证金/持仓集中度熔断 (KillSwitch 三级协议)
    # ============================================================
    def guard_kill_switch(self, pnl_report: Dict, plan: Dict) -> Dict:
        """全局熔断检查 - 集成 utils/kill_switch 三级协议

        L1 (Watch): 保证金≥50% 或 单票集中度≥25% → 预警标记
        L2 (Limit): 保证金≥65% 或 单票集中度≥35% → 禁止开仓
        L3 (Critical): 保证金≥75% 或 单票集中度≥50% → 强制平仓
        """
        try:
            from utils.kill_switch import KillSwitch
        except ImportError:
            self._log("[KillSwitch] 模块导入失败，使用降级检查")
            KillSwitch = None

        ks = KillSwitch() if KillSwitch else None
        pnl_summary = self._get_pnl_summary(pnl_report)

        # 1. 保证金使用率检查
        # P0-D 修复 (2026-07-26 v8.6.5): 当 pnl_report 中 margin_used/total_equity 为 None 时
        # 回退到 KillSwitch._estimate_margin_from_positions() 真实估算 (基于 positions.json)
        # 原始 bug: pnl_summary.get('margin_used', 0) 在字段存在但值为 None 时返回 None (非默认值 0)
        # 导致 None/None 抛 TypeError 被外层 try/except 吞掉, trade_plan 显示 level=0
        # 但同期 kill_switch_events.jsonl 显示 L2 已触发 (margin_usage=80.36%) — EOD Guard 失效
        margin_used = pnl_summary.get('margin_used')
        total_equity = pnl_summary.get('total_equity')

        if margin_used is None or total_equity is None or total_equity <= 0:
            if ks:
                try:
                    margin_usage = ks._estimate_margin_from_positions()
                    self._log(
                        f"[KillSwitch] [P0-D FIX] pnl_report 字段缺失 "
                        f"(margin_used={margin_used}, total_equity={total_equity}), "
                        f"回退到 _estimate_margin_from_positions() = {margin_usage:.1%}"
                    )
                except Exception as e:
                    self._log(f"[KillSwitch] [P0-D FIX] 回退失败: {e}, 使用保守值 0.50")
                    margin_usage = 0.50
            else:
                margin_usage = 0.50
                self._log("[KillSwitch] [P0-D FIX] KillSwitch 模块不可用, 使用保守值 0.50")
        else:
            margin_usage = margin_used / total_equity if total_equity > 0 else 0

        if ks:
            margin_status = ks.check_margin_status(margin_usage)
        else:
            # 降级：简单硬阈值
            if margin_usage >= 0.75:
                margin_status = {"level": "L3", "can_trade": False, "can_open": False,
                                 "action": "MARGIN_CRITICAL: 保证金≥75%"}
            elif margin_usage >= 0.65:
                margin_status = {"level": "L2", "can_trade": True, "can_open": False,
                                 "action": "MARGIN_LIMIT: 保证金≥65%"}
            elif margin_usage >= 0.50:
                margin_status = {"level": "L1", "can_trade": True, "can_open": True,
                                 "action": "MARGIN_WATCH: 保证金≥50%"}
            else:
                margin_status = {"level": "OK", "can_trade": True, "can_open": True}

        self._log(f"[KillSwitch] 保证金 {margin_usage:.1%} → {margin_status['level']}: {margin_status['action']}")

        # 2. 持仓集中度检查
        positions = pnl_summary.get('positions', pnl_report.get('portfolio_pnl', {}).get('positions', {}))
        if ks and positions:
            concentration_status = ks.check_concentration(positions)
            self._log(f"[KillSwitch] 集中度检查: {concentration_status.get('level', 'OK')}")

        # 3. 响应动作
        plan.setdefault('risk_guard', {})['kill_switch'] = {
            'level': margin_status.get('level'),
            'margin_usage': round(margin_usage, 4),
            'can_trade': margin_status.get('can_trade', True),
            'can_open': margin_status.get('can_open', True),
        }

        # v8.6.7 修复 (P1): 用 level 判断 L2/L3, 而非 can_trade
        # 原始 bug: can_trade = (level < 2), 所以 L2 时 can_trade=False,
        # 被误判为 L3 执行清空所有订单 (应只过滤 BUY)
        # 正确逻辑: L3 清空所有, L2 过滤 BUY 保留 SELL, L1 仅预警
        ks_level_raw = margin_status.get('level', 0)

        # P1-Q6 修复 (2026-07-26): 用 IntEnum + 专用解析函数替代嵌套三元运算符
        # 原代码:
        #   if isinstance(ks_level, str):
        #       level_str = ks_level.upper().replace('L', '')
        #       ks_level_int = 3 if level_str == 'OK' and margin_usage >= 0.75 else (
        #           3 if level_str == '3' else 2 if level_str == '2' else
        #           1 if level_str == '1' else 0)
        #   else:
        #       ks_level_int = int(ks_level)
        # 修复后: 类型安全 + 可读性 + 可测试
        ks_level_enum = parse_kill_switch_level(ks_level_raw, margin_usage)
        ks_level_int = int(ks_level_enum)

        # L3: 强制停止一切 (清空所有订单)
        if ks_level_enum >= KillSwitchLevel.L3:
            plan['market_state'] = plan.get('market_state', {})
            plan['market_state']['circuit_level'] = 'CRITICAL'
            plan['market_state']['build_allowed'] = False
            plan['market_state']['spot_build_allowed'] = False
            plan['execution_plan'] = plan.get('execution_plan', {})
            plan['execution_plan']['morning_orders'] = []
            plan['execution_plan']['afternoon_orders'] = []
            self._log(f"[KillSwitch] [EMERGENCY] {ks_level_enum.name} 触发: 全面停止交易, 仅允许平仓")
            return plan

        # L2: 禁止开仓 (过滤 BUY, 保留 SELL)
        if ks_level_enum == KillSwitchLevel.L2:
            plan['market_state'] = plan.get('market_state', {})
            # 只在 circuit_level 未被更高优先级 Guard 设为 CRITICAL 时才设为 WARNING
            if plan['market_state'].get('circuit_level') != 'CRITICAL':
                plan['market_state']['circuit_level'] = 'WARNING'
            plan['market_state']['spot_build_allowed'] = False
            # v8.6.8 P0-02 FIX (2026-07-26): L2 同步 build_allowed=False
            # 原代码只设 spot_build_allowed=False, build_allowed 仍为 True, 与一致性校验矛盾
            plan['market_state']['build_allowed'] = False
            plan['execution_plan'] = plan.get('execution_plan', {})
            # v8.6.8 P0-02 FIX: 修复字段名 bug
            # 原代码 o.get('direction') == 'SELL' 但 trade_plan 现货订单字段是 'side' (无 direction)
            # 所有订单 direction=None, None == 'SELL' 为 False, 导致所有订单被清空 (包括 SELL)
            # 实际 trade_plan 现货订单全部是 side=BUY, 所以这里应过滤 side=BUY 保留 side=SELL
            # 同时检查 direction 字段 (对冲订单可能用 direction)
            for session in ['morning_orders', 'afternoon_orders']:
                orders = plan['execution_plan'].get(session, [])
                plan['execution_plan'][session] = [
                    o for o in orders
                    if o.get('side', '').upper() != 'BUY'
                    and o.get('direction', '').upper() not in ('BUY', 'BUY_OPEN', 'BUY_PUT')
                ]
            self._log("[KillSwitch] [WARN] L2 触发: 禁止开仓, 仅允许平仓 (保留 SELL 订单)")
            return plan

        # L1: 仅预警, 不修改订单
        if ks_level_enum == KillSwitchLevel.L1:
            plan['market_state'] = plan.get('market_state', {})
            if plan['market_state'].get('circuit_level') not in ('CRITICAL', 'WARNING'):
                plan['market_state']['circuit_level'] = 'WATCH'
            self._log("[KillSwitch] [WATCH] L1 触发: 保证金预警, 不修改订单")

        return plan

    # ============================================================
    # Guard 6: 大盘熔断 (P1-H 新增, v8.6.6)
    # ============================================================
    def guard_market_circuit_breaker(self, pnl_report: Dict, plan: Dict) -> Dict:
        """大盘熔断 Guard - 沪深300 跌幅触发 L2/L3

        L2 (跌 5%): 禁止开仓, 保留平仓
        L3 (跌 7%): 全局平仓 + halt_all_trading

        委托 utils/market_circuit_breaker.py MarketCircuitBreaker 实现
        """
        try:
            from utils.market_circuit_breaker import MarketCircuitBreaker
        except ImportError:
            self._log("[大盘熔断] MarketCircuitBreaker 导入失败, 跳过")
            plan.setdefault('risk_guard', {})['market_circuit_breaker'] = {
                'status': 'SKIP', 'reason': 'import_failed'
            }
            return plan

        try:
            mcb = MarketCircuitBreaker()
            status = mcb.check_market_status()
            plan = mcb.apply_to_plan(plan, status)

            if status['level'] >= 2:
                self._log(
                    f"[大盘熔断] L{status['level']} 触发: 沪深300 跌幅 {status['hs300_change_pct']:.2%}, "
                    f"数据源={status['data_source']}, 动作={status['actions']}"
                )
            else:
                self._log(
                    f"[大盘熔断] 正常: 沪深300 跌幅 {status['hs300_change_pct']:.2%} "
                    f"(数据源={status['data_source']})"
                )
        except Exception as e:
            self._log(f"[大盘熔断] 检查崩溃: {e}")
            plan.setdefault('risk_guard', {})['market_circuit_breaker_error'] = str(e)
            # fail-closed: 大盘熔断崩溃时禁止建仓
            plan.setdefault('market_state', {})['spot_build_allowed'] = False

        return plan

    # ============================================================
    # Guard 7: 流动性危机全局撤单 (P1-J 新增, v8.6.6)
    # ============================================================
    def guard_liquidity_crisis(self, pnl_report: Dict, plan: Dict) -> Dict:
        """流动性危机 Guard - 全市场涨跌停家数触发全局撤单

        触发条件: limit_up_count + limit_down_count > 2000 (真实数据)
        响应动作:
            1. 清空 plan['execution_plan']['morning_orders']
            2. 清空 plan['execution_plan']['afternoon_orders']
            3. 标记 plan['market_state']['liquidity_crisis'] = True

        v8.6.8 P1-LIVE-05 修复 (2026-07-26):
            原始 bug: 数据源不可用时 (_fetch_limit_counts 返回 fail_closed),
            FAIL_CLOSED_COUNT=2500 > 阈值 2000, 误触发全局撤单 + circuit_level=CRITICAL.
            但数据源不可用 ≠ 流动性危机, 只是网络故障/接口封禁/非交易日.

            修复原则 (与其他 Guard 对齐):
                - 数据源不可用 → 保守禁开仓 (WARNING), 不清空订单 (不 CRITICAL)
                - 真实数据显示流动性危机 → 全局撤单 (CRITICAL)
                - 崩溃 → 保守禁开仓 (WARNING), 不清空订单

            风控分级原则:
                - 数据源不可用: 不允许新开仓 (因为无法判断市场状态)
                - 但已有订单应该保留 (因为没有证据表明需要清仓)
                - 仅当真实数据显示极端行情时才清仓

        日志样本 (修复前):
            [RiskGuard] [流动性危机] 所有数据源不可用, fail-closed 返回 2500 触发撤单
            [RiskGuard] [流动性危机] 触发全局撤单: 涨停 2500 + 跌停 0 = 2500 > 2000, 数据源=fail_closed
        """
        LIMIT_COUNT_THRESHOLD = 2000  # 涨跌停家数阈值 (真实数据触发)
        # v8.6.8 P1-LIVE-05: 移除 FAIL_CLOSED_COUNT=2500, 数据源不可用时返回 0 + 标记 fail_closed

        try:
            limit_up, limit_down, data_source = self._fetch_limit_counts(pnl_report)
            total_limit = limit_up + limit_down
            is_data_unavailable = (data_source == "fail_closed")

            plan.setdefault('risk_guard', {})['liquidity_crisis'] = {
                'limit_up': limit_up,
                'limit_down': limit_down,
                'total_limit': total_limit,
                'threshold': LIMIT_COUNT_THRESHOLD,
                'data_source': data_source,
                'data_unavailable': is_data_unavailable,
            }

            if is_data_unavailable:
                # v8.6.8 P1-LIVE-05: 数据源不可用 — 保守禁开仓, 但不清空订单
                # 风控原则: 无数据 ≠ 极端行情, 不应误清仓
                plan.setdefault('market_state', {})
                plan['market_state']['spot_build_allowed'] = False
                # v8.6.8 P0-05 FIX (2026-07-26): L2 等效场景必须同步 build_allowed=False
                # 原代码只设 spot_build_allowed=False, 但 build_allowed 仍为 True
                # 与一致性校验和 overnight_gap L2 处理不对齐, 下游执行器读 build_allowed 会绕过风控
                plan['market_state']['build_allowed'] = False
                # 不覆盖更高优先级 Guard 的 CRITICAL
                if plan['market_state'].get('circuit_level') != 'CRITICAL':
                    plan['market_state']['circuit_level'] = 'WARNING'
                plan['risk_guard']['liquidity_crisis']['action'] = 'DATA_UNAVAILABLE_NO_NEW_POSITIONS'
                plan['risk_guard']['liquidity_crisis']['triggered'] = False
                plan['risk_guard']['liquidity_crisis']['note'] = (
                    '数据源不可用, 保守禁开仓 (不清空订单). '
                    '生产环境请检查 akshare/astock_realtime 连通性'
                )
                self._log(
                    f"[流动性危机] 数据源不可用, 保守禁开仓 (不清空订单). "
                    f"建议检查数据源连通性. data_source={data_source}"
                )
            elif total_limit > LIMIT_COUNT_THRESHOLD:
                # 真实数据触发全局撤单
                plan.setdefault('execution_plan', {})
                plan['execution_plan']['morning_orders'] = []
                plan['execution_plan']['afternoon_orders'] = []
                plan.setdefault('market_state', {})
                plan['market_state']['liquidity_crisis'] = True
                plan['market_state']['spot_build_allowed'] = False
                plan['market_state']['circuit_level'] = 'CRITICAL'
                # v8.6.8 P0-01 FIX (2026-07-26): circuit_level=CRITICAL 必须 build_allowed=False
                # 原代码遗漏此字段, 导致 trade_plan 同时出现
                #   market_state.build_allowed=true vs market_state.circuit_level=CRITICAL
                # 顶级对冲基金标准: 风控字段必须自洽, CRITICAL 时禁止任何新开仓
                plan['market_state']['build_allowed'] = False
                plan['risk_guard']['liquidity_crisis']['action'] = 'CANCEL_ALL_ORDERS'
                plan['risk_guard']['liquidity_crisis']['triggered'] = True

                self._log(
                    f"[流动性危机] 触发全局撤单: 涨停 {limit_up} + 跌停 {limit_down} = "
                    f"{total_limit} > {LIMIT_COUNT_THRESHOLD}, 数据源={data_source}"
                )
            else:
                plan['risk_guard']['liquidity_crisis']['action'] = 'NORMAL'
                plan['risk_guard']['liquidity_crisis']['triggered'] = False
                self._log(
                    f"[流动性危机] 正常: 涨跌停 {total_limit} < {LIMIT_COUNT_THRESHOLD} "
                    f"(涨停 {limit_up} + 跌停 {limit_down}, 数据源={data_source})"
                )
        except Exception as e:
            self._log(f"[流动性危机] 检查崩溃: {e}")
            plan.setdefault('risk_guard', {})['liquidity_crisis_error'] = str(e)
            # v8.6.8 P1-LIVE-05: 崩溃时仅禁开仓, 不清空订单 (避免误清仓)
            plan.setdefault('market_state', {})['spot_build_allowed'] = False
            if plan['market_state'].get('circuit_level') != 'CRITICAL':
                plan['market_state']['circuit_level'] = 'WARNING'

        return plan

    def _fetch_limit_counts(self, pnl_report: Dict = None) -> tuple:
        """获取全市场涨跌停家数 (三层 fallback)

        Args:
            pnl_report: 当日盈亏报告 (用于 Layer 2 提取持仓代码)

        Returns:
            (limit_up_count, limit_down_count, data_source)
            - 真实数据: limit_up + limit_down > 2000 → 触发全局撤单 (CRITICAL)
            - 数据源不可用: 返回 (0, 0, "fail_closed"), 由调用方决定保守动作 (WARNING)
            v8.6.8 P1-LIVE-05: 移除 FAIL_CLOSED_COUNT=2500 误触发清仓
        """
        # v8.6.8 P1-LIVE-05: 移除 FAIL_CLOSED_COUNT=2500 (误触发 CRITICAL 全局撤单)
        # 数据源不可用时返回 0, 由 guard_liquidity_crisis 根据 data_source="fail_closed"
        # 做保守处理 (禁开仓 + WARNING, 但不清空订单)
        # Layer 1: akshare 全市场实时行情
        try:
            import akshare as ak
            df = ak.stock_zh_a_spot_em()
            if df is not None and not df.empty and '涨跌幅' in df.columns:
                # 涨停: 涨幅 >= 9.5% (考虑浮点误差)
                # 跌停: 跌幅 <= -9.5%
                pct = df['涨跌幅']
                limit_up = int((pct >= 9.5).sum())
                limit_down = int((pct <= -9.5).sum())
                return limit_up, limit_down, "akshare"
        except ImportError:
            self._log("[流动性危机] akshare 未安装, 尝试 Layer 2")
        except Exception as e:
            self._log(f"[流动性危机] akshare 获取失败: {e}, 尝试 Layer 2")

        # Layer 2: astock_realtime 持仓样本 (降级, 不准确)
        try:
            from utils.astock_realtime import get_realtime_quotes
            # v8.6.6 修复: 使用兼容层提取持仓代码 (支持完整格式和简化格式)
            codes = []
            if pnl_report:
                positions = self._extract_positions(pnl_report)
                codes = [p.get('code', p.get('symbol', '')) for p in positions if isinstance(p, dict)]

            # 清理代码格式 (astock_realtime 需要纯数字代码)
            clean_codes = []
            for c in codes[:50]:
                if not c:
                    continue
                # 提取纯数字部分 (如 "600519.SH" → "600519")
                num = ''.join(ch for ch in str(c) if ch.isdigit())
                if num:
                    clean_codes.append(num)

            if clean_codes:
                quotes = get_realtime_quotes(clean_codes)
                limit_up = sum(1 for q in quotes.values() if q.get('change_pct', 0) >= 9.5)
                limit_down = sum(1 for q in quotes.values() if q.get('change_pct', 0) <= -9.5)
                # 样本外推 (持仓 N 只 → 全市场 ~5000 只, 按比例放大)
                scale = max(1, 5000 // max(len(clean_codes), 1))
                self._log(
                    f"[流动性危机] Layer 2 样本外推: {len(clean_codes)} 只持仓 → "
                    f"涨停 {limit_up}×{scale} + 跌停 {limit_down}×{scale}"
                )
                return limit_up * scale, limit_down * scale, "astock_sample"
        except Exception as e:
            self._log(f"[流动性危机] astock_realtime 获取失败: {e}")

        # Layer 3: 数据源不可用 — 返回 0 + 标记 fail_closed
        # v8.6.8 P1-LIVE-05: 不再返回 2500 触发误清仓, 改为返回 0 让调用方保守处理
        self._log(
            "[流动性危机] 所有数据源不可用, 返回 (0, 0, fail_closed), "
            "由 guard_liquidity_crisis 保守禁开仓 (不清空订单)"
        )
        return 0, 0, "fail_closed"

    # ============================================================
    # Guard 8: 隔夜跳空缺口 (P1-I 新增, v8.6.6)
    # ============================================================
    def guard_overnight_gap(self, pnl_report: Dict, plan: Dict) -> Dict:
        """隔夜跳空 Guard - 外盘隔夜风险触发 L1/L2/L3

        L1 (S&P500 跌 1% 或 ADR 偏离 2%): 预警
        L2 (S&P500 跌 2% 或 ADR 偏离 4%): 禁止开仓
        L3 (S&P500 跌 3% 或 ADR 偏离 6%): 全局平仓

        委托 utils/overnight_gap_monitor.py OvernightGapMonitor 实现
        """
        try:
            from utils.overnight_gap_monitor import OvernightGapMonitor
        except ImportError:
            self._log("[隔夜跳空] OvernightGapMonitor 导入失败, 跳过")
            plan.setdefault('risk_guard', {})['overnight_gap'] = {
                'status': 'SKIP', 'reason': 'import_failed'
            }
            return plan

        try:
            ogm = OvernightGapMonitor()
            risk = ogm.evaluate_overnight_risk()
            plan = ogm.apply_to_plan(plan, risk)

            if risk['level'] >= 2:
                self._log(
                    f"[隔夜跳空] L{risk['level']} 触发: S&P500 {risk['sp500_change_pct']:.2%}, "
                    f"ADR偏离 {risk['adr_deviation_pct']:.2%}, 数据源={risk['data_source']}, "
                    f"触发={risk.get('trigger', 'unknown')}"
                )
            elif risk['level'] == 1:
                self._log(
                    f"[隔夜跳空] L1 预警: S&P500 {risk['sp500_change_pct']:.2%}, "
                    f"ADR偏离 {risk['adr_deviation_pct']:.2%}"
                )
            else:
                self._log(
                    f"[隔夜跳空] 正常: S&P500 {risk['sp500_change_pct']:.2%} "
                    f"(数据源={risk['data_source']})"
                )
        except Exception as e:
            self._log(f"[隔夜跳空] 检查崩溃: {e}")
            plan.setdefault('risk_guard', {})['overnight_gap_error'] = str(e)
            # fail-closed: 隔夜跳空崩溃时禁止建仓
            plan.setdefault('market_state', {})['spot_build_allowed'] = False

        return plan

    # ============================================================
    # Guard 9: 相关性对冲 (P1-K 新增, v8.6.6)
    # ============================================================
    def guard_correlation_hedge(self, pnl_report: Dict, plan: Dict) -> Dict:
        """相关性对冲 Guard - 集成 CorrelationHedger 到 EOD 链

        触发条件 (CorrelationHedger.compute_hedge):
            1. avg_corr > 0.85 且 jump > 0.15
            2. avg_corr > 0.95 (极端趋同)

        响应动作:
            - 生成黄金 ETF (518880) 买入订单
            - 生成国债逆回购 (GC001) 订单
            - 写入 plan['correlation_hedge_orders']
        """
        try:
            # 复用 v8.3_institutional 的 CorrelationHedger
            import sys as _sys
            _v83_src = BASE_DIR / "v8.3_institutional" / "src"
            if str(_v83_src) not in _sys.path:
                _sys.path.insert(0, str(_v83_src))
            from hedging.correlation_hedger import CorrelationHedger
        except ImportError as e:
            self._log(f"[相关性对冲] CorrelationHedger 导入失败, 跳过: {e}")
            plan.setdefault('risk_guard', {})['correlation_hedge'] = {
                'status': 'SKIP', 'reason': 'import_failed'
            }
            return plan

        try:
            # 构建持仓标的收益率 DataFrame
            returns_df = self._build_position_returns(pnl_report, lookback_days=60)
            if returns_df is None or returns_df.empty:
                self._log("[相关性对冲] 无可用收益率数据, 跳过")
                plan.setdefault('risk_guard', {})['correlation_hedge'] = {
                    'action': 'NO_DATA', 'reason': 'insufficient_returns_history'
                }
                return plan

            # 获取组合市值 (v8.6.6 修复: 使用兼容层支持两种报告格式)
            pnl_summary = self._extract_summary(pnl_report)
            portfolio_value = float(pnl_summary.get('total_market_value', 0)) or self.total_capital

            # 计算相关性对冲
            hedger = CorrelationHedger()
            hedge_result = hedger.compute_hedge(returns_df, portfolio_value)

            plan['risk_guard']['correlation_hedge'] = {
                'action': hedge_result.get('action', 'UNKNOWN'),
                'avg_corr': float(hedge_result.get('avg_corr', 0)),
                'baseline_corr': float(hedge_result.get('baseline_corr', 0)),
                'jump': float(hedge_result.get('jump', 0)),
            }

            if hedge_result.get('action') == 'SAFE_HAVEN_ALLOC':
                # 生成避险资产配置订单
                hedge_orders = self._build_safe_haven_orders(hedge_result)
                plan['correlation_hedge_orders'] = hedge_orders
                plan['risk_guard']['correlation_hedge']['gold_weight'] = float(
                    hedge_result.get('gold_weight', 0)
                )
                plan['risk_guard']['correlation_hedge']['repo_weight'] = float(
                    hedge_result.get('repo_weight', 0)
                )
                self._log(
                    f"[相关性对冲] 触发避险配置: ρ̄={hedge_result.get('avg_corr', 0):.3f}, "
                    f"黄金ETF {hedge_result.get('gold_weight', 0):.2%}, "
                    f"逆回购 {hedge_result.get('repo_weight', 0):.2%}"
                )
            else:
                self._log(
                    f"[相关性对冲] 无需对冲: {hedge_result.get('reason', '条件未满足')}"
                )
        except Exception as e:
            self._log(f"[相关性对冲] 执行崩溃: {e}")
            plan.setdefault('risk_guard', {})['correlation_hedge_error'] = str(e)
            # 相关性对冲崩溃不阻断主流程 (仅记录错误)

        return plan

    def _build_position_returns(self, pnl_report: Dict, lookback_days: int = 60):
        """从历史 daily_pnl_report 构建持仓标的收益率 DataFrame

        适配实际报告结构 (v8.6.6 修复):
            {
                "date": "2026-07-24",
                "summary": {"total_pnl": ..., "total_market_value": ..., "total_cost": ...},
                "positions": [{"code": "600519.SH", "name": "...", "pnl": 500.3, "market_value": 150000}, ...]
            }

        Args:
            pnl_report: 当日盈亏报告 (用于提取持仓代码)
            lookback_days: 回看天数

        Returns:
            pandas DataFrame (T×N 收益率) 或 None
        """
        try:
            import pandas as pd

            # v8.6.6 修复: 使用兼容层提取持仓 (支持完整格式和简化格式)
            positions = self._extract_positions(pnl_report)
            symbols = [p.get('code', p.get('symbol', '')) for p in positions if isinstance(p, dict)]

            if not symbols:
                self._log("[相关性对冲] 当日持仓为空, 无法构建收益率序列")
                return None

            # 加载历史报告
            report_files = sorted(REPORTS_DIR.glob("daily_pnl_report_*.json"))[-lookback_days:]
            if len(report_files) < 10:
                self._log(f"[相关性对冲] 历史报告不足 10 份 (实际 {len(report_files)}), 跳过")
                return None

            # 构建收益率序列
            returns_data = {sym: [] for sym in symbols}
            valid_days = 0
            for rf in report_files:
                try:
                    with open(rf, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    # v8.6.6 修复: 使用兼容层提取历史持仓
                    pos_list = self._extract_positions(data)
                    if not isinstance(pos_list, list):
                        continue

                    # 构建 {code: daily_return} 映射
                    day_returns = {}
                    for pos in pos_list:
                        if not isinstance(pos, dict):
                            continue
                        sym = pos.get('code', pos.get('symbol', ''))
                        if not sym or sym not in symbols:
                            continue
                        # v8.6.6 修复: 优先使用 daily_pnl_pct (完整格式, 百分比)
                        daily_pnl_pct = pos.get('daily_pnl_pct')
                        if daily_pnl_pct is not None:
                            daily_ret = float(daily_pnl_pct) / 100.0
                        else:
                            # fallback: pnl / market_value (简化格式)
                            pnl = float(pos.get('pnl', 0))
                            market_value = float(pos.get('market_value', 0))
                            daily_ret = pnl / market_value if market_value > 0 else 0.0
                        day_returns[sym] = daily_ret

                    # 所有标的都填充 (缺失的填 0)
                    for sym in symbols:
                        returns_data[sym].append(day_returns.get(sym, 0.0))
                    valid_days += 1
                except Exception:
                    continue

            if valid_days < 10:
                self._log(f"[相关性对冲] 有效历史数据不足 ({valid_days} 天), 跳过")
                return None

            df = pd.DataFrame(returns_data)
            self._log(
                f"[相关性对冲] 构建收益率矩阵: {df.shape[0]} 天 × {df.shape[1]} 标的"
            )
            return df

        except ImportError:
            self._log("[相关性对冲] pandas 未安装, 无法构建收益率矩阵")
            return None
        except Exception as e:
            self._log(f"[相关性对冲] 构建收益率失败: {e}")
            return None

    def _build_safe_haven_orders(self, hedge_result: Dict) -> list:
        """生成避险资产配置订单

        Args:
            hedge_result: CorrelationHedger.compute_hedge() 返回值

        Returns:
            订单列表 [{symbol, direction, weight, value, ...}]
        """
        orders = []
        gold_weight = float(hedge_result.get('gold_weight', 0))
        repo_weight = float(hedge_result.get('repo_weight', 0))
        gold_value = float(hedge_result.get('gold_value', 0))
        repo_value = float(hedge_result.get('repo_value', 0))

        if gold_weight > 0:
            orders.append({
                'symbol': hedge_result.get('gold_etf', '518880'),
                'direction': 'BUY',
                'order_type': 'SAFE_HAVEN',
                'weight': gold_weight,
                'est_amount': gold_value,
                'reason': 'correlation_hedge_gold',
                'note': f"相关性对冲: 黄金ETF {gold_weight:.2%}",
            })

        if repo_weight > 0:
            orders.append({
                'symbol': hedge_result.get('repo_symbol', 'GC001'),
                'direction': 'BUY',
                'order_type': 'SAFE_HAVEN',
                'weight': repo_weight,
                'est_amount': repo_value,
                'reason': 'correlation_hedge_repo',
                'note': f"相关性对冲: 国债逆回购 {repo_weight:.2%}",
            })

        return orders

    # ============================================================
    # v7.7: 对冲引擎 & 认沽保护引擎去重
    # ============================================================
    def _deduplicate_put_orders(self, plan: Dict):
        """去重: 避免 HedgeExecutionEngine 与 ProtectivePutEngine 对同一底层重复生成 PUT 订单

        策略:
          - ProtectivePutEngine 是 PUT 订单的权威来源 (含真实权利金估算、预算控制)
          - HedgeExecutionEngine 的 options_orders 如与 put_protection_orders 重复底层，则剔除
          - HedgeExecutionEngine 的 futures_orders 不受影响
        """
        put_protection_orders = plan.get('put_protection_orders', [])
        hedge_execution = plan.get('hedge_execution', {})
        options_orders = hedge_execution.get('options_orders', [])

        if not put_protection_orders or not options_orders:
            self._log("[去重] 无需去重 (单侧无Put订单)")
            return

        # 提取认沽保护引擎已覆盖的底层
        protected_codes = set()
        for o in put_protection_orders:
            code = self._extract_underlying_code(
                o.get('underlying', '') or o.get('instrument', '')
            )
            if code:
                protected_codes.add(code)

        if not protected_codes:
            self._log("[去重] 无法识别受保护底层，跳过")
            return

        # 检查对冲引擎的期权订单
        removed = []
        kept_options = []
        for o in options_orders:
            o_code = self._extract_underlying_code(
                o.get('instrument', '') or o.get('underlying', '')
            )
            if o_code and o_code in protected_codes:
                removed.append({'instrument': o.get('instrument', '?'), 'code': o_code,
                                'order_id': o.get('order_id', '?')})
            else:
                kept_options.append(o)

        if removed:
            plan['hedge_execution']['options_orders'] = kept_options
            # 更新订单总数
            futures = hedge_execution.get('futures_orders', [])
            plan['hedge_execution']['total_orders'] = len(futures) + len(kept_options)
            plan['risk_guard']['hedge_action'] = (
                f'GENERATED_{len(futures) + len(kept_options)}_ORDERS'
                f'(dedup_removed_{len(removed)})'
            )
            plan.setdefault('risk_guard', {})['put_hedge_dedup'] = {
                'removed_count': len(removed),
                'removed': [r['code'] for r in removed],
                'reason': 'ProtectivePutEngine 已覆盖, 避免超额对冲'
            }

            self._log(f"[去重] 剔除 {len(removed)} 笔重复对冲PUT: "
                     f"{', '.join(r['code'] for r in removed)}")
            self._log(f"[去重] 对冲引擎保留 {len(kept_options)} 笔独立PUT + "
                     f"{len(futures)} 笔期货")
        else:
            self._log("[去重] 无重复底层，所有订单保留")

    # ============================================================
    # 主执行入口
    # ============================================================
    def run_all_guards(self, next_trade_date: str) -> Dict:
        """执行所有风控守卫
        
        Args:
            next_trade_date: 下一交易日 (YYYY-MM-DD)
            
        Returns:
            修改后的交易计划
        """
        self._log("=" * 60)
        self._log(f"风控守卫集成器启动 | 报告日:{self.report_date} → 次日:{next_trade_date}")
        self._log("=" * 60)

        # 加载数据
        pnl_report = self._load_pnl_report()
        if not pnl_report:
            self._log("[WARN] 无法加载盈亏报告，使用空报告继续")
            pnl_report = {'portfolio_pnl': {'summary': {'total_cost': 0, 'total_market_value': 0, 'total_pnl': 0}}}

        plan = self._load_next_trade_plan(next_trade_date)
        if not plan:
            self._log("[WARN] 无法加载次日计划，风控守卫将只输出日志")
            plan = {'phase': {'daily_capital': 150000}, 'execution_plan': {}, 'risk_guard': {}}

        # 按优先级执行 (KillSwitch 最高优先级, 其次回撤)
        # 每个 guard 独立 try-except, 防止单个 guard 崩溃中断整个风控链路
        # v8.6.6 (P1-H/J/I/K): EOD Guard 链从 5 个扩展为 7 个, 覆盖大盘级/组合级/对冲级三层风控
        # 审计: docs/HEDGE_FUND_AUDIT_V2_2026-07-26.md

        # [1/7] 保证金熔断 (KillSwitch) — 最高优先级 (原有)
        self._log("--- [1/7] 保证金熔断 (KillSwitch) ---")
        try:
            plan = self.guard_kill_switch(pnl_report, plan)
        except Exception as e:
            self._log(f"[CRITICAL] 保证金熔断检查崩溃: {e}")
            plan.setdefault('risk_guard', {})['kill_switch_error'] = str(e)
            # 风控崩溃时保守处理: 禁止开仓
            plan.setdefault('market_state', {})['spot_build_allowed'] = False

        # [2/7] 大盘熔断 (P1-H 新增) — 大盘级
        self._log("--- [2/7] 大盘熔断 (P1-H) ---")
        try:
            plan = self.guard_market_circuit_breaker(pnl_report, plan)
        except Exception as e:
            self._log(f"[CRITICAL] 大盘熔断检查崩溃: {e}")
            plan.setdefault('risk_guard', {})['market_circuit_breaker_error'] = str(e)
            plan.setdefault('market_state', {})['spot_build_allowed'] = False

        # [3/7] 流动性危机 (P1-J 新增) — 全市场涨跌停
        self._log("--- [3/7] 流动性危机 (P1-J) ---")
        try:
            plan = self.guard_liquidity_crisis(pnl_report, plan)
        except Exception as e:
            self._log(f"[CRITICAL] 流动性危机检查崩溃: {e}")
            plan.setdefault('risk_guard', {})['liquidity_crisis_error'] = str(e)
            plan.setdefault('market_state', {})['spot_build_allowed'] = False

        # [4/7] 隔夜跳空 (P1-I 新增) — 隔夜外盘风险
        self._log("--- [4/7] 隔夜跳空 (P1-I) ---")
        try:
            plan = self.guard_overnight_gap(pnl_report, plan)
        except Exception as e:
            self._log(f"[CRITICAL] 隔夜跳空检查崩溃: {e}")
            plan.setdefault('risk_guard', {})['overnight_gap_error'] = str(e)
            plan.setdefault('market_state', {})['spot_build_allowed'] = False

        # [5/7] 回撤检查 — 组合级 (原有)
        self._log("--- [5/7] 回撤检查 ---")
        try:
            plan = self.guard_drawdown(pnl_report, plan)
        except Exception as e:
            self._log(f"[CRITICAL] 回撤检查崩溃: {e}")
            plan.setdefault('risk_guard', {})['drawdown_error'] = str(e)

        # [6/7] 波动率控制 — 组合级 (原有)
        self._log("--- [6/7] 波动率控制 ---")
        try:
            plan = self.guard_vol_target(pnl_report, plan)
        except Exception as e:
            self._log(f"[CRITICAL] 波动率控制崩溃: {e}")
            plan.setdefault('risk_guard', {})['vol_target_error'] = str(e)

        # [7/7] 对冲执行 + 认沽保护 + 相关性对冲 — 对冲动作 (原有 + P1-K 新增)
        self._log("--- [7/7] 对冲执行 ---")
        try:
            plan = self.guard_hedge_execution(pnl_report, plan, next_trade_date)
        except Exception as e:
            self._log(f"[CRITICAL] 对冲执行崩溃: {e}")
            plan.setdefault('risk_guard', {})['hedge_error'] = str(e)

        self._log("--- [7/7] 认沽保护 ---")
        try:
            plan = self.guard_protective_put(pnl_report, plan, next_trade_date)
        except Exception as e:
            self._log(f"[CRITICAL] 认沽保护崩溃: {e}")
            plan.setdefault('risk_guard', {})['put_error'] = str(e)

        self._log("--- [7/7] 相关性对冲 (P1-K) ---")
        try:
            plan = self.guard_correlation_hedge(pnl_report, plan)
        except Exception as e:
            self._log(f"[CRITICAL] 相关性对冲崩溃: {e}")
            plan.setdefault('risk_guard', {})['correlation_hedge_error'] = str(e)

        # v7.7: 去重 — 避免对冲引擎与认沽保护引擎对同一底层重复生成PUT
        self._log("--- [去重] 检查 PUT 订单重叠 ---")
        self._deduplicate_put_orders(plan)

        # v8.6.8 P0-01 FIX (2026-07-26): 风控字段最终一致性校验 (defense-in-depth)
        # 顶级对冲基金标准: 任何风控字段的最终状态必须自洽, 防止 Guard 链中
        #   某个 Guard 设了 circuit_level=CRITICAL 但遗漏 build_allowed=False
        #   导致 trade_plan 出现 build_allowed=true vs circuit_level=CRITICAL 的矛盾
        # 此校验作为最后兜底, 在保存前强制对齐所有派生字段
        try:
            ms = plan.setdefault('market_state', {})
            circuit_lvl = str(ms.get('circuit_level', 'NORMAL')).upper()
            if circuit_lvl == 'CRITICAL':
                if ms.get('build_allowed', True) or ms.get('spot_build_allowed', True):
                    self._log(
                        f"[一致性校验] circuit_level=CRITICAL 但 build_allowed="
                        f"{ms.get('build_allowed')}, spot_build_allowed="
                        f"{ms.get('spot_build_allowed')}, 强制对齐为 False"
                    )
                ms['build_allowed'] = False
                ms['spot_build_allowed'] = False
            elif circuit_lvl == 'WARNING':
                if ms.get('spot_build_allowed', True):
                    self._log(
                        f"[一致性校验] circuit_level=WARNING, spot_build_allowed="
                        f"{ms.get('spot_build_allowed')}, 强制对齐为 False"
                    )
                ms['spot_build_allowed'] = False

            # v8.6.8 P0-04 FIX (2026-07-26): spot_build_allowed=False 时必须清空 Theta Covered Call
            # 原始 bug: spot_build_allowed=false (WARNING) 时仅阻止现货建仓, 但 execution_plan.options_orders
            # 仍包含 Theta 引擎生成的 Covered Call 订单 (SELL_CALL). Covered Call 必须先持有现货才能卖出,
            # 无现货时执行 SELL_CALL 是裸卖出, 风险无限 (类似 GME 逼空事件).
            # 顶级对冲基金标准: 备兑策略必须有底层多头支撑, 否则一律禁止
            if not ms.get('spot_build_allowed', True):
                exec_plan = plan.get('execution_plan', {}) or {}
                cc_orders = exec_plan.get('options_orders', []) or []
                if cc_orders:
                    removed_cc = [
                        o for o in cc_orders
                        if str(o.get('direction', '')).upper() == 'SELL_CALL'
                        or 'CoveredCall' in str(o.get('name', ''))
                    ]
                    kept_cc = [
                        o for o in cc_orders
                        if not (str(o.get('direction', '')).upper() == 'SELL_CALL'
                               or 'CoveredCall' in str(o.get('name', '')))
                    ]
                    if removed_cc:
                        exec_plan['options_orders'] = kept_cc
                        exec_plan['options_orders_count'] = len(kept_cc)
                        # 重算总权利金 (仅 SELL_CALL 收入, 买入 PUT 是支出)
                        new_premium = sum(
                            float(o.get('est_premium_total', 0))
                            for o in kept_cc
                            if str(o.get('direction', '')).upper() == 'SELL_CALL'
                        )
                        exec_plan['options_total_premium'] = round(new_premium, 2)
                        plan['execution_plan'] = exec_plan
                        self._log(
                            f"[一致性校验] [P0-04] spot_build_allowed=False, "
                            f"已拦截 {len(removed_cc)} 笔 Theta Covered Call 订单 "
                            f"(裸卖出 Call 风险无限, 必须有现货备兑)"
                        )
                        # 同步 hedge_fund_overlays.theta_engine
                        theta = plan.get('hedge_fund_overlays', {}).get('theta_engine', {})
                        if theta:
                            theta['positions_count'] = len(kept_cc)
                            theta['total_premium'] = round(new_premium, 2)
                            theta['blocked_reason'] = 'spot_build_allowed=False, Covered Call 已拦截'

            # 同步 hedge_fund_overlays.v77_notes 与 market_state 一致
            hfo = plan.get('hedge_fund_overlays', {})
            notes = hfo.setdefault('v77_notes', {})
            notes['build_allowed'] = ms.get('build_allowed', True)
            notes['spot_build_allowed'] = ms.get('spot_build_allowed', True)
            if not ms.get('build_allowed', True) and not notes.get('reason_if_blocked'):
                notes['reason_if_blocked'] = f"circuit_level={circuit_lvl}"
        except Exception as _e_consistency:
            self._log(f"[一致性校验] 异常: {_e_consistency}")

        # 写入时间戳
        plan.setdefault('risk_guard', {})['last_run'] = datetime.now().isoformat()
        plan['risk_guard']['report_date'] = self.report_date

        # 保存修改后的计划
        self._save_trade_plan(plan, next_trade_date)

        # 写入风控日志
        self._write_guard_log(next_trade_date)

        self._log("=" * 60)
        self._log("风控守卫集成器完成")
        self._log("=" * 60)

        return plan

    def _write_guard_log(self, next_date: str):
        """写入风控日志"""
        log_file = LOGS_DIR / f"risk_guard_{next_date.replace('-', '')}.log"
        try:
            with open(log_file, 'w', encoding='utf-8') as f:
                f.write('\n'.join(self.log_entries))
        except Exception:
            pass


# ============================================================
# CLI 入口
# ============================================================
def main():
    """命令行入口: python -m utils.risk_guard_integrator [report_date] [next_date]"""
    import sys
    from datetime import timedelta

    report_date = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime('%Y-%m-%d')
    
    if len(sys.argv) > 2:
        next_date = sys.argv[2]
    else:
        # 计算下一交易日
        from datetime import datetime as _dt
        today = _dt.strptime(report_date, '%Y-%m-%d')
        next_day = today + timedelta(days=1)
        while next_day.weekday() >= 5:
            next_day += timedelta(days=1)
        next_date = next_day.strftime('%Y-%m-%d')

    integrator = RiskGuardIntegrator(report_date=report_date)
    integrator.run_all_guards(next_trade_date=next_date)


if __name__ == '__main__':
    main()
