# -*- coding: utf-8 -*-
"""
止损止盈监控模块

来源：整合自 E:\各种PY程序\stop_loss_monitor.py

核心功能：
1. 多级预警机制（正常/预警/危险/已触发）
2. 移动止盈（trailing stop）支持
3. 综合风险评分（PnL + 距止损距离 + 资产固有风险）
4. 格式化预警报告生成

使用方式：
  from utils.stop_loss import StopLossMonitor, generate_risk_report
  
  monitor = StopLossMonitor(rules)
  alerts = monitor.check_all(quotes)
  report = generate_risk_report(alerts)
"""

import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from enum import Enum

from utils.data_types import safe_float, safe_int

logger = logging.getLogger('stop_loss')


class AlertLevel(Enum):
    """预警级别"""
    NORMAL = "normal"         # 正常（距触发位>5%）
    WARNING = "warning"       # 预警（距触发位2%-5%）
    CRITICAL = "critical"     # 危险（距触发位0%-2%）
    TRIGGERED = "triggered"   # 已触发（突破止损/止盈位）


class RiskType(Enum):
    """风险类型"""
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"


class StopLossMonitor:
    """止损止盈监控器"""

    def __init__(self,
                 warning_threshold_pct: float = 5.0,
                 critical_threshold_pct: float = 2.0,
                 trailing_drawdown_pct: float = 10.0,
                 max_single_loss_rmb: float = 50_000):
        """
        Args:
            warning_threshold_pct: 预警阈值（距触发位百分比），默认 5%
            critical_threshold_pct: 危险阈值，默认 2%
            trailing_drawdown_pct: 移动止盈回撤阈值，默认 10%
            max_single_loss_rmb: 单只最大亏损额（元），默认 5万
        """
        self.warning_threshold = abs(warning_threshold_pct)
        self.critical_threshold = abs(critical_threshold_pct)
        self.trailing_drawdown_pct = abs(trailing_drawdown_pct)
        self.max_single_loss = max_single_loss_rmb
        self.alerts_history: List[Dict] = []

    def check_single(self,
                     code: str,
                     name: str,
                     current_price: float,
                     base_price: float,
                     stop_loss_pct: float,
                     take_profit_pct: float,
                     stop_loss_price: Optional[float] = None,
                     take_profit_price: Optional[float] = None,
                     high_price: Optional[float] = None,
                     position_weight: float = 0.0,
                     risk_level: str = 'medium',
                     trailing_stop: bool = False) -> Dict:
        """
        检查单只标的止损止盈状态。

        Args:
            code: 股票代码
            name: 股票名称
            current_price: 当前价格
            base_price: 基准价（建仓成本）
            stop_loss_pct: 止损百分比（如 -15.0）
            take_profit_pct: 止盈百分比（如 +50.0）
            stop_loss_price: 止损价（可选，不传则自动计算）
            take_profit_price: 止盈价（可选）
            high_price: 期间最高价（用于移动止盈）
            position_weight: 持仓权重
            risk_level: 资产固有风险等级 (low/medium/high)
            trailing_stop: 是否启用移动止盈

        Returns:
            完整的状态检查结果
        """
        # 计算触发价格
        safe_base = safe_float(base_price, default=0.0)
        safe_current = safe_float(current_price, default=None)
        safe_sl = safe_float(stop_loss_price, default=None)
        safe_tp = safe_float(take_profit_price, default=None)
        safe_high = safe_float(high_price, default=None)

        if safe_base is None or safe_base <= 0 or safe_current is None:
            return {
                'code': code,
                'status': 'unknown',
                'alert_level': AlertLevel.NORMAL,
                'message': '价格数据异常，无法判断',
                'should_alert': False
            }

        sl_price = safe_sl if safe_sl is not None else (safe_base * (1 + safe_float(stop_loss_pct, default=0.0) / 100))
        tp_price = safe_tp if safe_tp is not None else (safe_base * (1 + safe_float(take_profit_pct, default=0.0) / 100))

        # 当前收益率
        pnl_pct = (safe_current - safe_base) / safe_base * 100

        # 移动止盈调整
        effective_tp_price = tp_price
        if trailing_stop and safe_high is not None and safe_high > safe_base:
            peak_pct = (safe_high - safe_base) / safe_base * 100
            if peak_pct >= self.trailing_drawdown_pct:
                dd_from_peak = (safe_high - safe_current) / safe_high * 100
                if dd_from_peak >= self.trailing_drawdown_pct * 0.3:
                    effective_tp_price = safe_high * (1 - self.trailing_drawdown_pct / 100)

        # 距止损/止盈位距离
        dist_to_sl = (safe_current - sl_price) / sl_price * 100
        dist_to_tp = ((tp_price - safe_current) / tp_price * 100
                      if safe_current < tp_price
                      else -(safe_current - tp_price) / tp_price * 100)

        # 确定预警级别
        alert_level = self._determine_level(dist_to_sl)

        # 止损/止盈状态
        sl_status = {
            'type': 'stop_loss',
            'trigger_price': round(sl_price, 2),
            'trigger_pct': stop_loss_pct,
            'distance_pct': round(dist_to_sl, 2),
            'is_triggered': current_price <= sl_price,
        }
        tp_status = {
            'type': 'take_profit',
            'trigger_price': round(effective_tp_price if trailing_stop else tp_price, 2),
            'trigger_pct': take_profit_pct,
            'distance_pct': round(dist_to_tp, 2),
            'is_triggered': current_price >= tp_price,
            'trailing_active': trailing_stop and effective_tp_price != tp_price,
        }

        # 操作建议
        action = self._generate_action(alert_level, pnl_pct, dist_to_sl)

        # 风险评分
        risk_score = self._calculate_risk_score(pnl_pct, dist_to_sl, risk_level)

        result = {
            'code': code,
            'name': name,
            'current_price': current_price,
            'base_price': base_price,
            'pnl_pct': round(pnl_pct, 2),
            'stop_loss': sl_status,
            'take_profit': tp_status,
            'alert_level': alert_level.value,
            'distance_to_sl_pct': round(dist_to_sl, 2),
            'distance_to_tp_pct': round(dist_to_tp, 2),
            'action_suggestion': action,
            'risk_score': round(risk_score, 1),
            'position_weight': position_weight,
            'trailing_active': trailing_stop,
        }

        # 记录日志
        if alert_level in (AlertLevel.CRITICAL, AlertLevel.TRIGGERED):
            logger.warning("[%s] %s(%s) price=%.2f PnL=%.2f%% risk=%.0f",
                           alert_level.value.upper(), name, code,
                           current_price, pnl_pct, risk_score)
        else:
            logger.info("[%s] %s(%s) price=%.2f PnL=%.2f%% sl_dist=%.2f%%",
                        alert_level.value, name, code,
                        current_price, pnl_pct, dist_to_sl)

        self.alerts_history.append({**result, 'timestamp': datetime.now().isoformat()})
        return result

    def check_all(self, rules: List[Dict], quotes: Dict[str, Dict]) -> List[Dict]:
        """
        批量检查所有标的。

        Args:
            rules: 规则列表 [{'code': '600989', 'name': '宝丰能源', ...}, ...]
            quotes: 行情 {code: {'price': float, 'high': float|None, 'low': float|None}}

        Returns:
            按风险评分排序的结果列表
        """
        results = []
        for rule in rules:
            code = rule['code']
            if code not in quotes:
                results.append({'code': code, 'name': rule.get('name', '?'),
                                'error': '无行情数据', 'alert_level': 'unknown'})
                continue

            q = quotes[code]
            price = q.get('price', 0)
            if price <= 0:
                results.append({'code': code, 'name': rule.get('name', '?'),
                                'error': '价格无效', 'alert_level': 'unknown'})
                continue

            result = self.check_single(
                code=code,
                name=rule.get('name', code),
                current_price=price,
                base_price=rule.get('base_price', price),
                stop_loss_pct=rule.get('stop_loss_pct', -15.0),
                take_profit_pct=rule.get('take_profit_pct', 50.0),
                stop_loss_price=rule.get('stop_loss_price'),
                take_profit_price=rule.get('take_profit_price'),
                high_price=q.get('high'),
                position_weight=rule.get('position_weight', 0),
                risk_level=rule.get('risk_level', 'medium'),
                trailing_stop=rule.get('trailing_stop', False),
            )
            results.append(result)

        results.sort(key=lambda x: x.get('risk_score', 0), reverse=True)
        return results

    def _determine_level(self, dist_to_sl: float) -> AlertLevel:
        """确定预警级别"""
        if dist_to_sl <= 0:
            return AlertLevel.TRIGGERED
        if dist_to_sl <= self.critical_threshold:
            return AlertLevel.CRITICAL
        if dist_to_sl <= self.warning_threshold:
            return AlertLevel.WARNING
        return AlertLevel.NORMAL

    def _generate_action(self, level: AlertLevel, pnl_pct: float, dist_to_sl: float) -> str:
        """生成操作建议"""
        if level == AlertLevel.TRIGGERED:
            return "立即执行止损！价格已跌破止损位"
        if level == AlertLevel.CRITICAL:
            return f"危险！距止损仅{dist_to_sl:.1f}%，准备减仓或设条件单"
        if level == AlertLevel.WARNING:
            return f"预警：距止损{dist_to_sl:.1f}%，密切关注走势"
        if pnl_pct > 20:
            return "盈利良好，可考虑移动止盈锁定利润"
        if pnl_pct < -5:
            return "小幅浮亏，持续关注基本面变化"
        if pnl_pct > 0:
            return "持有观望，按计划执行"
        return "正常持有，定期监控"

    def _calculate_risk_score(self, pnl_pct: float, dist_to_sl: float,
                               risk_level: str) -> float:
        """综合风险评分 [0-100]，越高越危险"""
        score = 0.0

        # PnL 维度
        if pnl_pct < -20:
            score += 40
        elif pnl_pct < -10:
            score += 25
        elif pnl_pct < -5:
            score += 15
        elif pnl_pct < 0:
            score += 5

        # 距止损维度
        if dist_to_sl <= 0:
            score += 40
        elif dist_to_sl <= 2:
            score += 30
        elif dist_to_sl <= 5:
            score += 15
        elif dist_to_sl <= 10:
            score += 5

        # 固有风险乘数
        multipliers = {'low': 0.8, 'medium': 1.0, 'high': 1.2}
        score *= multipliers.get(risk_level, 1.0)

        return min(100, max(0, score))


def generate_risk_report(alerts: List[Dict]) -> str:
    """
    生成格式化的止损止盈预警报告。

    Args:
        alerts: StopLossMonitor.check_all() 的返回结果

    Returns:
        格式化报告文本
    """
    lines = []
    lines.append("=" * 70)
    lines.append("止损止盈风险监控报告")
    lines.append(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("=" * 70)
    lines.append("")

    # 统计
    counts = {'triggered': 0, 'critical': 0, 'warning': 0, 'normal': 0, 'unknown': 0}
    for a in alerts:
        lv = a.get('alert_level', 'unknown')
        counts[lv] = counts.get(lv, 0) + 1

    lines.append(f"监控总数: {len(alerts)}  |  "
                 f"已触发: {counts['triggered']}  |  "
                 f"危险: {counts['critical']}  |  "
                 f"预警: {counts['warning']}  |  "
                 f"正常: {counts['normal']}")
    lines.append("")

    # 详情表
    level_icons = {'normal': 'OK', 'warning': 'WARN', 'critical': 'CRIT', 'triggered': 'TRIG'}
    lines.append(f"{'状态':<6} {'名称':<10} {'代码':<8} {'现价':>8} "
                 f"{'PnL':>7} {'止损位':>8} {'距止损':>8} {'风险':>5}")
    lines.append("-" * 70)

    for a in alerts:
        icon = level_icons.get(a.get('alert_level', 'unknown'), '?')
        name = a.get('name', '?')[:8]
        code = a.get('code', '?')
        price = a.get('current_price', 0)
        pnl = a.get('pnl_pct', 0)
        sl = a.get('stop_loss', {})
        sl_price = sl.get('trigger_price', 0) if isinstance(sl, dict) else 0
        dist_sl = a.get('distance_to_sl_pct', 0)
        risk = a.get('risk_score', 0)

        lines.append(f"{icon:<6} {name:<10} {code:<8} {price:>8.2f} "
                     f"{pnl:>+6.2f}% {sl_price:>8.2f} {dist_sl:>+7.2f}% {risk:>4.0f}")

    lines.append("")

    # 需要关注的标的
    urgent = [a for a in alerts if a.get('alert_level') in ('triggered', 'critical')]
    if urgent:
        lines.append("需要立即关注:")
        for a in urgent:
            lines.append(f"  {a['name']}({a['code']}): {a.get('action_suggestion', '')}")

    # 综合评估
    valid_scores = [a.get('risk_score', 0) for a in alerts if 'risk_score' in a]
    if valid_scores:
        avg = sum(valid_scores) / len(valid_scores)
        if avg >= 60:
            overall = "高风险 — 组合面临较大回撤压力"
        elif avg >= 30:
            overall = "中等风险 — 部分标的需密切关注"
        else:
            overall = "低风险 — 组合运行在安全范围内"
        lines.append(f"\n综合评估: {overall} (平均风险: {avg:.1f}/100)")

    lines.append("\n" + "=" * 70)
    return '\n'.join(lines)
