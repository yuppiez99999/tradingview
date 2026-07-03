# -*- coding: utf-8 -*-
"""
ETF资金流向监控与信号生成

来源：整合自 E:\各种PY程序\实时ETF资金流向.py

核心功能：
1. 12只国家队ETF资金流向监控
2. 三级信号检测（50亿/10亿/2亿）
3. ETF→个股→板块映射链
4. 板块轮动资金汇总
5. 动态仓位建议

使用方式：
  from utils.etf_flow_monitor import ETFFlowMonitor
  
  monitor = ETFFlowMonitor()
  signals = monitor.detect_signals(flow_data)
  plan = monitor.generate_trading_plan(signals)
"""

from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime
import logging

logger = logging.getLogger('etf_flow')


@dataclass
class FlowSignal:
    """资金流向信号"""
    etf_code: str
    etf_name: str
    category: str
    net_flow: float          # 净流入（亿元）
    level: str               # high/medium/low/none
    sector: str              # 映射板块
    direction: str = ""      # 流入/流出
    related_stocks: List[str] = field(default_factory=list)
    position_advice: str = ""


class ETFFlowMonitor:
    """ETF资金流向监控器"""

    # 国家队重点监控ETF
    NATIONAL_TEAM_ETFS = [
        {"code": "510050", "name": "上证50ETF",       "category": "宽基"},
        {"code": "510300", "name": "沪深300ETF",      "category": "宽基"},
        {"code": "510500", "name": "中证500ETF",      "category": "宽基"},
        {"code": "588000", "name": "科创50ETF",       "category": "成长科技"},
        {"code": "512760", "name": "半导体ETF",       "category": "科技主题"},
        {"code": "512880", "name": "证券ETF",         "category": "金融主题"},
        {"code": "512800", "name": "银行ETF",         "category": "金融主题"},
        {"code": "518880", "name": "黄金ETF",         "category": "避险资产"},
        {"code": "512170", "name": "医疗ETF",         "category": "医药主题"},
        {"code": "515030", "name": "新能源车ETF",     "category": "新能源主题"},
        {"code": "159915", "name": "创业板ETF",       "category": "成长科技"},
        {"code": "512100", "name": "中证1000ETF",     "category": "小盘风格"},
    ]

    # 信号阈值（亿元）
    THRESHOLDS = {
        "high": 50.0,    # 强信号
        "medium": 10.0,  # 中信号
        "low": 2.0,      # 关注信号
    }

    # ETF → 板块 → 个股票池映射
    ETF_SECTOR_MAP = {
        "510050": {"sector": "上证50大盘蓝筹", "stocks": ["600036", "601318", "600519", "600276", "601166"]},
        "510300": {"sector": "沪深300核心资产", "stocks": ["600036", "600276", "601088", "002648", "600346"]},
        "510500": {"sector": "中盘成长", "stocks": ["002493", "000301", "601233", "603225", "000059"]},
        "588000": {"sector": "科创板科技", "stocks": ["688041", "300308", "688017", "002371"]},
        "512760": {"sector": "半导体", "stocks": ["688041", "002371", "300308"]},
        "512880": {"sector": "券商", "stocks": ["600030", "601211", "600837"]},
        "512800": {"sector": "银行", "stocks": ["600036", "601166", "000001"]},
        "518880": {"sector": "黄金避险", "stocks": ["600489", "601899", "600547"]},
        "512170": {"sector": "医疗医药", "stocks": ["600276", "300760", "603259"]},
        "515030": {"sector": "新能源", "stocks": ["300750", "300274", "002129"]},
        "159915": {"sector": "创业板成长", "stocks": ["300750", "300274", "300760"]},
        "512100": {"sector": "中证1000小盘", "stocks": ["002493", "000301", "601233"]},
    }

    def __init__(self, thresholds: Optional[Dict[str, float]] = None):
        """
        Args:
            thresholds: 自定义信号阈值，默认 {'high': 50, 'medium': 10, 'low': 2}
        """
        self.thresholds = thresholds or self.THRESHOLDS

    def detect_signals(self, flow_data: Dict[str, float]) -> List[FlowSignal]:
        """
        检测资金流向信号。

        Args:
            flow_data: {etf_code: net_flow_亿}，例如 {'510050': 55.2, '588000': -3.1}

        Returns:
            信号列表（仅包含有信号的ETF）
        """
        signals = []

        for etf in self.NATIONAL_TEAM_ETFS:
            code = etf['code']
            flow = flow_data.get(code, 0)

            # 确定信号级别
            level = self._classify_flow(abs(flow))

            if level == 'none':
                continue

            # 方向
            direction = '流入' if flow > 0 else '流出'

            # 板块映射
            sector_info = self.ETF_SECTOR_MAP.get(code, {})
            sector = sector_info.get('sector', etf['category'])
            stocks = sector_info.get('stocks', [])

            signals.append(FlowSignal(
                etf_code=code,
                etf_name=etf['name'],
                category=etf['category'],
                net_flow=round(flow, 2),
                level=level,
                direction=direction,
                sector=sector,
                related_stocks=stocks,
                position_advice=self._get_position_advice(level, flow > 0),
            ))

        # 按净流入绝对值排序
        signals.sort(key=lambda s: abs(s.net_flow), reverse=True)
        return signals

    def _classify_flow(self, abs_flow: float) -> str:
        """分类资金流强度"""
        if abs_flow >= self.thresholds['high']:
            return 'high'
        elif abs_flow >= self.thresholds['medium']:
            return 'medium'
        elif abs_flow >= self.thresholds['low']:
            return 'low'
        return 'none'

    def _get_position_advice(self, level: str, is_inflow: bool) -> str:
        """根据信号级别和方向给出仓位建议"""
        if not is_inflow:
            return "关注资金流出，考虑减仓"

        advice = {
            'high': "强流入信号！建议增加该板块配置，关注个股机会",
            'medium': "中等流入，可适当参与，等待确认",
            'low': "轻度流入，保持关注，暂不操作",
        }
        return advice.get(level, "")

    def aggregate_by_sector(self, signals: List[FlowSignal]) -> Dict[str, Dict]:
        """
        按板块汇总资金流向。

        Returns:
            {sector_name: {total_flow, etf_count, stocks, signal_level}}
        """
        sectors = {}
        for s in signals:
            sec = s.sector
            if sec not in sectors:
                sectors[sec] = {
                    'total_flow': 0.0,
                    'etf_count': 0,
                    'stocks': set(),
                    'signals': [],
                    'max_level': 'none',
                }

            sectors[sec]['total_flow'] += s.net_flow
            sectors[sec]['etf_count'] += 1
            sectors[sec]['stocks'].update(s.related_stocks)
            sectors[sec]['signals'].append(s)

            # 保留最高级别信号
            level_order = {'none': 0, 'low': 1, 'medium': 2, 'high': 3}
            if level_order.get(s.level, 0) > level_order.get(sectors[sec]['max_level'], 0):
                sectors[sec]['max_level'] = s.level

        # 转换 set → list
        for sec in sectors:
            sectors[sec]['stocks'] = list(sectors[sec]['stocks'])
            sectors[sec]['total_flow'] = round(sectors[sec]['total_flow'], 2)

        # 按总流量排序
        return dict(sorted(sectors.items(),
                          key=lambda x: abs(x[1]['total_flow']), reverse=True))

    def generate_trading_plan(self, signals: List[FlowSignal],
                               current_positions: Optional[Dict[str, float]] = None) -> Dict:
        """
        基于资金流向信号生成交易计划。

        Args:
            signals: 检测到的信号列表
            current_positions: 当前持仓 {code: weight}

        Returns:
            {
                'overall_signal': str,     # 整体信号
                'sector_rotation': [...],  # 板块轮动建议
                'stock_actions': [...],    # 个股操作建议
                'risk_warnings': [...],    # 风险提示
                'timestamp': str,
            }
        """
        if not signals:
            return {
                'overall_signal': 'neutral',
                'sector_rotation': [],
                'stock_actions': [],
                'risk_warnings': [],
                'timestamp': datetime.now().isoformat(),
                'note': '无显著资金流向信号',
            }

        # 板块汇总
        sectors = self.aggregate_by_sector(signals)

        # 整体信号
        high_count = sum(1 for s in signals if s.level == 'high')
        inflow_count = sum(1 for s in signals if s.net_flow > 0)

        if high_count >= 3:
            overall = 'strong_bullish'
        elif high_count >= 1 or inflow_count >= len(signals) * 0.7:
            overall = 'bullish'
        elif inflow_count <= len(signals) * 0.3:
            overall = 'bearish'
        else:
            overall = 'neutral'

        # 板块轮动建议
        rotation = []
        for sec_name, sec_data in sectors.items():
            if sec_data['max_level'] in ('high', 'medium') and sec_data['total_flow'] > 0:
                rotation.append({
                    'sector': sec_name,
                    'action': '增配' if sec_data['total_flow'] > 20 else '关注',
                    'total_flow': sec_data['total_flow'],
                    'stocks': sec_data['stocks'][:5],
                })

        # 个股操作建议
        stock_actions = []
        for s in signals:
            if s.level in ('high', 'medium') and s.net_flow > 0:
                for stock in s.related_stocks[:3]:
                    stock_actions.append({
                        'code': stock,
                        'action': 'buy' if s.level == 'high' else 'watch',
                        'reason': f"{s.etf_name} {s.level}级别{s.direction} {abs(s.net_flow):.1f}亿",
                    })

        # 去重
        seen = set()
        unique_actions = []
        for a in stock_actions:
            if a['code'] not in seen:
                seen.add(a['code'])
                unique_actions.append(a)
        stock_actions = unique_actions[:10]

        # 风险提示
        warnings = []
        for s in signals:
            if s.net_flow < 0 and s.level in ('high', 'medium'):
                warnings.append(f"{s.etf_name}({s.sector})大额流出 {abs(s.net_flow):.1f}亿，注意风险")

        return {
            'overall_signal': overall,
            'sector_rotation': rotation,
            'stock_actions': stock_actions,
            'risk_warnings': warnings,
            'signal_count': len(signals),
            'high_signals': high_count,
            'timestamp': datetime.now().isoformat(),
        }

    def generate_report(self, flow_data: Dict[str, float]) -> str:
        """生成资金流向格式化报告"""
        signals = self.detect_signals(flow_data)
        plan = self.generate_trading_plan(signals)
        sectors = self.aggregate_by_sector(signals)

        lines = [
            "=" * 70,
            "ETF资金流向监控报告",
            f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "=" * 70,
            "",
            f"整体信号: {plan['overall_signal']}",
            f"信号数量: {plan['signal_count']} (高:{plan['high_signals']})",
            "",
            "--- 板块资金流向 ---",
        ]

        for sec_name, sec_data in sectors.items():
            flow_str = f"+{sec_data['total_flow']:.1f}" if sec_data['total_flow'] > 0 else f"{sec_data['total_flow']:.1f}"
            lines.append(f"  {sec_name}: {flow_str}亿 [{sec_data['max_level']}]")
            lines.append(f"    相关标的: {', '.join(sec_data['stocks'][:5])}")

        if plan['risk_warnings']:
            lines.append("")
            lines.append("--- 风险提示 ---")
            for w in plan['risk_warnings']:
                lines.append(f"  ! {w}")

        lines.append("\n" + "=" * 70)
        return '\n'.join(lines)
