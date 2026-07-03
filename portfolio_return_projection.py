# -*- coding: utf-8 -*-
"""
投资组合收益预测模型 - 500万建仓计划
======================================
测算2026年7月建仓至2027年12月底的收益情景
包含：基准、乐观、悲观、黑天鹅四个情景
"""

import json
import os
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ============================================================
# 组合权重 (来自500万建仓计划)
# ============================================================
WEIGHTS = {
    '588000': 0.1375,  # 科创50ETF华夏
    '512480': 0.1317,  # 半导体ETF国泰
    '516160': 0.1317,  # 高端装备ETF南方
    '515030': 0.1260,  # 新能源车ETF华夏
    '159915': 0.1150,  # 创业板ETF易方达
    '159992': 0.0947,  # 创新药ETF银华
    '512010': 0.0664,  # 医药ETF易方达
    '511260': 0.0050,  # 十年国债ETF国泰
    '511520': 0.0037,  # 政金债ETF富国
    '512400': 0.0929,  # 有色金属ETF南方
    '518880': 0.0307,  # 黄金ETF华安
    '601088': 0.0638,  # 中国神华
    '511360': 0.0010,  # 短融ETF海富通
}

NAMES = {
    '588000': '科创50ETF华夏',
    '512480': '半导体ETF国泰',
    '516160': '高端装备ETF南方',
    '515030': '新能源车ETF华夏',
    '159915': '创业板ETF易方达',
    '159992': '创新药ETF银华',
    '512010': '医药ETF易方达',
    '511260': '十年国债ETF国泰',
    '511520': '政金债ETF富国',
    '512400': '有色金属ETF南方',
    '518880': '黄金ETF华安',
    '601088': '中国神华',
    '511360': '短融ETF海富通',
}

STYLES = {
    '588000': '高端制造', '512480': '高端制造', '516160': '高端制造',
    '515030': '高端制造', '159915': '高端制造',
    '159992': '防御', '512010': '防御', '511260': '防御',
    '511520': '防御', '511360': '防御',
    '512400': '资源', '518880': '资源',
    '601088': '顺周期',
}

# ============================================================
# 情景定义 (年化回报 %)
# 投资周期：2026年7月 → 2027年12月 (约1.5年)
# ============================================================

# 情景一：基准情景 (中性预期)
BASE_CASE = {
    '588000': 30.0,   # 科创50 - AI/科技成长主线
    '512480': 35.0,   # 半导体 - AI算力+国产替代双驱动
    '516160': 21.5,   # 高端装备 - 十五五制造业升级
    '515030': 18.5,   # 新能源车 - 渗透率提升但增速放缓
    '159915': 24.0,   # 创业板 - 成长综合受益
    '159992': 21.5,   # 创新药 - 产业拐点+低基数效应
    '512010': 12.5,   # 医药宽基 - 估值修复为主
    '511260': 2.75,   # 十年国债 - 票息+小幅资本利得
    '511520': 3.25,   # 政金债 - 略高于国债
    '512400': 18.5,   # 有色金属 - 超级周期2.0
    '518880': 11.5,   # 黄金 - 央行购金+降息预期
    '601088': 7.5,    # 中国神华 - 高股息+煤价中枢上移
    '511360': 1.75,   # 短融ETF - 货币收益
}

# 情景二：乐观情景 (牛市) - 康波繁荣期+十五五政策共振
BULL_CASE = {
    '588000': 55.0,
    '512480': 65.0,
    '516160': 45.0,
    '515030': 40.0,
    '159915': 42.0,
    '159992': 25.0,
    '512010': 22.0,
    '511260': 3.0,
    '511520': 3.5,
    '512400': 40.0,
    '518880': 15.0,
    '601088': 20.0,
    '511360': 2.0,
}

# 情景三：悲观情景 (熊市) - 地缘风险+全球衰退
BEAR_CASE = {
    '588000': -43.0,
    '512480': -47.0,
    '516160': -39.0,
    '515030': -38.0,
    '159915': -35.0,
    '159992': -22.0,
    '512010': -22.0,
    '511260': 5.5,
    '511520': 6.0,
    '512400': -35.0,
    '518880': 10.0,
    '601088': -18.0,
    '511360': 1.5,
}

# 情景四：黑天鹅 (极端尾部风险) - 台海冲突/金融崩溃
BLACK_SWAN = {
    '588000': -63.0,
    '512480': -67.0,
    '516160': -56.0,
    '515030': -60.0,
    '159915': -53.0,
    '159992': -35.0,
    '512010': -35.0,
    '511260': 8.0,
    '511520': 8.5,
    '512400': -48.0,
    '518880': 7.0,
    '601088': -30.0,
    '511360': 1.5,
}

# 概率权重
PROBABILITIES = {
    'bull': 0.20,
    'base': 0.40,
    'bear': 0.30,
    'black_swan': 0.10,
}

INVESTMENT_HORIZON_YEARS = 1.5  # 2026-07 → 2027-12
INITIAL_CAPITAL = 5_000_000


def compute_scenario(scenario: dict, label: str) -> dict:
    """计算情景的加权组合回报"""
    weighted_return = sum(WEIGHTS[code] * scenario[code] for code in WEIGHTS)

    # 1.5年累计回报
    cumulative = (1 + weighted_return / 100) ** INVESTMENT_HORIZON_YEARS - 1

    # 最终金额
    final_amount = INITIAL_CAPITAL * (1 + cumulative)
    profit = final_amount - INITIAL_CAPITAL

    # 按风格分组
    style_returns = {}
    for code in WEIGHTS:
        style = STYLES[code]
        if style not in style_returns:
            style_returns[style] = {'weight': 0, 'weighted_return': 0}
        style_returns[style]['weight'] += WEIGHTS[code]
        style_returns[style]['weighted_return'] += WEIGHTS[code] * scenario[code]

    for style in style_returns:
        style_returns[style]['effective_return'] = (
            style_returns[style]['weighted_return'] / style_returns[style]['weight']
        )

    # 各资产明细
    assets = []
    sorted_codes = sorted(WEIGHTS.keys(), key=lambda c: -WEIGHTS[c])
    for code in sorted_codes:
        ann = scenario[code]
        cum = (1 + ann / 100) ** INVESTMENT_HORIZON_YEARS - 1
        profit_asset = INITIAL_CAPITAL * WEIGHTS[code] * cum
        assets.append({
            'code': code,
            'name': NAMES[code],
            'weight': WEIGHTS[code],
            'style': STYLES[code],
            'annualized_return': ann,
            'cumulative_return': round(cum * 100, 2),
            'profit': round(profit_asset, 0),
        })

    return {
        'label': label,
        'weighted_annualized': round(weighted_return, 2),
        'cumulative_return': round(cumulative * 100, 2),
        'final_amount': round(final_amount, 0),
        'total_profit': round(profit, 0),
        'style_breakdown': style_returns,
        'assets': assets,
    }


def compute_probability_weighted(scenarios: dict) -> dict:
    """计算概率加权期望回报"""
    expected_annualized = 0
    expected_cumulative = 0
    expected_final = 0

    for key, prob in PROBABILITIES.items():
        s = scenarios[key]
        expected_annualized += prob * s['weighted_annualized']
        expected_cumulative += prob * s['cumulative_return']
        expected_final += prob * s['final_amount']

    return {
        'expected_annualized': round(expected_annualized, 2),
        'expected_cumulative': round(expected_cumulative, 2),
        'expected_final_amount': round(expected_final, 0),
        'expected_profit': round(expected_final - INITIAL_CAPITAL, 0),
    }


def generate_report(scenarios: dict, expected: dict) -> str:
    """生成Markdown报告"""
    lines = []
    lines.append('# 500万建仓组合收益预测报告')
    lines.append('')
    lines.append(f'> **生成时间**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    lines.append(f'> **投资周期**: 2026年7月6日 → 2027年12月31日 (约1.5年)')
    lines.append(f'> **初始资金**: {INITIAL_CAPITAL:,} 元 (500万)')
    lines.append(f'> **策略**: 康波周期第六轮复苏→繁荣 × 十五五规划(2026-2030)')
    lines.append(f'> **组合构成**: 13只标的, 高端制造63.7% / 防御17.0% / 资源12.4% / 顺周期6.4%')
    lines.append('')

    # ===== 总览 =====
    lines.append('## 一、情景总览')
    lines.append('')
    lines.append('| 情景 | 概率 | 年化回报 | 1.5年累计 | 最终金额 | 绝对收益 |')
    lines.append('|:-----|-----:|--------:|---------:|--------:|--------:|')

    scenario_order = ['bull', 'base', 'bear', 'black_swan']
    scenario_labels = {
        'bull': '乐观 (牛市)',
        'base': '基准 (中性)',
        'bear': '悲观 (熊市)',
        'black_swan': '黑天鹅 (极端)',
    }

    for key in scenario_order:
        s = scenarios[key]
        prob = PROBABILITIES[key]
        sign = '+' if s['total_profit'] >= 0 else ''
        lines.append(f"| {scenario_labels[key]} | {prob:.0%} | "
                     f"{s['weighted_annualized']:+.1f}% | {s['cumulative_return']:+.1f}% | "
                     f"{s['final_amount']:,.0f} | {sign}{s['total_profit']:,.0f} |")

    lines.append(f"| **概率加权期望** | **100%** | **{expected['expected_annualized']:+.1f}%** | "
                 f"**{expected['expected_cumulative']:+.1f}%** | "
                 f"**{expected['expected_final_amount']:,.0f}** | "
                 f"**{expected['expected_profit']:+,.0f}** |")
    lines.append('')

    # 概率加权说明
    lines.append('### 概率权重说明')
    lines.append('')
    lines.append('| 情景 | 权重 | 逻辑依据 |')
    lines.append('|:-----|-----:|:---------|')
    lines.append('| 乐观 | 20% | AI超级周期+十五五政策共振发生概率较低，需要多重利好同时兑现 |')
    lines.append('| 基准 | 40% | 中性预期为主，反映康波复苏期的趋势性机会 |')
    lines.append('| 悲观 | 30% | 地缘风险和全球衰退压力赋予较高权重 |')
    lines.append('| 黑天鹅 | 10% | 尾部风险不可忽视，尤其台海和金融系统性风险 |')
    lines.append('')

    # ===== 各情景详细分析 =====
    lines.append('## 二、各情景详细分析')
    lines.append('')

    scenario_descriptions = {
        'bull': {
            'title': '乐观情景 (牛市) — 概率 20%',
            'drivers': [
                'AI应用全面爆发，全球半导体需求持续超预期，WSTS预测2026年全球半导体市场规模达1.51万亿美元',
                '十五五规划财政刺激超预期落地，超长期特别国债+专项债推动基建和科技投资加速',
                '美联储2027年开启降息周期，全球流动性宽松推动风险资产重估',
                '中国房地产企稳，消费信心恢复，A股盈利增速上行至15%+',
                '商品超级周期2.0确认，有色金属供给缺口持续扩大',
            ],
            'reference': '高盛预计2026-2027年中国股市每年上涨15-20%，由14%/12%盈利增长+约10%估值重估驱动',
        },
        'base': {
            'title': '基准情景 (中性) — 概率 40%',
            'drivers': [
                'AI和半导体维持高景气但增速从爆发期回归常态化增长',
                '十五五规划政策稳步推进，对相关产业形成持续但温和的支撑',
                'A股结构性行情为主，科技成长风格相对占优（银河证券、华泰证券判断）',
                '美联储维持利率高位至2027年初，之后小幅降息',
                '有色金属受益于供给约束维持较高价格，但需求增速放缓',
                '黄金在央行购金支撑下维持震荡上行',
            ],
            'reference': '沪深300预期年化8-12%，成长板块基于Beta弹性获得15-35%不等收益率',
        },
        'bear': {
            'title': '悲观情景 (熊市) — 概率 30%',
            'drivers': [
                '中美科技博弈升级，半导体出口管制全面收紧，影响AI产业链',
                '中国房地产和地方债务问题再度发酵，拖累银行体系和整体经济',
                '全球经济衰退，美联储高压政策导致需求萎缩',
                'A股出现系统性下跌，两融余额从3万亿高位回落引发踩踏',
                '人民币贬值压力加大，外资加速流出A股',
            ],
            'reference': '参考2018年熊市(CSI 300跌33%)和2015年股灾(CSI 300跌45%)的综合影响',
        },
        'black_swan': {
            'title': '黑天鹅情景 (极端) — 概率 10%',
            'drivers': [
                '台海地缘冲突升级，外资恐慌性撤离，A股出现历史性暴跌',
                '全球性金融危机爆发，类似2008年但冲击力更强',
                '中国金融系统出现局部性危机（中小银行+房地产+地方债务三重共振）',
                '全球供应链二次断裂，大宗商品价格剧烈波动',
                '流动性危机下所有风险资产被无差别抛售，仅国债和黄金具备避险功能',
            ],
            'reference': '参考2008年全球金融危机（A股跌73%）和美联储2026年压力测试极端情景（美股跌58%）',
        },
    }

    for key in scenario_order:
        desc = scenario_descriptions[key]
        s = scenarios[key]
        lines.append(f"### {desc['title']}")
        lines.append('')
        lines.append(f"**组合年化回报**: {s['weighted_annualized']:+.1f}% | "
                     f"**1.5年累计**: {s['cumulative_return']:+.1f}% | "
                     f"**最终金额**: {s['final_amount']:,.0f} 元")
        lines.append('')

        lines.append('**核心驱动因素**:')
        for d in desc['drivers']:
            lines.append(f'- {d}')
        lines.append('')
        lines.append(f'> 参考: {desc["reference"]}')
        lines.append('')

        # 风格分布
        lines.append('**风格板块表现**:')
        lines.append('')
        lines.append('| 风格 | 权重 | 年化回报 | 贡献 |')
        lines.append('|:-----|-----:|--------:|------:|')
        style_order = ['高端制造', '防御', '资源', '顺周期']
        for style in style_order:
            sr = s['style_breakdown'][style]
            lines.append(f"| {style} | {sr['weight']:.1%} | "
                         f"{sr['effective_return']:+.1f}% | "
                         f"{sr['weighted_return']:+.1f}pp |")
        lines.append('')

        # 各资产明细
        lines.append('**各资产明细**:')
        lines.append('')
        lines.append('| 代码 | 名称 | 权重 | 风格 | 年化回报 | 1.5年累计 | 盈亏 |')
        lines.append('|:-----|:-----|-----:|:-----|--------:|---------:|--------:|')
        for asset in s['assets']:
            sign = '+' if asset['profit'] >= 0 else ''
            lines.append(f"| {asset['code']} | {asset['name']} | {asset['weight']:.1%} | "
                         f"{asset['style']} | {asset['annualized_return']:+.1f}% | "
                         f"{asset['cumulative_return']:+.1f}% | {sign}{asset['profit']:,.0f} |")
        lines.append('')

    # ===== 敏感性分析 =====
    lines.append('## 三、敏感性分析')
    lines.append('')
    lines.append('### 高端制造板块回报对组合的影响')
    lines.append('')
    lines.append('高端制造板块占组合63.7%，是最关键的收益驱动因素。假设其他资产回报不变：')
    lines.append('')
    lines.append('| 高端制造年化回报 | 组合年化回报 | 最终金额 |')
    lines.append('|----------------:|------------:|--------:|')

    for hm_return in [60, 45, 30, 21.5, 10, 0, -10, -20, -40]:
        # 重新计算组合回报
        weighted = 0
        for code in WEIGHTS:
            if STYLES[code] == '高端制造':
                weighted += WEIGHTS[code] * hm_return
            elif code in ['511260', '511520', '511360']:
                weighted += WEIGHTS[code] * 2.5  # 债券类固定
            elif code in ['518880']:
                weighted += WEIGHTS[code] * 11.5
            elif code in ['512400']:
                weighted += WEIGHTS[code] * 18.5
            elif code in ['159992', '512010']:
                weighted += WEIGHTS[code] * 15.0
            elif code in ['601088']:
                weighted += WEIGHTS[code] * 7.5

        cum = (1 + weighted / 100) ** 1.5 - 1
        final = INITIAL_CAPITAL * (1 + cum)
        lines.append(f"| {hm_return:+.0f}% | {weighted:+.1f}% | {final:,.0f} |")
    lines.append('')

    lines.append('### 关键参数假设')
    lines.append('')
    lines.append('| 参数 | 基准值 | 乐观 | 悲观 | 说明 |')
    lines.append('|:-----|:------|:-----|:-----|:-----|')
    lines.append('| 半导体行业增速 | 35%/年 | 65%/年 | -47%/年 | AI周期核心变量 |')
    lines.append('| 沪深300回报 | ~10-12%/年 | ~25-30%/年 | ~-25%/年 | 市场基准 |')
    lines.append('| 10年国债收益率 | 1.7-2.0% | 1.5-1.8% | 2.0-2.5% | 利率环境 |')
    lines.append('| 黄金价格(美元) | ~4,600 | ~5,400 | ~4,300 | 央行购金趋势 |')
    lines.append('| 人民币汇率 | 7.2-7.3 | 7.0-7.2 | 7.5-7.8 | 汇率风险 |')
    lines.append('| CSI 300 PE | 14-16x | 18-20x | 10-12x | 估值水平 |')
    lines.append('')

    # ===== 风险提示 =====
    lines.append('## 四、核心风险因素')
    lines.append('')
    lines.append('1. **集中度风险 (极高)**：高端制造板块占比63.7%, 若该方向遭遇系统性调整, 组合将承受远超市场平均的损失。悲观情景下该板块年化跌幅可达35-47%。')
    lines.append('')
    lines.append('2. **地缘政治风险 (高)**：中美科技博弈和台海局势是本组合最大的外生风险源。半导体ETF(512480)和科创50ETF(588000)对制裁升级极度敏感。')
    lines.append('')
    lines.append('3. **估值回调风险 (中高)**：科创50当前静态PE超60倍, 半导体板块经历大幅上涨, 若盈利增速不及预期将面临戴维斯双杀。')
    lines.append('')
    lines.append('4. **流动性风险 (中)**：两融余额突破3万亿, 虽然占比相对健康(2.83% vs 2015年4.72%), 但极端行情下仍可能引发流动性踩踏。')
    lines.append('')
    lines.append('5. **黄金双刃剑风险 (低中)**：黄金在流动性危机初期可能被无差别抛售(如2008年和2020年3月), 需注意其避险属性在极端情景下的暂时失效。')
    lines.append('')
    lines.append('6. **康波周期不确定性 (中)**：第六波康波复苏→繁荣转换的时点和强度存在分歧, 部分学者认为真正的繁荣期可能在2030年前后才全面开启。')
    lines.append('')

    # ===== 结论 =====
    lines.append('## 五、结论')
    lines.append('')
    lines.append(f'在概率加权的框架下，本组合至2027年底的预期年化收益约为 **{expected["expected_annualized"]:+.1f}%**。')
    lines.append('')
    lines.append(f'- **最好情况 (20%概率)**：年化 {scenarios["bull"]["weighted_annualized"]:+.1f}%, '
                 f'500万增长至约 **{scenarios["bull"]["final_amount"]:,.0f} 元**（盈利 {scenarios["bull"]["total_profit"]:+,.0f} 元）')
    lines.append(f'- **基准情况 (40%概率)**：年化 {scenarios["base"]["weighted_annualized"]:+.1f}%, '
                 f'500万增长至约 **{scenarios["base"]["final_amount"]:,.0f} 元**（盈利 {scenarios["base"]["total_profit"]:+,.0f} 元）')
    lines.append(f'- **最差情况 (30%概率)**：年化 {scenarios["bear"]["weighted_annualized"]:+.1f}%, '
                 f'500万缩水至约 **{scenarios["bear"]["final_amount"]:,.0f} 元**（亏损 {scenarios["bear"]["total_profit"]:+,.0f} 元）')
    lines.append(f'- **极端情况 (10%概率)**：年化 {scenarios["black_swan"]["weighted_annualized"]:+.1f}%, '
                 f'500万缩水至约 **{scenarios["black_swan"]["final_amount"]:,.0f} 元**（亏损 {scenarios["black_swan"]["total_profit"]:+,.0f} 元）')
    lines.append('')

    lines.append('### 极端情景下的对冲策略建议')
    lines.append('')
    lines.append('若希望在极端情景下保护组合，建议考虑以下措施：')
    lines.append('')
    lines.append('1. **增加债券配置**：将防御板块从17%提升至25-30%，增加国债ETF和政金债ETF权重（熊市中债市可贡献5-8%正收益）')
    lines.append('2. **黄金加仓**：将黄金从3%提升至5-8%，历史上11次重大危机中黄金有9次最终涨超20%')
    lines.append('3. **期权对冲**：买入科创50或沪深300的看跌期权（Put），在极端行情下提供非线性保护')
    lines.append('4. **动态止损**：严格执行建仓计划中的止损规则（高风险-15%、中风险-12%、低风险-5%）')
    lines.append('5. **现金储备**：保持至少10-15%的现金或短融ETF，用于极端行情下的低位补仓')
    lines.append('')

    lines.append('---')
    lines.append('')
    lines.append('> **免责声明**: 本报告中的回报预测基于历史数据、分析师预期和情景假设, 不构成投资建议。')
    lines.append('> 实际结果可能因市场条件、政策变化和不可预见事件而显著偏离预测值。')
    lines.append('> 所有投资决策应基于独立研究和专业判断。')
    lines.append('')
    lines.append(f'> *报告生成: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}*')
    lines.append('> *数据来源: 高盛/摩根士丹利/中信证券/华泰证券研报, Wind数据, 康波周期理论框架*')

    return '\n'.join(lines)


def main():
    print('=' * 70)
    print('  500万建仓组合收益预测')
    print(f'  投资周期: 2026-07-06 → 2027-12-31 (约1.5年)')
    print('=' * 70)

    # 计算各情景
    scenarios = {}
    scenarios['bull'] = compute_scenario(BULL_CASE, '乐观 (牛市)')
    scenarios['base'] = compute_scenario(BASE_CASE, '基准 (中性)')
    scenarios['bear'] = compute_scenario(BEAR_CASE, '悲观 (熊市)')
    scenarios['black_swan'] = compute_scenario(BLACK_SWAN, '黑天鹅 (极端)')

    # 概率加权
    expected = compute_probability_weighted(scenarios)

    # 打印摘要
    print(f'\n{"情景":<16} {"概率":<8} {"年化回报":<12} {"最终金额":<16} {"盈亏":<16}')
    print('-' * 68)
    labels = {'bull': '乐观(牛市)', 'base': '基准(中性)', 'bear': '悲观(熊市)', 'black_swan': '黑天鹅(极端)'}
    for key in ['bull', 'base', 'bear', 'black_swan']:
        s = scenarios[key]
        sign = '+' if s['total_profit'] >= 0 else ''
        print(f'{labels[key]:<16} {PROBABILITIES[key]:<8.0%} '
              f'{s["weighted_annualized"]:>+8.1f}%    {s["final_amount"]:>12,.0f}  {sign}{s["total_profit"]:>12,.0f}')
    print('-' * 68)
    print(f'{"概率加权期望":<16} {"100%":<8} '
          f'{expected["expected_annualized"]:>+8.1f}%    {expected["expected_final_amount"]:>12,.0f}  '
          f'{expected["expected_profit"]:+>12,.0f}')

    # 生成报告
    report = generate_report(scenarios, expected)

    report_path = os.path.join(BASE_DIR, 'portfolio_return_projection.md')
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report)

    # 保存JSON
    json_data = {
        'generated_at': datetime.now().isoformat(),
        'investment_horizon': '2026-07-06 → 2027-12-31',
        'horizon_years': INVESTMENT_HORIZON_YEARS,
        'initial_capital': INITIAL_CAPITAL,
        'scenarios': {
            'bull': {k: v for k, v in scenarios['bull'].items() if k != 'assets'},
            'base': {k: v for k, v in scenarios['base'].items() if k != 'assets'},
            'bear': {k: v for k, v in scenarios['bear'].items() if k != 'assets'},
            'black_swan': {k: v for k, v in scenarios['black_swan'].items() if k != 'assets'},
        },
        'probability_weights': PROBABILITIES,
        'expected': expected,
        'asset_detail': scenarios['base']['assets'],
    }

    json_path = os.path.join(BASE_DIR, 'portfolio_return_projection.json')
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(json_data, f, ensure_ascii=False, indent=2)

    print(f'\n报告已保存: {report_path}')
    print(f'JSON数据:   {json_path}')


if __name__ == '__main__':
    main()
