 # -*- coding: utf-8 -*-
"""
500万持仓建仓计划生成器
========================
基于康波周期(第六轮复苏→繁荣)+十五五规划 双重叠加策略

目标配置：13只标的，高端制造为核心
建仓起点：2026-07-06 (周一，下一交易日)
建仓周期：3个月 (2026年7月 - 2026年9月)

配置逻辑：
  高端制造 63.7% - 康波复苏期核心驱动(AI/半导体/高端装备/新能源)
  防御 17.0%   - 医药+国债作为安全垫
  资源 12.4%   - 有色+黄金通胀对冲
  顺周期 6.4%  - 能源龙头周期配置
  货基 0.5%    - 流动性储备
"""

import os
import json
from datetime import datetime, timedelta
from typing import Dict, List

# ============================================================
# 核心配置
# ============================================================

TOTAL_CAPITAL = 5_000_000  # 500万

# 起始日期：下一个交易日
# 今天 2026-07-03(周五) → 下一个交易日 2026-07-06(周一)
START_DATE = datetime(2026, 7, 6)
END_DATE = datetime(2026, 9, 30)

# ============================================================
# 目标配置权重（来自增强版交易计划 - 康波+十五五优化）
# ============================================================

TARGET_PORTFOLIO = {
    # ===== 高端制造板块 (63.7%) =====
    '588000': {'name': '科创50ETF华夏',     'type': 'ETF',  'risk': '高', 'style': '高端制造',
               'weight': 0.1375, 'est_price': 1.05,  'lots': 100,
               'reason': 'AI/科技核心指数，十五五规划重点方向'},
    '512480': {'name': '半导体ETF国泰',      'type': 'ETF',  'risk': '高', 'style': '高端制造',
               'weight': 0.1317, 'est_price': 1.38,  'lots': 100,
               'reason': 'AI算力硬件核心，康波第六轮技术底座'},
    '516160': {'name': '高端装备ETF南方',    'type': 'ETF',  'risk': '高', 'style': '高端制造',
               'weight': 0.1317, 'est_price': 1.12,  'lots': 100,
               'reason': '十五五重点产业，制造业升级主线'},
    '515030': {'name': '新能源车ETF华夏',    'type': 'ETF',  'risk': '高', 'style': '高端制造',
               'weight': 0.1260, 'est_price': 1.55,  'lots': 100,
               'reason': '新能源产业链，十五五绿色转型战略'},
    '159915': {'name': '创业板ETF易方达',    'type': 'ETF',  'risk': '高', 'style': '高端制造',
               'weight': 0.1150, 'est_price': 2.15,  'lots': 100,
               'reason': '成长风格核心敞口，创新企业集中地'},

    # ===== 防御板块 (17.0%) =====
    '159992': {'name': '创新药ETF银华',      'type': 'ETF',  'risk': '高', 'style': '防御',
               'weight': 0.0947, 'est_price': 0.92,  'lots': 100,
               'reason': '生物医药创新，十五五民生健康重点'},
    '512010': {'name': '医药ETF易方达',      'type': 'ETF',  'risk': '中', 'style': '防御',
               'weight': 0.0664, 'est_price': 0.58,  'lots': 100,
               'reason': '医药行业宽基配置，防御+成长双属性'},
    '511260': {'name': '十年国债ETF国泰',    'type': '债券', 'risk': '低', 'style': '防御',
               'weight': 0.0050, 'est_price': 102.50, 'lots': 10,
               'reason': '利率债配置，极端行情安全垫'},
    '511520': {'name': '政金债ETF富国',      'type': '债券', 'risk': '低', 'style': '防御',
               'weight': 0.0037, 'est_price': 101.20, 'lots': 10,
               'reason': '政策性金融债，信用风险极低'},

    # ===== 资源板块 (12.4%) =====
    '512400': {'name': '有色金属ETF南方',    'type': 'ETF',  'risk': '高', 'style': '资源',
               'weight': 0.0929, 'est_price': 1.18,  'lots': 100,
               'reason': '康波繁荣期预期，商品超级周期受益'},
    '518880': {'name': '黄金ETF华安',        'type': '商品', 'risk': '中', 'style': '资源',
               'weight': 0.0307, 'est_price': 5.85,  'lots': 100,
               'reason': '通胀对冲+地缘风险避险，组合稳定器'},

    # ===== 顺周期板块 (6.4%) =====
    '601088': {'name': '中国神华',           'type': '个股', 'risk': '中', 'style': '顺周期',
               'weight': 0.0638, 'est_price': 38.50, 'lots': 100,
               'reason': '能源安全龙头，高股息+顺周期双重属性'},

    # ===== 货币/流动性 (0.4%) =====
    '511360': {'name': '短融ETF海富通',      'type': '货币', 'risk': '低', 'style': '防御',
               'weight': 0.0010, 'est_price': 100.05, 'lots': 10,
               'reason': '现金管理工具，闲置资金获取货币收益'},
}

# 权重验证
_weight_sum = sum(v['weight'] for v in TARGET_PORTFOLIO.values())
assert abs(_weight_sum - 1.0) < 0.01, f"权重合计应为100%，实际为{_weight_sum:.4f}"


# ============================================================
# 分阶段建仓计划
# ============================================================

BUILD_PHASES = [
    {
        'phase': 1, 'name': '第一阶段-底仓建立',
        'start': datetime(2026, 7, 6),    'duration_days': 10,
        'capital_ratio': 0.35,            'desc': '周一开盘建立核心底仓，关注市场流动性',
        'strategy': '优先建立高端制造核心仓位（科创50、半导体、高端装备），同步配置黄金ETF和中国神华作为稳定器',
    },
    {
        'phase': 2, 'name': '第二阶段-配置完善',
        'start': datetime(2026, 7, 20),   'duration_days': 15,
        'capital_ratio': 0.30,            'desc': '完成新能源车、创业板、有色金属配置',
        'strategy': '利用月中波动窗口分批加仓，关注大宗商品价格趋势，择机增加资源板块',
    },
    {
        'phase': 3, 'name': '第三阶段-防御补充',
        'start': datetime(2026, 8, 10),   'duration_days': 15,
        'capital_ratio': 0.20,            'desc': '配置医药ETF和创新药ETF，完成防御板块',
        'strategy': '结合中报披露窗口，优选医药板块回调时点建仓，配置债券类资产',
    },
    {
        'phase': 4, 'name': '第四阶段-最终调整',
        'start': datetime(2026, 9, 1),    'duration_days': 20,
        'capital_ratio': 0.15,            'desc': '微调各板块权重，完成建仓',
        'strategy': '审视前三阶段执行偏差，补齐偏离标的，配置短融ETF管理剩余现金',
    },
]


# ============================================================
# 风控参数
# ============================================================

RISK_PARAMS = {
    'stop_loss': {
        'high_risk': -0.15,      # 高风险标的(ETF/个股) 15%止损
        'medium_risk': -0.12,    # 中风险标的 12%止损
        'low_risk': -0.05,       # 低风险标的(债券) 5%止损
    },
    'position_limits': {
        'max_single_weight': 0.15,         # 单一标的不超过15%
        'max_style_concentration': 0.65,   # 单一风格不超过65%
    },
    'portfolio_limits': {
        'max_drawdown_warning': 0.06,      # 回撤6%预警
        'max_drawdown_action': 0.08,       # 回撤8%减仓
        'max_drawdown_stop': 0.12,         # 回撤12%全面风控
    },
    'rebalance': {
        'threshold': 0.05,                 # 偏离5%触发再平衡
        'frequency': 'quarterly',          # 季度再平衡
        'review_date': '2026-09-30',       # 首次全面复盘
    },
}

# ============================================================
# 建仓执行规则
# ============================================================

EXECUTION_RULES = {
    'daily_timing': {
        'morning_window': ('09:30', '10:30'),   # 上午执行窗口
        'afternoon_window': ('14:00', '14:30'), # 下午执行窗口
        'split_ratio': 0.50,                      # 上下半场各50%
    },
    'price_rules': {
        'discount_buy': 0.10,     # 低于预估10%加配20%
        'normal_buy': 0.05,       # 正常区间
        'premium_skip': 0.10,     # 高于预估10%暂停该批次
    },
    'volume_limits': {
        'max_daily_ratio': 0.10,  # 单日不超过标的日均成交量10%
        'min_lot_size': True,     # 遵循最小交易单位
    },
    'cash_management': {
        'idle_instrument': '511360',  # 闲置资金放短融ETF
        'min_cash_reserve': 50000,    # 最低现金保留5万
    },
}


# ============================================================
# 计算引擎
# ============================================================

# 中文风险等级 → 英文键名映射
RISK_KEY_MAP = {
    '高': 'high_risk',
    '中': 'medium_risk',
    '低': 'low_risk',
}

def compute_position_plan() -> Dict:
    """计算每标的完整建仓计划"""
    plan = {}

    for code, cfg in TARGET_PORTFOLIO.items():
        target_amount = TOTAL_CAPITAL * cfg['weight']
        est_price = cfg['est_price']
        lots = cfg['lots']
        total_shares = int(target_amount / est_price / lots) * lots
        actual_amount = total_shares * est_price

        phases_detail = []
        cumulative_ratio = 0.0
        for phase in BUILD_PHASES:
            phase_ratio = phase['capital_ratio']
            phase_target = target_amount * phase_ratio
            phase_shares = int(phase_target / est_price / lots) * lots
            phase_amount = phase_shares * est_price
            cumulative_ratio += phase_ratio

            phases_detail.append({
                'phase': phase['phase'],
                'name': phase['name'],
                'start': phase['start'].strftime('%Y-%m-%d'),
                'capital_ratio': phase_ratio,
                'target_amount': round(phase_target, 0),
                'shares': phase_shares,
                'actual_amount': round(phase_amount, 0),
                'cumulative_ratio': round(cumulative_ratio, 3),
            })

        plan[code] = {
            'code': code,
            'name': cfg['name'],
            'type': cfg['type'],
            'risk': cfg['risk'],
            'style': cfg['style'],
            'target_weight': cfg['weight'],
            'target_amount': round(target_amount, 0),
            'est_price': est_price,
            'total_shares': total_shares,
            'actual_amount': round(actual_amount, 0),
            'reason': cfg['reason'],
            'stop_loss': RISK_PARAMS['stop_loss'][RISK_KEY_MAP[cfg['risk']]],
            'phases': phases_detail,
        }

    return plan


def compute_style_summary(plan: Dict) -> Dict:
    """按风格汇总"""
    summary = {}
    for code, info in plan.items():
        style = info['style']
        if style not in summary:
            summary[style] = {'amount': 0, 'weight': 0, 'codes': [], 'risk_distribution': {}}
        summary[style]['amount'] += info['actual_amount']
        summary[style]['weight'] += info['target_weight']
        summary[style]['codes'].append(code)
        risk = info['risk']
        summary[style]['risk_distribution'][risk] = summary[style]['risk_distribution'].get(risk, 0) + info['actual_amount']
    return summary


def compute_phase_summary(plan: Dict) -> List[Dict]:
    """按阶段汇总"""
    summaries = []
    for i, phase in enumerate(BUILD_PHASES):
        phase_capital = TOTAL_CAPITAL * phase['capital_ratio']
        phase_assets = 0
        phase_codes = []

        for code, info in plan.items():
            if i < len(info['phases']):
                phase_codes.append({
                    'code': code,
                    'name': info['name'],
                    'shares': info['phases'][i]['shares'],
                    'amount': info['phases'][i]['actual_amount'],
                })
                phase_assets += info['phases'][i]['actual_amount']

        summaries.append({
            'phase': phase['phase'],
            'name': phase['name'],
            'start': phase['start'].strftime('%Y-%m-%d'),
            'duration_days': phase['duration_days'],
            'capital_ratio': phase['capital_ratio'],
            'capital_amount': round(phase_capital, 0),
            'asset_count': len([c for c in phase_codes if c['shares'] > 0]),
            'total_actual': round(phase_assets, 0),
            'strategy': phase['strategy'],
            'assets': phase_codes,
        })

    return summaries


# ============================================================
# 报告生成
# ============================================================

def generate_markdown_report(plan: Dict, style_summary: Dict, phase_summary: List[Dict]) -> str:
    """生成完整Markdown建仓计划报告"""

    lines = []
    lines.append('# 500万持仓建仓计划')
    lines.append('')
    lines.append('> **策略**: 康波周期(第六轮复苏→繁荣) × 十五五规划双重叠加')
    lines.append('> **总资金**: 5,000,000 元 (500万)')
    lines.append('> **建仓起点**: 2026年7月6日 (周一，下一交易日)')
    lines.append('> **建仓完成**: 2026年9月30日')
    lines.append('> **建仓周期**: 约3个月 (4个阶段)')
    lines.append('> **核心主题**: AI/半导体/高端制造/新能源/生物医药')
    lines.append('> **生成时间**: ' + datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    lines.append('')

    # ===== 一、策略概要 =====
    lines.append('## 一、策略逻辑')
    lines.append('')
    lines.append('本建仓计划基于两大宏观周期的高度同频共振：')
    lines.append('')
    lines.append('1. **康波第六轮周期**：由AI/算力驱动的新一轮技术革命，当前处于复苏期75%进度，')
    lines.append('   预计2027年前后转入繁荣期。根据历史规律，复苏→繁荣转换期是权益资产最佳配置窗口。')
    lines.append('2. **十五五规划(2026-2030)**：七大战略方向(AI/半导体/高端制造/新能源/数字经济/生物医药)')
    lines.append('   与康波驱动力完全重叠，政策周期与技术周期形成正向合力。')
    lines.append('3. **策略选择**：进取型配置，高端制造板块超配至63.7%，充分捕捉康波复苏期的科技成长红利。')
    lines.append('')

    # ===== 二、资产配置总览 =====
    lines.append('## 二、资产配置总览 (500万)')
    lines.append('')
    lines.append('| 代码 | 名称 | 类型 | 风险 | 风格 | 目标权重 | 目标金额 | 预估单价 | 预估股数 |')
    lines.append('|:-----|:-----|:-----|:-----|:-----|--------:|--------:|--------:|--------:|')

    # 按权重降序
    sorted_codes = sorted(
        TARGET_PORTFOLIO.keys(),
        key=lambda c: -TARGET_PORTFOLIO[c]['weight']
    )

    total_amount = 0
    for code in sorted_codes:
        info = plan[code]
        total_amount += info['actual_amount']
        lines.append('| {} | {} | {} | {} | {} | {:.2f}% | {:,.0f} | {:.2f} | {:,.0f} |'.format(
            code, info['name'], info['type'], info['risk'], info['style'],
            info['target_weight'] * 100, info['target_amount'],
            info['est_price'], info['total_shares'],
        ))

    # 汇总行
    lines.append('| **合计** | **13只标的** | | | | **100%** | **{:,.0f}** | | |'.format(total_amount))
    lines.append('')

    # 风格汇总
    lines.append('### 风格板块分布')
    lines.append('')
    lines.append('| 风格 | 配置金额 | 占比 | 标的数 | 风险分布 |')
    lines.append('|:-----|--------:|-----:|------:|:---------|')

    for style in ['高端制造', '防御', '资源', '顺周期']:
        if style in style_summary:
            s = style_summary[style]
            risk_str = ', '.join(
                '{}: {:,.0f}'.format(r, amt)
                for r, amt in sorted(s['risk_distribution'].items())
            )
            lines.append('| {} | {:,.0f} | {:.1f}% | {} | {} |'.format(
                style, s['amount'], s['weight'] * 100, len(s['codes']), risk_str))

    lines.append('')

    # ===== 三、分阶段建仓计划 =====
    lines.append('## 三、分阶段建仓计划')
    lines.append('')
    lines.append('建仓周期约3个月(2026年7月-9月)，分4个阶段执行：')
    lines.append('')

    for ps in phase_summary:
        lines.append('### 第{}阶段：{}'.format(ps['phase'], ps['name']))
        lines.append('')
        lines.append('- **时间**: {} ({}个交易日)'.format(ps['start'], ps['duration_days']))
        lines.append('- **建仓比例**: {:.0f}% ({:,.0f} 元)'.format(
            ps['capital_ratio'] * 100, ps['capital_amount']))
        lines.append('- **策略**: {}'.format(ps['strategy']))
        lines.append('')

        lines.append('| 代码 | 名称 | 买入股数 | 预估金额 |')
        lines.append('|:-----|:-----|--------:|--------:|')

        for asset in ps['assets']:
            if asset['shares'] > 0:
                lines.append('| {} | {} | {:,.0f} | {:,.0f} |'.format(
                    asset['code'], asset['name'], asset['shares'], asset['amount']))
        lines.append('| **合计** | **{}** | | **{:,.0f}** |'.format(
            ps['asset_count'], ps['total_actual']))
        lines.append('')

    # ===== 四、每标的完整建仓路线 =====
    lines.append('## 四、每标的完整建仓路线')
    lines.append('')

    for code in sorted_codes:
        info = plan[code]
        lines.append('### {} {}'.format(code, info['name']))
        lines.append('')
        lines.append('- **类型**: {} | **风险**: {} | **风格**: {}'.format(
            info['type'], info['risk'], info['style']))
        lines.append('- **目标权重**: {:.1f}% | **目标金额**: {:,.0f} 元'.format(
            info['target_weight'] * 100, info['target_amount']))
        lines.append('- **预估价格**: {:.2f} | **总股数**: {:,.0f}'.format(
            info['est_price'], info['total_shares']))
        lines.append('- **止损线**: {:.0f}%'.format(info['stop_loss'] * 100))
        lines.append('- **配置理由**: {}'.format(info['reason']))
        lines.append('')

        lines.append('| 阶段 | 时间 | 比例 | 买入股数 | 金额 | 累计比例 |')
        lines.append('|:-----|:-----|-----:|--------:|------:|--------:|')

        for ph in info['phases']:
            lines.append('| {} | {} | {:.0f}% | {:,.0f} | {:,.0f} | {:.0f}% |'.format(
                ph['phase'], ph['start'], ph['capital_ratio'] * 100,
                ph['shares'], ph['actual_amount'], ph['cumulative_ratio'] * 100))
        lines.append('')

    # ===== 五、资金使用计划 =====
    lines.append('## 五、资金使用时间线')
    lines.append('')

    cumulative_capital = 0
    lines.append('| 时间节点 | 阶段 | 投入资金 | 累计投入 | 累计比例 | 剩余现金 |')
    lines.append('|:---------|:-----|--------:|--------:|--------:|--------:|')

    for ps in phase_summary:
        cumulative_capital += ps['capital_amount']
        remaining = TOTAL_CAPITAL - cumulative_capital
        lines.append('| {} | {} | {:,.0f} | {:,.0f} | {:.0f}% | {:,.0f} |'.format(
            ps['start'], ps['name'], ps['capital_amount'],
            cumulative_capital, cumulative_capital / TOTAL_CAPITAL * 100, remaining))

    lines.append('')
    lines.append('> 未建仓资金存放于短融ETF(511360)或券商货币基金，获取约2%年化收益。')
    lines.append('')

    # ===== 六、执行规则 =====
    lines.append('## 六、每日执行规则')
    lines.append('')

    lines.append('### 交易时间窗口')
    lines.append('')
    lines.append('| 时段 | 时间 | 操作 | 比例 |')
    lines.append('|:-----|:-----|:-----|-----:|')
    lines.append('| 上午执行 | 09:30 - 10:30 | 当日买入计划的50% | 50% |')
    lines.append('| 下午执行 | 14:00 - 14:30 | 当日买入计划的50% | 50% |')
    lines.append('| 盘后确认 | 15:00 后 | 确认成交，记录成本 | - |')
    lines.append('')

    lines.append('### 价格判断规则')
    lines.append('')
    lines.append('| 条件 | 操作 |')
    lines.append('|:-----|:-----|')
    lines.append('| 现价 < 预估90% (低于区间) | 该批次增配20%，加大低吸力度 |')
    lines.append('| 预估90% ≤ 现价 ≤ 预估110% | 正常执行建仓计划 |')
    lines.append('| 现价 > 预估110% (突破区间) | 该批次暂停，资金延至下一批 |')
    lines.append('')

    # ===== 七、风险管理 =====
    lines.append('## 七、风险管理体系')
    lines.append('')

    lines.append('### 止损规则')
    lines.append('')
    lines.append('| 风险等级 | 止损线 | 适用标的 |')
    lines.append('|:---------|:------|:---------|')
    lines.append('| 高风险 | -15% | ' + ', '.join(
        info['name'] for info in plan.values() if info['risk'] == '高') + ' |')
    lines.append('| 中风险 | -12% | ' + ', '.join(
        info['name'] for info in plan.values() if info['risk'] == '中') + ' |')
    lines.append('| 低风险 | -5% | ' + ', '.join(
        info['name'] for info in plan.values() if info['risk'] == '低') + ' |')
    lines.append('')

    lines.append('### 组合层面风控')
    lines.append('')
    lines.append('| 预警级别 | 回撤阈值 | 应对措施 |')
    lines.append('|:---------|:--------|:---------|')
    lines.append('| 黄色预警 | 6% | 增加关注，不操作 |')
    lines.append('| 橙色预警 | 8% | 减仓至70%，增持短融ETF至30% |')
    lines.append('| 红色止损 | 12% | 清仓高风险标的，仅保留黄金+债券 |')
    lines.append('')

    lines.append('### 再平衡规则')
    lines.append('')
    lines.append('- 季度再平衡：每季末检查偏离度，超过5%则恢复目标权重')
    lines.append('- 首次复盘日：2026年9月30日，评估建仓完成效果')
    lines.append('- 年度复盘：2026年12月，结合康波阶段确认决定2027年调仓方向')
    lines.append('')

    # ===== 八、首批建仓清单(7月6日周一) =====
    lines.append('## 八、首批建仓 —— 2026年7月6日(周一) 关键执行')
    lines.append('')

    first_phase = phase_summary[0]
    lines.append('**优先执行标的** (第一阶段核心仓位)：')
    lines.append('')
    lines.append('| 优先级 | 代码 | 名称 | 买入股数 | 预估金额 | 风格 |')
    lines.append('|:-------|:-----|:-----|--------:|--------:|:-----|')

    priority_order = sorted(
        first_phase['assets'],
        key=lambda x: -x['amount']
    )
    for idx, asset in enumerate(priority_order, 1):
        if asset['shares'] > 0:
            info = plan[asset['code']]
            lines.append('| {} | {} | {} | {:,.0f} | {:,.0f} | {} |'.format(
                idx, asset['code'], asset['name'], asset['shares'], asset['amount'], info['style']))

    lines.append('')
    lines.append('> **小计**：首批建仓 {:,.0f} 元 ({:.0f}%)'.format(
        first_phase['total_actual'], first_phase['capital_ratio'] * 100))
    lines.append('')

    lines.append('### 执行检查清单')
    lines.append('')
    lines.append('- [ ] 确认券商账户资金 500万元已到账')
    lines.append('- [ ] 确认所有ETF交易权限正常（科创板、创业板、商品ETF）')
    lines.append('- [ ] 09:20 查看盘前集合竞价，确认各标的开盘参考价')
    lines.append('- [ ] 09:30-10:30 执行上午批次（50%），优先大权重标的')
    lines.append('- [ ] 14:00-14:30 执行下午批次（50%）')
    lines.append('- [ ] 15:00 后确认成交并记录实际成本')
    lines.append('- [ ] 剩余资金转入短融ETF(511360)')
    lines.append('')

    # ===== 九、风险提示 =====
    lines.append('## 九、风险提示')
    lines.append('')
    lines.append('1. **集中度风险**: 高端制造风格占比超63%，若该方向遭遇系统性调整将显著影响组合')
    lines.append('2. **流动性风险**: 部分ETF在极端行情下可能出现流动性不足')
    lines.append('3. **模型风险**: 本计划基于康波周期理论假设，实际市场走势可能显著偏离预期')
    lines.append('4. **政策风险**: 十五五规划具体执行力度和政策调整可能影响相关标的预期')
    lines.append('5. **预测偏差**: 预估价格基于2026年6月中旬合理估算，实际成交价可能有偏差')
    lines.append('')
    lines.append('> **免责声明**: 本建仓计划仅供个人研究参考，不构成任何投资建议。市场有风险，投资需谨慎。')
    lines.append('')

    lines.append('---')
    lines.append('')
    lines.append('*建仓计划生成时间: {}*'.format(datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    lines.append('*数据来源: 康波周期+十五五规划组合优化引擎*')
    lines.append('*策略版本: 500万建仓计划 v1.0*')

    return '\n'.join(lines)


# ============================================================
# 保存
# ============================================================

def main():
    print('=' * 70)
    print('  500万持仓建仓计划生成器')
    print('  康波周期(第六轮复苏→繁荣) × 十五五规划(2026-2030)')
    print('=' * 70)

    # 计算
    plan = compute_position_plan()
    style_summary = compute_style_summary(plan)
    phase_summary = compute_phase_summary(plan)

    # 样式分布
    print('\n风格分布:')
    for style, s in style_summary.items():
        print('  {}: {:,.0f} 元 ({:.1f}%) - {}个标的'.format(
            style, s['amount'], s['weight'] * 100, len(s['codes'])))

    # 阶段汇总
    print('\n阶段资金分配:')
    for ps in phase_summary:
        print('  {}({}): {:,.0f} 元 ({:.0f}%)'.format(
            ps['name'], ps['start'], ps['capital_amount'], ps['capital_ratio'] * 100))

    # 生成报告
    report = generate_markdown_report(plan, style_summary, phase_summary)

    # 保存到工作目录和每日报告目录
    today_str = datetime.now().strftime('%Y%m%d')

    # 1. 保存到ZCodeProject
    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)))
    if not os.path.exists(output_dir):
        output_dir = os.getcwd()
    
    report_path = os.path.join(output_dir, '500万建仓计划_20260706.md')
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report)
    print('\n报告已保存: {}'.format(report_path))

    # 2. 保存JSON数据(用于程序化导入)
    json_path = os.path.join(output_dir, '500万建仓计划_20260706.json')
    json_data = {
        'metadata': {
            'total_capital': TOTAL_CAPITAL,
            'start_date': START_DATE.strftime('%Y-%m-%d'),
            'end_date': END_DATE.strftime('%Y-%m-%d'),
            'target_count': len(TARGET_PORTFOLIO),
            'build_phases': len(BUILD_PHASES),
            'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'strategy': '康波周期(第六轮复苏→繁荣) × 十五五规划(2026-2030)',
        },
        'target_portfolio': TARGET_PORTFOLIO,
        'position_plan': plan,
        'phase_summary': phase_summary,
        'style_summary': {k: {**v, 'risk_distribution': {r: round(a, 0) for r, a in v['risk_distribution'].items()}} for k, v in style_summary.items()},
        'risk_params': RISK_PARAMS,
        'execution_rules': EXECUTION_RULES,
    }
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(json_data, f, ensure_ascii=False, indent=2, default=str)
    print('JSON数据已保存: {}'.format(json_path))

    # 3. 打印摘要
    print('\n' + '=' * 70)
    print('建仓计划摘要')
    print('=' * 70)
    print('  总资金: {:,.0f} 元 (500万)'.format(TOTAL_CAPITAL))
    print('  标的数量: {} 只'.format(len(TARGET_PORTFOLIO)))
    print('  建仓起点: 2026-07-06 (周一，下一交易日)')
    print('  建仓完成: 2026-09-30')
    print('  建仓阶段: 4 个阶段')
    print('  核心风格: 高端制造 {:.1f}% | 防御 {:.1f}% | 资源 {:.1f}% | 顺周期 {:.1f}%'.format(
        style_summary.get('高端制造', {}).get('weight', 0) * 100,
        style_summary.get('防御', {}).get('weight', 0) * 100,
        style_summary.get('资源', {}).get('weight', 0) * 100,
        style_summary.get('顺周期', {}).get('weight', 0) * 100,
    ))
    print('  首批建仓: {:,.0f} 元 ({:.0f}%)'.format(
        phase_summary[0]['total_actual'], phase_summary[0]['capital_ratio'] * 100))
    print('=' * 70)
    print('  下一步: 2026年7月6日(周一) 09:20 盘前准备')
    print('=' * 70)


if __name__ == '__main__':
    main()
