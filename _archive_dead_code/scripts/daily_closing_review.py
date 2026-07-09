# -*- coding: utf-8 -*-
"""
每日收盘盈亏复盘报告生成器（顶级对冲基金视角）
=================================================
- 成本价：第一天建仓当日开盘价格（从 500万建仓计划_20260706.json 提取 phase1 actual_amount/shares）
- 持仓范围：股票+ETF+对冲品种（IF 股指期货空头）
- 复盘视角：Bridgewater/Renaissance Two Sigma 等顶级对冲基金方法论
- 次日计划：策略建议 + 风控预案（不生成具体订单，由 build_plan_executor.py 负责）

用法:
    python scripts/daily_closing_review.py                       # 默认今天
    python scripts/daily_closing_review.py --date 2026-07-08     # 指定日期
    python scripts/daily_closing_review.py --dry-run             # 仅打印不保存
"""

import os
import sys
import json
import argparse
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Dict, List, Any, Optional, Tuple

# 路径配置
PROJECT_ROOT = r'e:\各种PY程序\28-终极量化交易系统7.1'
BUILD_PLAN_PATH = os.path.join(PROJECT_ROOT, '500万建仓计划_20260706.json')
HEDGE_DIR = os.path.join(PROJECT_ROOT, 'v7.5_institutional', 'reports')
TRADE_PLANS_DIR = os.path.join(PROJECT_ROOT, 'v7.5_institutional', 'trade_plans')
ARCHIVE_ROOT = os.path.join(PROJECT_ROOT, '每日报告归档')

# 第一天建仓日期（用于成本价基准）
DAY1_DATE = '2026-07-06'

# 兜底收盘价（当 Wind MCP / iFinD MCP 都不可用时使用，标注为估算值）
# 更新于 2026-07-08 收盘
FALLBACK_CLOSE_PRICES = {
    'sz510300': 4.012, 'sh510300': 4.012, '510300': 4.012,
    'sz510500': 6.542, 'sh510500': 6.542, '510500': 6.542,
    'sz512100': 2.318, 'sh512100': 2.318, '512100': 2.318,
    'sz588000': 1.062, 'sh588000': 1.062, '588000': 1.062,
    'sz159915': 2.167, 'sh159915': 2.167, '159915': 2.167,
    'sz515180': 5.043, 'sh515180': 5.043, '515180': 5.043,
    'sz518880': 5.892, 'sh518880': 5.892, '518880': 5.892,
    'sh600089': 25.18, '600089': 25.18,
    'sh600036': 38.42, '600036': 38.42,
    'sh600875': 22.05, '600875': 22.05,
    'sh600406': 35.31, '600406': 35.31,
    'sh600989': 18.12, '600989': 18.12,
    'sh600900': 28.15, '600900': 28.15,
    'sh600276': 50.45, '600276': 50.45,
    'sh601088': 38.85, '601088': 38.85,
    'sh688017': 181.50, '688017': 181.50,
    'sh688041': 85.80, '688041': 85.80,
    'sh688981': 144.20, '688981': 144.20,
    'sh603019': 95.10, '603019': 95.10,
    'sh600219': 4.225, '600219': 4.225,
    'sh600019': 5.652, '600019': 5.652,
    'sz300274': 45.36, 'sh300274': 45.36, '300274': 45.36,
    'sz300308': 121.10, 'sh300308': 121.10, '300308': 121.10,
    'sz002371': 352.80, 'sh002371': 352.80, '002371': 352.80,
    'sz000425': 8.575, 'sh000425': 8.575, '000425': 8.575,
    'sz000792': 29.50, 'sh000792': 29.50, '000792': 29.50,
    # 沪深300指数（用于 IF 期货盯市）
    'sh000300': 4705.20, '000300': 4705.20,
}


def parse_args():
    parser = argparse.ArgumentParser(description='每日收盘盈亏复盘报告生成器')
    parser.add_argument('--date', type=str, default=None,
                        help='报告日期 YYYY-MM-DD（默认今天）')
    parser.add_argument('--dry-run', action='store_true',
                        help='仅打印不保存文件')
    return parser.parse_args()


def load_json(path: str) -> Dict:
    """安全加载 JSON 文件"""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f'[加载失败] {path}: {e}')
        return {}


def extract_day1_cost_basis(build_plan: Dict) -> Dict[str, Dict]:
    """
    从 500万建仓计划 提取每个标的第一天建仓成本。
    成本价 = phase1.actual_amount / phase1.shares
    """
    target_portfolio = build_plan.get('target_portfolio', {})
    position_plan = build_plan.get('position_plan', {})
    day1_positions = {}

    for code, info in position_plan.items():
        phase1 = next((p for p in info.get('phases', []) if p.get('phase') == 1), None)
        if not phase1:
            continue
        shares = phase1.get('shares', 0)
        actual_amount = phase1.get('actual_amount', 0)
        if shares <= 0:
            continue
        cost_price = actual_amount / shares
        target_info = target_portfolio.get(code, {})
        day1_positions[code] = {
            'name': info.get('name', target_info.get('name', '')),
            'style': target_info.get('style', ''),
            'risk': target_info.get('risk', ''),
            'shares': shares,
            'cost_price': cost_price,
            'cost': actual_amount,
            'stop_loss': target_info.get('stop_loss', -0.10),
            'reason': target_info.get('reason', ''),
        }
    return day1_positions


def load_hedge_positions(report_date: str) -> List[Dict]:
    """加载对冲成交记录（按报告日期）"""
    hedge_file = os.path.join(HEDGE_DIR, f'hedge_execution_fill_{report_date}.json')
    if not os.path.exists(hedge_file):
        # 回退到最近的对冲文件
        candidates = sorted(
            [f for f in os.listdir(HEDGE_DIR) if f.startswith('hedge_execution_fill_')],
            reverse=True
        ) if os.path.exists(HEDGE_DIR) else []
        if candidates:
            hedge_file = os.path.join(HEDGE_DIR, candidates[0])
            print(f'[提示] 未找到 {report_date} 对冲文件，使用最近: {candidates[0]}')
        else:
            print(f'[警告] 未找到任何对冲成交文件')
            return []
    hedge_data = load_json(hedge_file)
    return hedge_data.get('orders', [])


def _classify_symbol(code: str) -> str:
    """分类标的类型：stock / etf / index"""
    pure = code.lstrip('shzsSHZS')
    # 指数：000xxx (上证指数系列) 或 399xxx (深证指数系列)
    if pure.startswith('000') and len(pure) == 6 and not pure.startswith('0000'):
        return 'index'
    if pure.startswith('399'):
        return 'index'
    # ETF: 51xxxx (沪ETF) / 15xxxx (深ETF) / 58xxxx (科创板ETF) / 16xxxx (LOF)
    if pure.startswith(('51', '58', '15', '16')):
        return 'etf'
    return 'stock'


def _index_code_to_name(code: str) -> str:
    """指数代码转名称（iFinD 需要）"""
    mapping = {
        'sh000300': '沪深300', '000300': '沪深300',
        'sh000852': '中证1000', '000852': '中证1000',
        'sh000016': '上证50', '000016': '上证50',
        'sh000905': '中证500', '000905': '中证500',
        'sh000001': '上证指数', '000001': '上证指数',
        'sh399001': '深证成指', '399001': '深证成指',
        'sh399006': '创业板指', '399006': '创业板指',
    }
    return mapping.get(code, code)


def _safe_float(value) -> Optional[float]:
    """安全转浮点数"""
    if value is None:
        return None
    try:
        f = float(value)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def _fetch_via_wind_mcp(symbols: List[str], cost_basis: Dict[str, float] = None) -> Dict[str, Dict]:
    """
    优先级 P0：Wind MCP 获取收盘价
    包含价格合理性校验：如果返回价格偏离成本价超过 50%，认为数据可疑并丢弃
    （Wind MCP 存在代码映射 bug，如 sh000300 返回上证指数价格而非沪深300）
    """
    prices = {}
    try:
        sys.path.insert(0, PROJECT_ROOT)
        from wind_mcp_fetcher import wind_get_quote  # type: ignore
    except Exception as e:
        print(f'[Wind MCP 不可用] 模块加载失败: {e}')
        return prices

    cost_basis = cost_basis or {}
    success_count = 0
    rejected_count = 0
    for code in symbols:
        try:
            sym_type = _classify_symbol(code)
            # Wind MCP: ETF/股票用 is_fund 区分；指数当作 stock 查询
            is_fund = (sym_type == 'etf')
            quote = wind_get_quote(code, is_fund=is_fund)
            if not quote:
                continue
            # 优先用 close/pre_close，其次 price（盘后 price 通常等于收盘价）
            p = _safe_float(quote.get('close')) or _safe_float(quote.get('price'))
            if not p or p <= 0:
                continue

            # 价格合理性校验
            cost = cost_basis.get(code)
            if cost and cost > 0:
                deviation = abs(p - cost) / cost
                if deviation > 0.5:  # 偏离成本价超过 50%，数据可疑
                    print(f'[Wind MCP 数据可疑] {code}: 返回价格 {p:.3f} 偏离成本价 {cost:.3f} 达 {deviation*100:.1f}%，丢弃')
                    rejected_count += 1
                    continue

            prices[code] = {'close': p, 'source': 'Wind MCP'}
            success_count += 1
        except Exception as e:
            print(f'[Wind MCP] {code} 获取失败: {e}')
            continue
    if rejected_count > 0:
        print(f'[Wind MCP] 拒绝 {rejected_count} 个可疑数据（偏离成本价 >50%）')
    print(f'[Wind MCP] 成功获取 {success_count}/{len(symbols)} 个标的价格')
    return prices


def _fetch_via_ifind_mcp(symbols: List[str]) -> Dict[str, Dict]:
    """优先级 P1：iFinD MCP 获取收盘价（仅对 Wind MCP 失败的标的）"""
    prices = {}
    try:
        sys.path.insert(0, PROJECT_ROOT)
        from utils.ifind_client import IFindClient  # type: ignore
        import os as _os
        token = _os.environ.get('IFIND_TOKEN', '')
        client = IFindClient(auth_token=token)
    except Exception as e:
        print(f'[iFinD MCP 不可用] {e}')
        return prices

    # 按类型分组
    etf_codes = [c.lstrip('shzsSHZS') for c in symbols if _classify_symbol(c) == 'etf']
    index_codes = [c for c in symbols if _classify_symbol(c) == 'index']
    stock_codes = [c for c in symbols if _classify_symbol(c) == 'stock']

    # 1. ETF 批量获取
    if etf_codes:
        try:
            etf_quotes = client.get_etf_quotes(etf_codes) or {}
            for full_code in symbols:
                if _classify_symbol(full_code) != 'etf':
                    continue
                pure = full_code.lstrip('shzsSHZS')
                q = etf_quotes.get(pure) or etf_quotes.get(full_code)
                if q:
                    p = _safe_float(q.get('price'))
                    if p and p > 0:
                        prices[full_code] = {'close': p, 'source': 'iFinD MCP'}
        except Exception as e:
            print(f'[iFinD MCP] ETF 批量获取失败: {e}')

    # 2. 指数获取
    for code in index_codes:
        try:
            name = _index_code_to_name(code)
            idx = client.get_index_latest(name)
            if idx:
                p = _safe_float(idx.get('close'))
                if p and p > 0:
                    prices[code] = {'close': p, 'source': 'iFinD MCP'}
        except Exception as e:
            print(f'[iFinD MCP] 指数 {code} 获取失败: {e}')
            continue

    # 3. 股票：用历史K线取最新一条
    for code in stock_codes:
        try:
            klines = client.get_historical_klines(code.lstrip('shzsSHZS'), days=1)
            if klines:
                last = klines[-1] if isinstance(klines, list) else None
                p = _safe_float(last.get('close')) if last else None
                if p and p > 0:
                    prices[code] = {'close': p, 'source': 'iFinD MCP'}
        except Exception as e:
            print(f'[iFinD MCP] 股票 {code} 获取失败: {e}')
            continue

    print(f'[iFinD MCP] 成功获取 {len(prices)}/{len(symbols)} 个标的价格')
    return prices


def _fetch_via_fallback(symbols: List[str]) -> Dict[str, Dict]:
    """优先级 P2：兜底估算价格"""
    prices = {}
    for code in symbols:
        fb = FALLBACK_CLOSE_PRICES.get(code)
        if not fb:
            pure = code.lstrip('shzsSHZS')
            fb = FALLBACK_CLOSE_PRICES.get(pure)
        if fb:
            prices[code] = {'close': fb, 'source': '兜底估算'}
    return prices


def fetch_close_prices(symbols: List[str], cost_basis: Dict[str, float] = None) -> Dict[str, Dict]:
    """
    获取收盘价。严格遵循优先级链：
    Wind MCP (P0) → iFinD MCP (P1) → 兜底估算 (P2)

    参数:
        symbols: 标的代码列表
        cost_basis: 成本价字典 {code: cost_price}，用于 Wind MCP 数据合理性校验

    返回: {symbol: {'close': float, 'source': str}}
    每个标的价格都会标注来源，便于报告透明。
    """
    prices = {}

    # P0: Wind MCP（带成本价校验）
    wind_prices = _fetch_via_wind_mcp(symbols, cost_basis=cost_basis)
    prices.update(wind_prices)

    # P1: iFinD MCP（仅对 Wind MCP 未获取的标的）
    missing = [c for c in symbols if c not in prices]
    if missing:
        print(f'[数据源降级] {len(missing)} 个标的降级到 iFinD MCP')
        ifind_prices = _fetch_via_ifind_mcp(missing)
        prices.update(ifind_prices)

    # P2: 兜底估算（仅对前两级都失败的标的）
    missing = [c for c in symbols if c not in prices]
    if missing:
        print(f'[数据源降级] {len(missing)} 个标的降级到兜底估算')
        fb_prices = _fetch_via_fallback(missing)
        prices.update(fb_prices)

    # 统计数据源分布
    source_stats = defaultdict(int)
    for v in prices.values():
        source_stats[v['source']] += 1
    print(f'[数据源统计] {dict(source_stats)}')

    return prices


def calc_stock_pnl(positions: Dict, prices: Dict) -> Tuple[List[Dict], Dict]:
    """计算股票/ETF 持仓盈亏"""
    rows = []
    total_cost = 0.0
    total_value = 0.0
    total_pnl = 0.0

    for code, pos in positions.items():
        cost = pos['cost']
        shares = pos['shares']
        cost_price = pos['cost_price']
        price_info = prices.get(code, {'close': cost_price, 'source': '无数据'})
        close_price = price_info['close']

        position_value = shares * close_price
        pnl = position_value - cost
        pnl_pct = (pnl / cost * 100) if cost > 0 else 0.0

        # 止损状态
        stop_loss = pos.get('stop_loss', -0.10)
        pnl_ratio = pnl / cost if cost > 0 else 0
        if pnl_ratio <= stop_loss:
            status = 'STOP_LOSS_TRIGGERED'
        elif pnl_ratio <= stop_loss * 0.7:
            status = 'WARNING'
        elif pnl_ratio >= 0.05:
            status = 'PROFIT'
        else:
            status = 'NORMAL'

        total_cost += cost
        total_value += position_value
        total_pnl += pnl

        rows.append({
            'code': code,
            'name': pos['name'],
            'style': pos['style'],
            'risk': pos['risk'],
            'shares': shares,
            'cost_price': cost_price,
            'close_price': close_price,
            'source': price_info['source'],
            'cost': cost,
            'position_value': position_value,
            'pnl': pnl,
            'pnl_pct': pnl_pct,
            'status': status,
            'stop_loss': stop_loss,
        })

    summary = {
        'total_cost': total_cost,
        'total_value': total_value,
        'total_pnl': total_pnl,
        'total_pnl_pct': (total_pnl / total_cost * 100) if total_cost > 0 else 0.0,
        'position_count': len(rows),
    }
    # 按盈亏排序
    rows.sort(key=lambda x: x['pnl'], reverse=True)
    return rows, summary


def calc_hedge_pnl(hedge_orders: List[Dict], prices: Dict) -> Tuple[List[Dict], Dict]:
    """计算对冲头寸盈亏（IF 期货空头）"""
    rows = []
    total_notional = 0.0
    total_hedge_pnl = 0.0

    for order in hedge_orders:
        if order.get('type') != 'BETA' or order.get('action') != 'SHORT_FUTURES':
            continue
        instrument = order.get('instrument', 'IF')
        contracts = order.get('contracts', 0)
        entry_price = order.get('price', 0)
        notional = order.get('notional', 0)
        multiplier = 300  # IF 每点 300 元

        # 获取标的指数收盘价作为盯市价格
        underlying_code = 'sh000300' if instrument == 'IF' else f'sh000852'
        mark_price = prices.get(underlying_code, {}).get('close', entry_price)

        # 空头盈亏 = (开仓价 - 收盘价) * 乘数 * 手数
        direction = 'SHORT'
        hedge_pnl = (entry_price - mark_price) * multiplier * contracts
        hedge_pnl_pct = ((entry_price - mark_price) / entry_price * 100) if entry_price > 0 else 0

        total_notional += notional
        total_hedge_pnl += hedge_pnl

        rows.append({
            'instrument': instrument,
            'direction': direction,
            'contracts': contracts,
            'multiplier': multiplier,
            'entry_price': entry_price,
            'mark_price': mark_price,
            'notional': notional,
            'hedge_pnl': hedge_pnl,
            'hedge_pnl_pct': hedge_pnl_pct,
            'underlying': underlying_code,
        })

    summary = {
        'total_notional': total_notional,
        'total_hedge_pnl': total_hedge_pnl,
        'position_count': len(rows),
    }
    return rows, summary


def analyze_style_attribution(stock_rows: List[Dict]) -> List[Dict]:
    """风格表现归因分析"""
    style_stats = defaultdict(lambda: {'cost': 0.0, 'value': 0.0, 'pnl': 0.0, 'count': 0})
    for r in stock_rows:
        s = style_stats[r['style']]
        s['cost'] += r['cost']
        s['value'] += r['position_value']
        s['pnl'] += r['pnl']
        s['count'] += 1
    result = []
    for style, stats in style_stats.items():
        pnl_pct = (stats['pnl'] / stats['cost'] * 100) if stats['cost'] > 0 else 0
        weight = stats['value']  # 占比稍后计算
        result.append({
            'style': style,
            'cost': stats['cost'],
            'value': stats['value'],
            'pnl': stats['pnl'],
            'pnl_pct': pnl_pct,
            'count': stats['count'],
        })
    result.sort(key=lambda x: x['pnl'], reverse=True)
    return result


def calc_risk_metrics(stock_rows: List[Dict], hedge_rows: List[Dict],
                      stock_summary: Dict, hedge_summary: Dict) -> Dict:
    """风险指标计算（顶级对冲基金视角）"""
    # 持仓收益分布
    returns = [r['pnl_pct'] / 100 for r in stock_rows]
    avg_return = sum(returns) / len(returns) if returns else 0
    volatility = (sum((r - avg_return) ** 2 for r in returns) / len(returns)) ** 0.5 if len(returns) > 1 else 0

    # 最大跌幅
    losses = [r['pnl_pct'] for r in stock_rows if r['pnl_pct'] < 0]
    max_drawdown = min(losses) if losses else 0

    # 止损状态统计
    status_count = defaultdict(int)
    for r in stock_rows:
        status_count[r['status']] += 1

    # 净盈亏
    net_pnl = stock_summary['total_pnl'] + hedge_summary['total_hedge_pnl']
    net_pnl_pct = (net_pnl / stock_summary['total_cost'] * 100) if stock_summary['total_cost'] > 0 else 0

    # 对冲有效性（简化：对冲规模 / 股票市值）
    hedge_ratio = (hedge_summary['total_notional'] / stock_summary['total_value']
                   if stock_summary['total_value'] > 0 else 0)

    # 估算组合 Beta 敞口
    # 假设股票组合原始 Beta ~1.0，对冲降低的部分 = 对冲规模/股票市值
    portfolio_beta = max(0.1, 1.0 - hedge_ratio)

    return {
        'net_pnl': net_pnl,
        'net_pnl_pct': net_pnl_pct,
        'avg_return_pct': avg_return * 100,
        'volatility_pct': volatility * 100,
        'max_drawdown_pct': max_drawdown,
        'stop_loss_status': dict(status_count),
        'hedge_ratio': hedge_ratio,
        'portfolio_beta': portfolio_beta,
        'hedge_effectiveness': hedge_ratio * 100,  # 简化指标
    }


def generate_next_day_plan(stock_rows: List[Dict], hedge_rows: List[Dict],
                           risk_metrics: Dict, build_plan: Dict,
                           report_date: str) -> Dict:
    """
    生成次日交易计划：策略建议 + 风控预案
    （不生成具体订单，由 build_plan_executor.py 负责生成订单）
    """
    # 判断当前阶段
    phase_roadmap = build_plan.get('phase_roadmap', [])
    current_phase = next((p for p in phase_roadmap if p.get('start', '') <= report_date), None)
    next_phase = next((p for p in phase_roadmap if p.get('start', '') > report_date), None)

    # 风险评估
    net_pnl_pct = risk_metrics['net_pnl_pct']
    stop_loss_count = risk_metrics['stop_loss_status'].get('STOP_LOSS_TRIGGERED', 0)
    warning_count = risk_metrics['stop_loss_status'].get('WARNING', 0)
    portfolio_beta = risk_metrics['portfolio_beta']

    # 策略建议
    if net_pnl_pct > 2.0:
        market_regime = 'STRONG_BULL'
        strategy = '维持当前仓位，按计划继续建仓；考虑获利了结部分超涨标的'
    elif net_pnl_pct > 0:
        market_regime = 'BULLISH'
        strategy = '按计划继续建仓，关注领涨风格延续性'
    elif net_pnl_pct > -2.0:
        market_regime = 'NEUTRAL'
        strategy = '谨慎建仓，放缓节奏，加强对冲'
    else:
        market_regime = 'BEARISH'
        strategy = '暂停建仓，评估止损，加大尾部对冲'

    # 对冲调整建议
    if portfolio_beta > 0.6:
        hedge_advice = '建议增加 IF 期货空头合约，降低组合 Beta 至 0.3 以下'
    elif portfolio_beta < 0.1:
        hedge_advice = '对冲偏重，可考虑减仓部分 IF 空单，释放保证金'
    else:
        hedge_advice = '对冲比例合适，维持当前期货空头仓位'

    # 风控预案
    risk_scenarios = [
        {
            'trigger': '单日组合回撤 ≥ -3%',
            'action': '停止当日买入，评估是否减仓 30%',
            'priority': 'HIGH',
        },
        {
            'trigger': '单日组合回撤 ≥ -5%',
            'action': '强制减仓 30%，IF 期货空头加仓 1-2 手',
            'priority': 'CRITICAL',
        },
        {
            'trigger': '个股触发止损线',
            'action': f'次日开盘评估止损执行（当前触发 {stop_loss_count} 只，预警 {warning_count} 只）',
            'priority': 'HIGH' if stop_loss_count > 0 else 'MEDIUM',
        },
        {
            'trigger': 'VIX > 25 或日内波动 > 2%',
            'action': '启动期权保护性 Put，加大尾部对冲',
            'priority': 'MEDIUM',
        },
        {
            'trigger': '流动性枯竭（成交额 < 5日均值的 60%）',
            'action': '暂停建仓，改为分批小单执行',
            'priority': 'MEDIUM',
        },
    ]

    # 标的级建议（盈亏前 3 / 后 3）
    winners = [r for r in stock_rows[:3] if r['pnl'] > 0]
    losers = [r for r in stock_rows[-3:] if r['pnl'] < 0]

    return {
        'market_regime': market_regime,
        'strategy': strategy,
        'hedge_advice': hedge_advice,
        'current_phase': current_phase,
        'next_phase': next_phase,
        'risk_scenarios': risk_scenarios,
        'winners': winners,
        'losers': losers,
        'stop_loss_triggered': stop_loss_count,
        'warning_count': warning_count,
    }


def render_markdown(report: Dict) -> str:
    """渲染 Markdown 报告（顶级对冲基金视角）"""
    md = []
    md.append('# 收盘盈亏复盘报告（顶级对冲基金视角）')
    md.append('')
    md.append(f"**报告日期**: {report['report_date']}")
    md.append(f"**生成时间**: {report['generated_at']}")
    md.append(f"**成本基准**: 第一天建仓成本（{report['day1_date']} 开盘价）")
    md.append(f"**复盘视角**: Bridgewater / Renaissance / Two Sigma 方法论")
    md.append(f"**持仓范围**: 股票+ETF+对冲品种（IF 期货）")
    md.append('')
    md.append('---')
    md.append('')

    # 一、市场概况与组合净盈亏
    md.append('## 一、市场概况与组合净盈亏')
    md.append('')
    md.append('| 指标 | 数值 |')
    md.append('|------|------|')
    md.append(f"| 市场状态 | {report['next_day_plan']['market_regime']} |")
    md.append(f"| 组合总成本 | ¥{report['stock_summary']['total_cost']:,.0f} |")
    md.append(f"| 组合总市值 | ¥{report['stock_summary']['total_value']:,.0f} |")
    md.append(f"| 股票盈亏 | {report['stock_summary']['total_pnl']:+,.0f} ({report['stock_summary']['total_pnl_pct']:+.2f}%) |")
    md.append(f"| 对冲盈亏 | {report['hedge_summary']['total_hedge_pnl']:+,.0f} |")
    md.append(f"| **净盈亏** | **{report['risk_metrics']['net_pnl']:+,.0f} ({report['risk_metrics']['net_pnl_pct']:+.2f}%)** |")
    md.append(f"| 持仓数量 | {report['stock_summary']['position_count']} 只股票 + {report['hedge_summary']['position_count']} 个对冲 |")
    md.append('')

    # 二、持仓盈亏明细
    md.append('## 二、持仓盈亏明细（按盈亏排序）')
    md.append('')
    md.append('| 代码 | 名称 | 风格 | 风险 | 持仓 | 成本价 | 收盘价 | 成本 | 市值 | 盈亏 | 盈亏率 | 状态 | 数据源 |')
    md.append('|------|------|------|------|------|--------|--------|------|------|------|--------|------|--------|')
    for r in report['stock_rows']:
        status_icon = {
            'PROFIT': '🟢',
            'NORMAL': '✅',
            'WARNING': '⚠️',
            'STOP_LOSS_TRIGGERED': '🔴',
        }.get(r['status'], '✅')
        md.append(f"| {r['code']} | {r['name']} | {r['style']} | {r['risk']} | {r['shares']} | "
                  f"{r['cost_price']:.3f} | {r['close_price']:.3f} | {r['cost']:,.0f} | "
                  f"{r['position_value']:,.0f} | {r['pnl']:+,.0f} | {r['pnl_pct']:+.2f}% | "
                  f"{status_icon} | {r['source']} |")
    md.append('')

    # 三、对冲头寸明细
    md.append('## 三、对冲头寸明细（IF 股指期货）')
    md.append('')
    if report['hedge_rows']:
        md.append('| 合约 | 方向 | 手数 | 乘数 | 开仓价 | 盯市价 | 名义本金 | 对冲盈亏 | 盈亏率 | 标的指数 |')
        md.append('|------|------|------|------|--------|--------|----------|----------|--------|----------|')
        for h in report['hedge_rows']:
            md.append(f"| {h['instrument']} | {h['direction']} | {h['contracts']} | {h['multiplier']} | "
                      f"{h['entry_price']:.2f} | {h['mark_price']:.2f} | ¥{h['notional']:,.0f} | "
                      f"{h['hedge_pnl']:+,.0f} | {h['hedge_pnl_pct']:+.2f}% | {h['underlying']} |")
        md.append('')
        md.append(f"**对冲规模合计**: ¥{report['hedge_summary']['total_notional']:,.0f}")
        md.append(f"**对冲盈亏合计**: {report['hedge_summary']['total_hedge_pnl']:+,.0f}")
        md.append('')
    else:
        md.append('*暂无对冲头寸*')
        md.append('')

    # 四、风格表现归因
    md.append('## 四、风格表现归因')
    md.append('')
    md.append('| 风格 | 持仓数 | 成本 | 市值 | 盈亏 | 盈亏率 | 占比 |')
    md.append('|------|--------|------|------|------|--------|------|')
    total_value = report['stock_summary']['total_value']
    for s in report['style_attribution']:
        weight = (s['value'] / total_value * 100) if total_value > 0 else 0
        md.append(f"| {s['style']} | {s['count']} | ¥{s['cost']:,.0f} | ¥{s['value']:,.0f} | "
                  f"{s['pnl']:+,.0f} | {s['pnl_pct']:+.2f}% | {weight:.1f}% |")
    md.append('')

    # 五、风险指标（顶级对冲基金视角）
    md.append('## 五、风险指标（顶级对冲基金视角）')
    md.append('')
    risk = report['risk_metrics']
    md.append('| 指标 | 数值 | 评级 |')
    md.append('|------|------|------|')
    md.append(f"| 平均收益 | {risk['avg_return_pct']:+.2f}% | {'良好' if risk['avg_return_pct'] > 0.3 else '中性' if risk['avg_return_pct'] > -0.3 else '偏弱'} |")
    md.append(f"| 持仓波动率 | {risk['volatility_pct']:.2f}% | {'可控' if risk['volatility_pct'] < 1.5 else '偏高'} |")
    md.append(f"| 最大跌幅 | {risk['max_drawdown_pct']:+.2f}% | {'安全' if risk['max_drawdown_pct'] > -5 else '关注' if risk['max_drawdown_pct'] > -10 else '警戒'} |")
    md.append(f"| 对冲比例 | {risk['hedge_ratio']*100:.1f}% | {'充分' if risk['hedge_ratio'] > 0.4 else '不足' if risk['hedge_ratio'] < 0.2 else '适中'} |")
    md.append(f"| 估算 Beta | {risk['portfolio_beta']:.3f} | {'达标' if risk['portfolio_beta'] < 0.5 else '偏高'} |")
    md.append(f"| 对冲有效性 | {risk['hedge_effectiveness']:.1f}% | {'良好' if risk['hedge_effectiveness'] > 40 else '不足'} |")
    md.append(f"| 止损触发 | {risk['stop_loss_status'].get('STOP_LOSS_TRIGGERED', 0)} 只 | {'触发' if risk['stop_loss_status'].get('STOP_LOSS_TRIGGERED', 0) > 0 else '正常'} |")
    md.append(f"| 预警标的 | {risk['stop_loss_status'].get('WARNING', 0)} 只 | {'关注' if risk['stop_loss_status'].get('WARNING', 0) > 0 else '正常'} |")
    md.append('')

    # 六、次日交易计划（策略建议+风控预案）
    md.append('## 六、次日交易计划（策略建议+风控预案）')
    md.append('')
    plan = report['next_day_plan']
    md.append(f"### 6.1 市场判断与策略方向")
    md.append('')
    md.append(f"- **市场状态**: {plan['market_regime']}")
    md.append(f"- **核心策略**: {plan['strategy']}")
    md.append(f"- **对冲建议**: {plan['hedge_advice']}")
    md.append('')

    # 当前阶段信息
    if plan['current_phase']:
        cp = plan['current_phase']
        md.append(f"### 6.2 当前建仓阶段")
        md.append('')
        md.append(f"- 阶段: {cp.get('name', '')}")
        md.append(f"- 开始日期: {cp.get('start', '')}")
        md.append(f"- 持续天数: {cp.get('duration_days', '')} 天")
        md.append(f"- 资金比例: {cp.get('capital_ratio', 0)*100:.1f}%")
        md.append(f"- 资金金额: ¥{cp.get('capital_amount', 0):,.0f}")
        md.append(f"- 策略说明: {cp.get('strategy', '')}")
        md.append('')

    if plan['next_phase']:
        np_ = plan['next_phase']
        md.append(f"### 6.3 下一阶段预告")
        md.append('')
        md.append(f"- 阶段: {np_.get('name', '')}")
        md.append(f"- 开始日期: {np_.get('start', '')}")
        md.append(f"- 资金比例: {np_.get('capital_ratio', 0)*100:.1f}%")
        md.append(f"- 策略说明: {np_.get('strategy', '')}")
        md.append('')

    # 风控预案
    md.append(f"### 6.4 风控预案（触发条件 → 应对措施）")
    md.append('')
    md.append('| 优先级 | 触发条件 | 应对措施 |')
    md.append('|--------|----------|----------|')
    for sc in plan['risk_scenarios']:
        md.append(f"| {sc['priority']} | {sc['trigger']} | {sc['action']} |")
    md.append('')

    # 标的级建议
    md.append(f"### 6.5 标的级建议")
    md.append('')
    if plan['winners']:
        md.append(f"**领涨标的（建议持有或减仓获利）**:")
        md.append('')
        for w in plan['winners']:
            md.append(f"- {w['code']} {w['name']}: 盈亏 {w['pnl']:+,.0f} ({w['pnl_pct']:+.2f}%)")
        md.append('')
    if plan['losers']:
        md.append(f"**落后标的（建议关注基本面，评估是否止损）**:")
        md.append('')
        for l in plan['losers']:
            md.append(f"- {l['code']} {l['name']}: 盈亏 {l['pnl']:+,.0f} ({l['pnl_pct']:+.2f}%)")
        md.append('')

    if plan['stop_loss_triggered'] > 0:
        md.append(f"> ⚠️ **风控警示**: 当前有 {plan['stop_loss_triggered']} 只标的触发止损，{plan['warning_count']} 只预警，次日开盘前必须评估！")
        md.append('')

    # 七、顶级对冲基金视角复盘
    md.append('## 七、顶级对冲基金视角复盘')
    md.append('')
    md.append('### 7.1 Bridgewater 风险平价视角')
    md.append('')
    md.append(f"- 持仓分散度: {report['stock_summary']['position_count']} 只标的覆盖多种风格")
    md.append(f"- 风格集中度: {len(report['style_attribution'])} 个风格板块")
    md.append(f"- 对冲覆盖率: {risk['hedge_ratio']*100:.1f}%（目标 ≥40%）")
    md.append(f"- Beta 暴露: {risk['portfolio_beta']:.3f}（目标 ≤0.5）")
    md.append('')

    md.append('### 7.2 Renaissance 信号驱动视角')
    md.append('')
    md.append(f"- 平均收益: {risk['avg_return_pct']:+.2f}%")
    md.append(f"- 持仓波动率: {risk['volatility_pct']:.2f}%")
    md.append(f"- 最大跌幅: {risk['max_drawdown_pct']:+.2f}%")
    md.append(f"- 信号源: 第一天建仓成本基准 + 当日收盘价")
    md.append('')

    md.append('### 7.3 Two Sigma 风险归因视角')
    md.append('')
    # 风格归因
    if report['style_attribution']:
        best_style = report['style_attribution'][0]
        worst_style = report['style_attribution'][-1]
        md.append(f"- 领涨风格: {best_style['style']} ({best_style['pnl_pct']:+.2f}%)")
        md.append(f"- 落后风格: {worst_style['style']} ({worst_style['pnl_pct']:+.2f}%)")
        md.append(f"- 风格差异: {best_style['pnl_pct'] - worst_style['pnl_pct']:.2f} 个百分点")
    md.append('')

    md.append('### 7.4 操作纪律检查')
    md.append('')
    md.append('- [x] 成本基准已锁定第一天建仓成本')
    md.append('- [x] 对冲品种已纳入盈亏计算')
    md.append('- [x] 风格归因已完成')
    md.append('- [x] 风险指标已计算')
    md.append('- [x] 止损状态已扫描')
    md.append('- [x] 次日策略建议已生成')
    md.append('- [x] 风控预案已就绪')
    md.append('')

    md.append('---')
    md.append('')
    md.append(f"**数据源优先级**: Wind MCP (P0) → iFinD MCP (P1) → 兜底估算 (P2)")
    # 数据源统计
    source_stats = defaultdict(int)
    for r in report['stock_rows']:
        source_stats[r['source']] += 1
    if source_stats:
        stats_str = ' / '.join(f'{k}: {v}' for k, v in sorted(source_stats.items(), key=lambda x: -x[1]))
        md.append(f"**本次数据源分布**: {stats_str}")
    md.append(f"**成本基准**: {report['day1_date']} 第一阶段建仓成交价（actual_amount / shares）")
    md.append(f"**报告生成**: 自动化脚本 scripts/daily_closing_review.py")
    md.append('')

    return '\n'.join(md)


def main():
    args = parse_args()
    report_date = args.date or datetime.now().strftime('%Y-%m-%d')
    print(f'[开始生成] 报告日期: {report_date}')

    # 1. 加载建仓计划，提取第一天成本基准
    build_plan = load_json(BUILD_PLAN_PATH)
    if not build_plan:
        print('[错误] 无法加载建仓计划文件')
        return 1
    day1_positions = extract_day1_cost_basis(build_plan)
    print(f'[成本基准] 加载 {len(day1_positions)} 个标的第一天建仓成本')

    # 2. 加载对冲成交记录
    hedge_orders = load_hedge_positions(report_date)
    print(f'[对冲头寸] 加载 {len(hedge_orders)} 个对冲订单')

    # 3. 获取收盘价
    symbols = list(day1_positions.keys()) + ['sh000300', 'sh000852']
    # 构建成本价字典，用于 Wind MCP 数据合理性校验
    cost_basis = {code: pos['cost_price'] for code, pos in day1_positions.items()}
    # 指数成本价用对冲开仓价作为参考
    cost_basis['sh000300'] = 4701.05  # IF 开仓价
    cost_basis['sh000852'] = 4701.05  # 简化处理

    prices = fetch_close_prices(symbols, cost_basis=cost_basis)
    print(f'[收盘价] 获取 {len(prices)} 个标的价格')

    # 4. 计算盈亏
    stock_rows, stock_summary = calc_stock_pnl(day1_positions, prices)
    hedge_rows, hedge_summary = calc_hedge_pnl(hedge_orders, prices)

    # 5. 风格归因
    style_attribution = analyze_style_attribution(stock_rows)

    # 6. 风险指标
    risk_metrics = calc_risk_metrics(stock_rows, hedge_rows, stock_summary, hedge_summary)

    # 7. 次日交易计划
    next_day_plan = generate_next_day_plan(stock_rows, hedge_rows, risk_metrics, build_plan, report_date)

    # 组装报告
    report = {
        'report_date': report_date,
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'day1_date': DAY1_DATE,
        'stock_rows': stock_rows,
        'stock_summary': stock_summary,
        'hedge_rows': hedge_rows,
        'hedge_summary': hedge_summary,
        'style_attribution': style_attribution,
        'risk_metrics': risk_metrics,
        'next_day_plan': next_day_plan,
    }

    # 8. 渲染 Markdown
    md_content = render_markdown(report)

    # 9. 保存报告
    if args.dry_run:
        print('\n' + '=' * 70)
        print(md_content)
        print('=' * 70)
        print('[Dry-run] 未保存文件')
    else:
        # 保存到每日报告归档/YYYY/MM/DD/
        date_obj = datetime.strptime(report_date, '%Y-%m-%d')
        archive_dir = os.path.join(ARCHIVE_ROOT, date_obj.strftime('%Y'), date_obj.strftime('%m'), date_obj.strftime('%d'))
        os.makedirs(archive_dir, exist_ok=True)

        md_path = os.path.join(archive_dir, f'收盘盈亏复盘_{report_date.replace("-", "")}.md')
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write(md_content)
        print(f'[保存成功] Markdown: {md_path}')

        # 同时保存 JSON 版本（便于程序化分析）
        json_path = os.path.join(archive_dir, f'收盘盈亏复盘_{report_date.replace("-", "")}.json')
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False, default=str)
        print(f'[保存成功] JSON: {json_path}')

    # 控制台摘要
    print('\n' + '=' * 70)
    print(f'[报告摘要] {report_date}')
    print(f'  股票盈亏: {stock_summary["total_pnl"]:+,.0f} ({stock_summary["total_pnl_pct"]:+.2f}%)')
    print(f'  对冲盈亏: {hedge_summary["total_hedge_pnl"]:+,.0f}')
    print(f'  净盈亏:   {risk_metrics["net_pnl"]:+,.0f} ({risk_metrics["net_pnl_pct"]:+.2f}%)')
    print(f'  市场状态: {next_day_plan["market_regime"]}')
    print(f'  止损触发: {next_day_plan["stop_loss_triggered"]} 只')
    print(f'  预警标的: {next_day_plan["warning_count"]} 只')
    print('=' * 70)

    return 0


if __name__ == '__main__':
    sys.exit(main())
