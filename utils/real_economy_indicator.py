# -*- coding: utf-8 -*-
"""
实体经济综合判断指标 — 基于工业品价格的宏观晴雨表

来源：整合自 E:\各种PY程序\实体经济判断指标.py

核心逻辑：
  使用7种工业品（瓦楞纸/废纸/水泥/螺纹钢/铜/铝/白酒）的价格数据，
  通过Z-Score归一化构建综合经济热度指标。

设计理念：
  纸价 → 消费活跃度（瓦楞纸关联30+行业，废纸反映回收景气）
  水泥 → 当日需求强度（不可库存，比PMI更实时）
  螺纹钢 → 房地产+基建周期
  铜/铝 → 制造业+新能源+AI算力三重需求
  高端白酒 → 商务活动+财富效应

使用方式：
  from utils.real_economy_indicator import RealEconomyIndicator
  
  indicator = RealEconomyIndicator()
  result = indicator.calculate_indicator(price_data)
  trend = indicator.analyze_trend(history_data)
"""

import numpy as np
from datetime import datetime
from typing import Dict, List, Optional, Tuple


class RealEconomyIndicator:
    """实体经济综合判断指标 — 基于7种工业品价格的宏观体温计"""

    # 五级经济状态
    LEVELS = {
        (90, 100): {'level': '过热', 'description': '警惕通胀风险，建议防御配置'},
        (75, 90):  {'level': '偏热', 'description': '经济活跃，可适度进攻'},
        (50, 75):  {'level': '正常', 'description': '经济平稳，保持均衡配置'},
        (30, 50):  {'level': '偏冷', 'description': '经济疲软，关注防御板块'},
        (0, 30):   {'level': '衰退', 'description': '经济衰退，建议大幅减仓'},
    }

    # 各指标名称映射
    INDICATOR_NAMES = {
        'paper': '瓦楞纸价格指数',
        'recycled_paper': '废纸价格指数',
        'cement': '水泥价格指数',
        'rebar': '螺纹钢价格指数',
        'copper': '铜价指数',
        'aluminum': '铝价指数',
        'white_spirit': '高端白酒价格指数',
    }

    def __init__(self, weights: Optional[Dict[str, float]] = None):
        """
        Args:
            weights: 自定义权重，默认使用均衡权重。
                     例如: {'paper': 0.25, 'cement': 0.15, ...}
        """
        self.weights = weights or {
            'paper': 0.25,
            'recycled_paper': 0.15,
            'cement': 0.15,
            'rebar': 0.15,
            'copper': 0.10,
            'aluminum': 0.08,
            'white_spirit': 0.12,
        }

    def _normalize(self, current: float, mean: float, std: float) -> float:
        """
        Z-Score归一化到 [0, 100] 区间。
        均值 → 50，每偏离1个标准差 → ±15分。
        """
        if std == 0:
            return 50.0
        z = (current - mean) / std
        return max(0.0, min(100.0, 50.0 + z * 15.0))

    def calculate_indicator(self, data: Dict[str, Dict[str, float]]) -> Dict:
        """
        计算综合实体经济指标。

        Args:
            data: {
                'paper': {'current': 3200, 'avg': 3500, 'std': 300},
                'cement': {'current': 380, 'avg': 420, 'std': 50},
                ...
            }

        Returns:
            {
                'overall_score': float,   # 综合评分 [0-100]
                'level': str,             # 经济状态
                'description': str,       # 配置建议
                'sub_scores': {indicator_name: score},
                'timestamp': str,
            }
        """
        scores = {}
        total_weight = 0.0

        for key, weight in self.weights.items():
            if key not in data:
                continue
            item = data[key]
            score = self._normalize(item['current'], item['avg'], item['std'])
            scores[key] = score * weight
            total_weight += weight

        if total_weight == 0:
            return {'overall_score': 50.0, 'level': '数据不足',
                    'description': '缺少有效数据', 'sub_scores': {},
                    'timestamp': datetime.now().isoformat()}

        weighted_score = sum(scores.values()) / total_weight

        # 确定经济状态
        level_info = None
        for (low, high), info in self.LEVELS.items():
            if low <= weighted_score < high:
                level_info = info
                break
        if level_info is None:
            level_info = self.LEVELS[(0, 30)]

        return {
            'overall_score': round(weighted_score, 2),
            'level': level_info['level'],
            'description': level_info['description'],
            'sub_scores': {k: round(v / self.weights.get(k, 1), 2) for k, v in scores.items()},
            'timestamp': datetime.now().isoformat(),
        }

    def analyze_trend(self, history_data: Dict[str, Dict]) -> Dict:
        """
        分析历史趋势。

        Args:
            history_data: {'2026-01': price_data, '2026-02': price_data, ...}

        Returns:
            {'trend': '上升'|'下降'|'平稳', 'recent_score': float,
             'history': [{'date': str, 'score': float, 'level': str}, ...]}
        """
        results = []
        for date_str, data in sorted(history_data.items()):
            result = self.calculate_indicator(data)
            result['date'] = date_str
            results.append(result)

        trend = '数据不足'
        if len(results) >= 2:
            change = results[-1]['overall_score'] - results[-2]['overall_score']
            if change > 3:
                trend = '上升'
            elif change < -3:
                trend = '下降'
            else:
                trend = '平稳'

        return {
            'trend': trend,
            'recent_score': results[-1]['overall_score'] if results else None,
            'history': [{
                'date': r['date'],
                'score': r['overall_score'],
                'level': r['level']
            } for r in results],
        }

    def to_risk_signal(self, score: float) -> Tuple[str, float]:
        """
        将经济评分转换为风险信号，供 enhanced_risk_manager 使用。

        Returns:
            (regime: str, multiplier: float)
            - 过热: 防御信号，multiplier=0.7
            - 偏热: 进攻信号，multiplier=1.1
            - 正常: 中性，multiplier=1.0
            - 偏冷: 谨慎，multiplier=0.85
            - 衰退: 避险，multiplier=0.5
        """
        if score >= 90:
            return ('overheat', 0.70)
        elif score >= 75:
            return ('warm', 1.10)
        elif score >= 50:
            return ('normal', 1.00)
        elif score >= 30:
            return ('cool', 0.85)
        else:
            return ('recession', 0.50)

    def generate_report(self, data: Dict, history_data: Optional[Dict] = None) -> str:
        """生成格式化文本报告。"""
        result = self.calculate_indicator(data)

        lines = [
            "实体经济综合判断指标报告",
            "=" * 50,
            f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "=" * 50,
            "",
            f"【综合评分】: {result['overall_score']} 分",
            f"【经济状态】: {result['level']}",
            f"【配置建议】: {result['description']}",
            "",
            "分项指标评分:",
            "-" * 30,
        ]

        for key, name in self.INDICATOR_NAMES.items():
            if key in result['sub_scores']:
                w = self.weights.get(key, 0) * 100
                lines.append(f"  {name}: {result['sub_scores'][key]:.2f}分 (权重: {w:.0f}%)")

        if history_data:
            trend = self.analyze_trend(history_data)
            lines.extend([
                "",
                f"【趋势分析】: {trend['trend']}",
                f"【最新评分】: {trend['recent_score']}分",
            ])

        lines.extend([
            "",
            "=" * 50,
            "风险提示: 本指标仅供参考，不构成投资建议",
            "=" * 50,
        ])

        return '\n'.join(lines)
