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
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger("risk_guard_integrator")

BASE_DIR = Path(__file__).resolve().parent.parent
TRADE_PLANS_DIR = BASE_DIR / "v8.3_institutional" / "trade_plans"
REPORTS_DIR = BASE_DIR / "v8.3_institutional" / "reports"
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
        """加载当日盈亏报告"""
        json_path = REPORTS_DIR / f"daily_pnl_report_{self.report_date}.json"
        if not json_path.exists():
            # 尝试无横杠格式
            alt_path = REPORTS_DIR / f"daily_pnl_report_{self.report_date.replace('-', '')}.json"
            if alt_path.exists():
                json_path = alt_path
            else:
                return None
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            self._log(f"加载盈亏报告失败: {e}")
            return None

    def _get_pnl_summary(self, pnl_report: Dict) -> Dict:
        """从盈亏报告中提取汇总数据 (v7.7修正: 适配 portfolio_pnl.summary 嵌套结构)"""
        return pnl_report.get('portfolio_pnl', {}).get('summary', {})

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
        
        plan['phase']['daily_capital'] = adjusted_budget
        plan['phase']['day_capital'] = adjusted_budget
        plan.setdefault('risk_guard', {})['vol_scale'] = vol_scale
        plan['risk_guard']['vol_action'] = f'SCALE_DOWN_{vol_scale:.2f}'
        plan['risk_guard']['vol_note'] = (
            f'波动率目标控制: vol_scale={vol_scale:.3f}, '
            f'预算 {original_budget:,.0f} → {adjusted_budget:,.0f}'
        )

        self._log(f"[波动率] vol_scale={vol_scale:.3f}, 预算缩减: "
                  f"{original_budget:,.0f} → {adjusted_budget:,.0f}")
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
                
                # 写入交易计划
                plan['hedge_execution'] = hedge_result
                plan.setdefault('risk_guard', {})['hedge_action'] = f'GENERATED_{total_orders}_ORDERS'
                
                # 同时写入独立的对冲执行文件
                engine.write_to_trade_plan(hedge_result, next_date)
                
                self._log(f"[对冲] 已生成 {total_orders} 条对冲订单 "
                         f"(期货{len(futures_orders)}+期权{len(options_orders)})")
                
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
        margin_used = pnl_summary.get('margin_used', 0)
        total_equity = pnl_summary.get('total_equity', self.total_capital)
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

        # L3: 强制停止一切
        if not margin_status.get('can_trade', True):
            plan['market_state'] = plan.get('market_state', {})
            plan['market_state']['circuit_level'] = 'CRITICAL'
            plan['market_state']['build_allowed'] = False
            plan['market_state']['spot_build_allowed'] = False
            plan['execution_plan'] = plan.get('execution_plan', {})
            plan['execution_plan']['morning_orders'] = []
            plan['execution_plan']['afternoon_orders'] = []
            self._log("[KillSwitch] [EMERGENCY] L3 触发: 全面停止交易, 仅允许平仓")
            return plan

        # L2: 禁止开仓
        if not margin_status.get('can_open', True):
            plan['market_state'] = plan.get('market_state', {})
            plan['market_state']['spot_build_allowed'] = False
            plan['execution_plan'] = plan.get('execution_plan', {})
            # 过滤掉建仓订单, 保留平仓订单
            for session in ['morning_orders', 'afternoon_orders']:
                orders = plan['execution_plan'].get(session, [])
                plan['execution_plan'][session] = [o for o in orders if o.get('direction') == 'SELL']
            self._log("[KillSwitch] [WARN] L2 触发: 禁止开仓, 仅允许平仓")

        return plan

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
        self._log("--- [1/5] 保证金熔断 ---")
        try:
            plan = self.guard_kill_switch(pnl_report, plan)
        except Exception as e:
            self._log(f"[CRITICAL] 保证金熔断检查崩溃: {e}")
            plan.setdefault('risk_guard', {})['kill_switch_error'] = str(e)
            # 风控崩溃时保守处理: 禁止开仓
            plan.setdefault('market_state', {})['spot_build_allowed'] = False

        self._log("--- [2/5] 回撤检查 ---")
        try:
            plan = self.guard_drawdown(pnl_report, plan)
        except Exception as e:
            self._log(f"[CRITICAL] 回撤检查崩溃: {e}")
            plan.setdefault('risk_guard', {})['drawdown_error'] = str(e)

        self._log("--- [3/5] 波动率控制 ---")
        try:
            plan = self.guard_vol_target(pnl_report, plan)
        except Exception as e:
            self._log(f"[CRITICAL] 波动率控制崩溃: {e}")
            plan.setdefault('risk_guard', {})['vol_target_error'] = str(e)

        self._log("--- [4/5] 对冲执行 ---")
        try:
            plan = self.guard_hedge_execution(pnl_report, plan, next_trade_date)
        except Exception as e:
            self._log(f"[CRITICAL] 对冲执行崩溃: {e}")
            plan.setdefault('risk_guard', {})['hedge_error'] = str(e)

        self._log("--- [5/5] 认沽保护 ---")
        try:
            plan = self.guard_protective_put(pnl_report, plan, next_trade_date)
        except Exception as e:
            self._log(f"[CRITICAL] 认沽保护崩溃: {e}")
            plan.setdefault('risk_guard', {})['put_error'] = str(e)

        # v7.7: 去重 — 避免对冲引擎与认沽保护引擎对同一底层重复生成PUT
        self._log("--- [去重] 检查 PUT 订单重叠 ---")
        self._deduplicate_put_orders(plan)

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
