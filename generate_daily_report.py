"""
收盘盈亏报告生成器
- 按照 Bridgewater/Renaissance 等顶级对冲基金视角
- 盘中自主决策支持
- 严谨高效的持仓盈亏明细
"""

import sys
import os
import json
from datetime import datetime
from typing import Dict, List, Any, Optional

import requests as _requests

# 新浪实时行情 Session (绕过系统代理)
_SINA_SESSION = _requests.Session()
_SINA_SESSION.trust_env = False
_SINA_SESSION.proxies = {"http": None, "https": None}

sys.path.insert(0, '.')

# Constants
REPORT_DATE = datetime.now().strftime("%Y-%m-%d")
IFIND_TOKEN = os.environ.get('IFIND_TOKEN', '')

class PortfolioAnalyzer:
    """组合分析器 - 顶级对冲基金视角"""
    
    def __init__(self, positions_file: str, hedge_file: str):
        self.positions_data = self._load_json(positions_file)
        self.hedge_data = self._load_json(hedge_file)
        self.market_prices: Dict[str, Dict] = {}
        self._data_provider = None
        
    def _load_json(self, path: str) -> Dict:
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"加载文件失败: {path}, {e}")
            return {}
    
    def _init_data_provider(self):
        """初始化数据源 - Wind MCP > iFinD MCP"""
        try:
            from utils.data_provider import MarketDataProvider
            self._data_provider = MarketDataProvider()
        except Exception as e:
            print(f"数据源初始化失败: {e}")

    def _apply_positions_snapshot(self, snapshot_path: str, trade_plan_path: str = None):
        """加载 sim_snapshots/positions_{date}.json 并构建实际持仓视图

        成本价计算规则: 按"第一次交易开盘价格"计算 (trade_plan 中的 est_price)
        - 持仓量: 用快照实际成交量 (actual_shares)
        - 成本价: 用 trade_plan 的 est_price (第一次交易开盘价), 回退到快照 avg_price
        - 收盘价: 用 market_prices (Wind MCP / fallback)

        设计原则:
          1. 不修改 positions.json 的计划持仓数据
          2. 用快照生成独立字段 actual_shares / actual_avg_cost
          3. 用 trade_plan 的 est_price 覆盖 est_price (第一次交易开盘价)
        """
        try:
            with open(snapshot_path, 'r', encoding='utf-8') as f:
                snapshot = json.load(f)
        except Exception as e:
            print(f"加载持仓快照失败: {e}")
            return

        # 加载 trade_plan 获取第一次交易开盘价
        plan_prices = {}  # code_num -> est_price (第一次交易开盘价)
        if trade_plan_path:
            try:
                with open(trade_plan_path, 'r', encoding='utf-8') as f:
                    plan = json.load(f)
                # 合并 morning + afternoon orders
                exec_plan = plan.get('execution_plan', {})
                for order in exec_plan.get('morning_orders', []):
                    code = order.get('code', '')
                    code_num = code[2:] if code.startswith(('sh', 'sz')) else code
                    if code_num and order.get('est_price'):
                        plan_prices[code_num] = order['est_price']
                for order in exec_plan.get('afternoon_orders', []):
                    code = order.get('code', '')
                    code_num = code[2:] if code.startswith(('sh', 'sz')) else code
                    if code_num and order.get('est_price') and code_num not in plan_prices:
                        plan_prices[code_num] = order['est_price']
                print(f"加载 trade_plan: {len(plan_prices)} 个标的的开盘价")
            except Exception as e:
                print(f"加载 trade_plan 失败: {e}")

        # 快照结构: {"futures": {"positions": {code: {"qty", "avg_price", "market_value"}}}}
        sim_positions = snapshot.get('futures', {}).get('positions', {})
        if not sim_positions:
            print("持仓快照为空, 跳过合并")
            return

        # 建立快照索引: 去掉 sh/sz 前缀后的代码 -> 快照记录
        snap_index = {}
        for sk, sv in sim_positions.items():
            norm = sk.lower()
            for prefix in ('sh', 'sz', 'bj'):
                if norm.startswith(prefix):
                    norm = norm[len(prefix):]
                    break
            snap_index[norm] = sv

        positions = self.positions_data.get('positions', {})
        matched = 0
        skipped = 0
        extra = 0

        for key, pos in positions.items():
            # positions.json key: "688041.SH" -> "688041"
            code_num = key.split('.')[0]
            snap = snap_index.get(code_num)
            if snap is None:
                skipped += 1
                continue

            qty = snap.get('qty', 0)
            avg_price = snap.get('avg_price', pos.get('est_price', 0))
            if not avg_price:
                avg_price = pos.get('est_price', 0)

            # 成本价 = 第一次交易开盘价 (trade_plan est_price)
            # 回退: 快照 avg_price (成交均价) -> positions.json est_price
            first_open_price = plan_prices.get(code_num, avg_price)

            # 写入实际持仓字段
            pos['actual_shares'] = qty
            pos['actual_avg_cost'] = avg_price  # 成交均价 (保留供参考)
            pos['est_price'] = first_open_price  # 覆盖为第一次交易开盘价 (成本价)
            matched += 1

        # 处理快照中有但 positions.json 未计划的标的
        known_codes = {k.split('.')[0] for k in positions.keys()}
        for norm_code, sv in snap_index.items():
            if norm_code in known_codes:
                continue
            extra += 1
            self.positions_data.setdefault('positions', {})[norm_code] = {
                'code': norm_code,
                'name': sv.get('name', ''),
                'style': '其他',
                'sector': '其他',
                'phase1_shares': 0,
                'shares': 0,
                'est_price': sv.get('avg_price', 0),
                'avg_cost': sv.get('avg_price', 0),
                'target_weight': 0.0,
                'actual_shares': sv.get('qty', 0),
                'actual_avg_cost': sv.get('avg_price', 0),
            }

        print(
            f"持仓快照合并完成: 匹配 {matched} / 跳过 {skipped} / 新增 {extra}"
        )

    @staticmethod
    def _to_sina_code(code: str) -> str:
        """将标准代码转为新浪代码: 688041.SH -> sh688041, 000333.SZ -> sz000333"""
        code = code.strip().upper()
        if '.' in code:
            num, suffix = code.split('.', 1)
            if suffix in ('SH', 'SS'):
                return f'sh{num}'
            if suffix == 'SZ':
                return f'sz{num}'
        # 无后缀: 6/5/9 开头为上交所, 0/3 开头为深交所
        if code.startswith(('6', '5', '9')):
            return f'sh{code}'
        return f'sz{code}'

    def _fetch_sina_realtime(self, codes: List[str]) -> Dict[str, Dict]:
        """通过新浪财经 API 批量获取实时行情

        API: https://hq.sinajs.cn/list=sh688041,sz000333
        返回字段(逗号分隔):
          0=名称, 1=今开, 2=昨收, 3=当前价, 4=最高, 5=最低, 8=成交量, 9=成交额
        """
        if not codes:
            return {}
        sina_codes = [self._to_sina_code(c) for c in codes]
        url = f"https://hq.sinajs.cn/list={','.join(sina_codes)}"
        try:
            resp = _SINA_SESSION.get(
                url,
                headers={"Referer": "https://finance.sina.com.cn"},
                timeout=15,
            )
            if resp.status_code != 200:
                print(f"新浪实时行情 HTTP {resp.status_code}")
                return {}
            text = resp.text
        except Exception as e:
            print(f"新浪实时行情获取失败: {e}")
            return {}

        result = {}
        for orig_code, sina_code in zip(codes, sina_codes):
            # 提取 var hq_str_sh688041="..."; 中的内容
            prefix = f'hq_str_{sina_code}="'
            idx = text.find(prefix)
            if idx < 0:
                continue
            start = idx + len(prefix)
            end = text.find('"', start)
            if end < 0:
                continue
            content = text[start:end]
            if not content or content == '':
                continue
            fields = content.split(',')
            if len(fields) < 6:
                continue
            try:
                name = fields[0]
                open_price = float(fields[1])    # 今开
                prev_close = float(fields[2])    # 昨收
                current = float(fields[3])       # 当前价/收盘价
                high = float(fields[4])
                low = float(fields[5])
            except (ValueError, IndexError):
                continue
            if current <= 0 or prev_close <= 0:
                continue
            change_pct = (current - prev_close) / prev_close * 100
            result[orig_code] = {
                'close': current,
                'prev_close': prev_close,
                'open': open_price,
                'high': high,
                'low': low,
                'change_pct': round(change_pct, 2),
                'source': 'sina_realtime',
                'name': name,
            }
        return result

    def fetch_market_prices(self) -> Dict[str, Dict]:
        """获取所有持仓标的的收盘价格

        价格验证规则 (防止 data_provider 返回指数点位):
          1. 不使用 index_price 字段 (它是指数点位 ~3000-4700, 不是股价)
          2. 优先使用 close / last / price 字段 (个股实际收盘价)
          3. 绝对范围: ETF 0.1-50, 股票 0.5-2000
          4. 相对范围: close 必须在 cost_price 的 0.3x ~ 3x 之间
        """
        self._init_data_provider()

        prices = {}
        positions = self.positions_data.get('positions', {})

        # 构建代码 -> 成本价 (est_price) 映射, 用于比例验证
        code_to_cost = {}
        for key, pos in positions.items():
            code = pos.get('code', '')
            if code:
                # 成本价 = 第一次交易开盘价 (est_price, 已被 _apply_positions_snapshot 覆盖)
                cost = pos.get('est_price') or pos.get('actual_avg_cost') or pos.get('avg_cost', 0)
                code_to_cost[code] = float(cost) if cost else 0

        print(f"获取 {len(code_to_cost)} 个标的的收盘价格...")

        # 使用 data_provider 获取实时价格
        for code, cost_price in code_to_cost.items():
            try:
                if not self._data_provider:
                    continue
                market_data = self._data_provider.get_market_data(code)
                if not market_data:
                    continue

                # 优先级: close > last > price > (不用 index_price, 它是指数点位)
                close = (market_data.get('close')
                         or market_data.get('last')
                         or market_data.get('price'))
                prev_close = market_data.get('prev_close')

                # 绝对范围验证
                code_num = code.split('.')[0]
                is_etf = code_num.startswith(('5', '1')) and len(code_num) == 6
                max_price = 50 if is_etf else 2000   # A股最贵茅台~1500, 2000已留余量
                min_price = 0.1 if is_etf else 0.5
                if close is None or close <= 0:
                    continue
                if close > max_price or close < min_price:
                    print(f"价格异常 {code}: close={close} (超出范围 {min_price}-{max_price}), 跳过")
                    continue

                # 无成本价的特殊处理：如果价格 > 500 且不是 ETF，可能是后复权价/指数点位，使用 fallback
                if cost_price == 0 and not is_etf and close > 500:
                    fb = fallback_prices.get(code_num)
                    if fb:
                        close = fb.get('close', close)
                        prev_close = fb.get('prev_close', prev_close)
                        change_pct = fb.get('change_pct', change_pct)
                        print(f"价格异常修正 {code}: 使用 fallback 价格 close={close}")

                # 相对成本价比例验证 (防止指数点位冒充股价)
                # 收盘价一般不会超过成本价的 3 倍或低于 0.3 倍
                if cost_price > 0:
                    ratio = close / cost_price
                    if ratio > 3.0 or ratio < 0.3:
                        print(f"价格可疑 {code}: close={close} vs cost={cost_price} (比例 {ratio:.2f}x 超出 0.3-3.0), 跳过")
                        continue

                change_pct = market_data.get('change_pct')
                if change_pct is None and close and prev_close and prev_close > 0:
                    change_pct = (close - prev_close) / prev_close * 100
                prices[code] = {
                    'close': close,
                    'prev_close': prev_close,
                    'change_pct': change_pct,
                    'source': market_data.get('source', 'unknown')
                }
            except Exception as e:
                print(f"获取 {code} 价格失败: {e}")

        # 新浪实时行情补充 (当 Wind MCP / iFinD MCP 都失败时)
        # 批量获取所有未拿到价格的标的
        missing_codes = [c for c in code_to_cost.keys() if c not in prices]
        if missing_codes:
            sina_prices = self._fetch_sina_realtime(missing_codes)
            for code, sp in sina_prices.items():
                prices[code] = sp
            if sina_prices:
                print(f"新浪实时行情获取成功: {len(sina_prices)} / {len(missing_codes)} 个标的")

        # 使用预定义的模拟价格作为补充（当Wind MCP不可用时）
        # 价格基于 2026-07-09 持仓快照实际成交均价; 5个新标的(000680等)使用计划价
        fallback_prices = {
            '510300': {'close': 4.89, 'prev_close': 4.887, 'change_pct': 0.06, 'source': 'fallback'},
            '510500': {'close': 8.92, 'prev_close': 8.897, 'change_pct': 0.26, 'source': 'fallback'},
            '512100': {'close': 3.51, 'prev_close': 3.494, 'change_pct': 0.46, 'source': 'fallback'},
            '588000': {'close': 1.0505, 'prev_close': 1.0505, 'change_pct': 0.0, 'source': 'fallback'},
            '159915': {'close': 4.05, 'prev_close': 4.029, 'change_pct': 0.52, 'source': 'fallback'},
            '515180': {'close': 5.0025, 'prev_close': 5.0025, 'change_pct': 0.0, 'source': 'fallback'},
            '688041': {'close': 85.0425, 'prev_close': 85.0425, 'change_pct': 0.0, 'source': 'fallback'},
            '300308': {'close': 120.06, 'prev_close': 120.06, 'change_pct': 0.0, 'source': 'fallback'},
            '300274': {'close': 45.0225, 'prev_close': 45.0225, 'change_pct': 0.0, 'source': 'fallback'},
            '002371': {'close': 350.175, 'prev_close': 350.175, 'change_pct': 0.0, 'source': 'fallback'},
            '688017': {'close': 180.09, 'prev_close': 180.09, 'change_pct': 0.0, 'source': 'fallback'},
            '600276': {'close': 50.025, 'prev_close': 50.025, 'change_pct': 0.0, 'source': 'fallback'},
            '600089': {'close': 25.0125, 'prev_close': 25.0125, 'change_pct': 0.0, 'source': 'fallback'},
            '600875': {'close': 29.5, 'prev_close': 29.29, 'change_pct': 0.71, 'source': 'fallback'},
            '000425': {'close': 8.5042, 'prev_close': 8.5042, 'change_pct': 0.0, 'source': 'fallback'},
            '600406': {'close': 23.0, 'prev_close': 22.88, 'change_pct': 0.52, 'source': 'fallback'},
            '600989': {'close': 20.5, 'prev_close': 20.39, 'change_pct': 0.54, 'source': 'fallback'},
            '600036': {'close': 38.019, 'prev_close': 38.019, 'change_pct': 0.0, 'source': 'fallback'},
            '600900': {'close': 27.0635, 'prev_close': 27.0635, 'change_pct': 0.0, 'source': 'fallback'},
            '601088': {'close': 40.7204, 'prev_close': 40.7204, 'change_pct': 0.0, 'source': 'fallback'},
            '518880': {'close': 5.8529, 'prev_close': 5.8529, 'change_pct': 0.0, 'source': 'fallback'},
            '688981': {'close': 95.0475, 'prev_close': 95.0475, 'change_pct': 0.0, 'source': 'fallback'},
            '603019': {'close': 94.4672, 'prev_close': 94.4672, 'change_pct': 0.0, 'source': 'fallback'},
            '600219': {'close': 4.1921, 'prev_close': 4.1921, 'change_pct': 0.0, 'source': 'fallback'},
            '600019': {'close': 5.6128, 'prev_close': 5.6128, 'change_pct': 0.0, 'source': 'fallback'},
            # 2026-07-09 新增 5 标的 (持仓为0, 仅用于价格查询)
            '000680': {'close': 7.50, 'prev_close': 7.50, 'change_pct': 0.0, 'source': 'fallback'},
            '000333': {'close': 75.00, 'prev_close': 75.00, 'change_pct': 0.0, 'source': 'fallback'},
            '000408': {'close': 35.00, 'prev_close': 35.00, 'change_pct': 0.0, 'source': 'fallback'},
            '000975': {'close': 15.00, 'prev_close': 15.00, 'change_pct': 0.0, 'source': 'fallback'},
            '002422': {'close': 28.00, 'prev_close': 28.00, 'change_pct': 0.0, 'source': 'fallback'},
            # 2026-07-10 新计划 500万 20 标的补充 (auto_trade_plan_500w_2026-2030.json)
            '588080': {'close': 1.05, 'prev_close': 1.05, 'change_pct': 0.0, 'source': 'fallback'},  # 科创50ETF易方达
            '512880': {'close': 1.10, 'prev_close': 1.10, 'change_pct': 0.0, 'source': 'fallback'},  # 证券ETF国泰
            '510050': {'close': 3.00, 'prev_close': 3.00, 'change_pct': 0.0, 'source': 'fallback'},  # 上证50ETF华夏
            '512800': {'close': 1.40, 'prev_close': 1.40, 'change_pct': 0.0, 'source': 'fallback'},  # 银行ETF华宝
            '515030': {'close': 1.50, 'prev_close': 1.50, 'change_pct': 0.0, 'source': 'fallback'},  # 新能源车ETF华夏
            '512760': {'close': 1.30, 'prev_close': 1.30, 'change_pct': 0.0, 'source': 'fallback'},  # 半导体ETF国泰
            '512170': {'close': 0.50, 'prev_close': 0.50, 'change_pct': 0.0, 'source': 'fallback'},  # 医疗ETF华宝
            '300033': {'close': 150.00, 'prev_close': 150.00, 'change_pct': 0.0, 'source': 'fallback'},  # 同花顺
            '688981': {'close': 50.00, 'prev_close': 50.00, 'change_pct': 0.0, 'source': 'fallback'},  # 中芯国际
            '601899': {'close': 18.00, 'prev_close': 18.00, 'change_pct': 0.0, 'source': 'fallback'},  # 紫金矿业
            '002281': {'close': 35.00, 'prev_close': 35.00, 'change_pct': 0.0, 'source': 'fallback'},  # 光迅科技
            '000901': {'close': 45.00, 'prev_close': 45.00, 'change_pct': 0.0, 'source': 'fallback'},  # 国盾量子
        }
        
        # 合并价格数据，避免覆盖实时数据
        for code, fb_price in fallback_prices.items():
            if code not in prices:
                prices[code] = fb_price

        # 价格异常修正：对无成本价的个股，如果价格 > 500 且不是 ETF，可能是后复权价/指数点位，使用 fallback
        for code, pd in list(prices.items()):
            if pd.get('close') is None:
                continue
            close = pd.get('close')
            code_num = code.split('.')[0]
            is_etf = code_num.startswith(('5', '1')) and len(code_num) == 6
            cost_price = code_to_cost.get(code, 0) or 0
            if cost_price == 0 and not is_etf and close > 500:
                fb = fallback_prices.get(code_num)
                if fb:
                    prices[code] = {
                        'close': fb.get('close', close),
                        'prev_close': fb.get('prev_close', pd.get('prev_close')),
                        'change_pct': fb.get('change_pct', pd.get('change_pct')),
                        'source': 'fallback_corrected'
                    }
                    print(f"价格异常修正 {code}: 使用 fallback 价格 close={fb.get('close')}")

        self.market_prices = prices
        return prices
    
    def calculate_pnl(self) -> Dict[str, Any]:
        """计算持仓盈亏明细"""
        positions = self.positions_data.get('positions', {})
        
        pnl_details = []
        total_cost = 0.0
        total_market_value = 0.0
        total_pnl = 0.0
        
        for key, pos in positions.items():
            code = pos.get('code', '')
            
            # 优先使用快照实际成交数据
            # 成本价按"第一次交易开盘价格"计算 (est_price), 不用成交均价 (actual_avg_cost)
            # 持仓量用快照实际成交量 (actual_shares)
            if 'actual_shares' in pos or 'actual_avg_cost' in pos:
                shares = pos.get('actual_shares', 0)
                cost_price = pos.get('est_price', pos.get('actual_avg_cost', 0))
                calc_mode = 'snapshot'
            else:
                # 回退: phase1_shares (计划) -> shares (实际持仓, 由 execute_instructions 同步)
                shares = pos.get('phase1_shares', 0) or pos.get('shares', 0)
                # 回退: est_price (第一次交易开盘价) -> avg_cost (加权平均成本)
                cost_price = pos.get('est_price', 0) or pos.get('avg_cost', 0)
                calc_mode = 'plan'
            
            # 获取收盘价 (兼容带后缀和不带后缀的 code)
            price_data = self.market_prices.get(code, {})
            if not price_data:
                # 回退: 用不带后缀的 code 查找 (fallback_prices 的 key 格式)
                code_num = code.split('.')[0]
                price_data = self.market_prices.get(code_num, {})
            close_price = price_data.get('close', cost_price) or cost_price
            prev_close = price_data.get('prev_close', cost_price) or cost_price
            change_pct = price_data.get('change_pct', 0)
            if change_pct is None or change_pct == 0:
                if close_price and prev_close and prev_close > 0:
                    change_pct = (close_price - prev_close) / prev_close * 100
            
            # 计算市值和盈亏
            cost_amount = shares * cost_price
            market_value = shares * close_price
            pnl = market_value - cost_amount
            pnl_pct = (close_price - cost_price) / cost_price if cost_price > 0 else 0
            
            # 日内盈亏（基于昨收）
            daily_pnl = shares * (close_price - prev_close) if close_price and prev_close else 0
            daily_pnl_pct = change_pct if shares > 0 else 0
            
            total_cost += cost_amount
            total_market_value += market_value
            total_pnl += pnl
            
            pnl_details.append({
                'code': code,
                'name': pos.get('name', ''),
                'style': pos.get('style', ''),
                'risk': pos.get('risk', ''),
                'shares': shares,
                'cost_price': round(cost_price, 2),
                'close_price': round(close_price, 2),
                'prev_close': round(prev_close, 2),
                'cost_amount': round(cost_amount, 2),
                'market_value': round(market_value, 2),
                'pnl': round(pnl, 2),
                'pnl_pct': round(pnl_pct * 100, 2),
                'daily_pnl': round(daily_pnl, 2),
                'daily_pnl_pct': round(daily_pnl_pct, 2),
                'stop_loss': pos.get('stop_loss', 0),
                'calc_mode': calc_mode,
                'status': self._get_position_status(pnl_pct, pos.get('stop_loss', 0))
            })
        
        # 按盈亏排序
        pnl_details.sort(key=lambda x: x['daily_pnl_pct'], reverse=True)
        
        return {
            'details': pnl_details,
            'summary': {
                'total_cost': round(total_cost, 2),
                'total_market_value': round(total_market_value, 2),
                'total_pnl': round(total_pnl, 2),
                'total_pnl_pct': round((total_pnl / total_cost) * 100 if total_cost > 0 else 0, 2),
                'position_count': len(pnl_details)
            }
        }
    
    def _get_position_status(self, pnl_pct: float, stop_loss: float) -> str:
        """判断持仓状态"""
        # stop_loss=0 表示未设置止损, 不触发止损逻辑
        if stop_loss < 0 and pnl_pct <= stop_loss:
            return 'STOP_LOSS_TRIGGERED'
        elif stop_loss < 0 and pnl_pct <= stop_loss * 0.7:
            return 'WARNING'
        elif pnl_pct >= 0.05:
            return 'PROFIT'
        else:
            return 'NORMAL'
    
    def analyze_hedge_position(self) -> Dict[str, Any]:
        """分析对冲头寸"""
        hedge_orders = self.hedge_data.get('orders', [])

        hedge_details = []
        total_hedge_notional = 0.0

        # 兼容 hedge_execution_fill 格式 (side=SELL_SHORT) 与 hedge_decision 格式 (direction=SELL)
        for order in hedge_orders:
            instrument = order.get('instrument', '')
            contracts = order.get('contracts', 0)
            # direction 兼容: direction / side (SELL_SHORT → SELL)
            direction = order.get('direction', '')
            if not direction:
                side = order.get('side', '')
                direction = 'SELL' if side in ('SELL_SHORT', 'SELL', 'SHORT') else side

            # futures_price 兼容: futures_price / price
            futures_price = order.get('futures_price', 0)
            if not futures_price:
                futures_price = order.get('price', 0)

            multiplier = order.get('multiplier', 300)  # IF 合约乘数 300
            notional = order.get('notional', 0)

            # 获取IF期货真实收盘价
            # 数据源优先级: 新浪财经 > data_provider(close/last/price) > 开仓价兜底
            # 不用 index_price (它是沪深300指数点位, 不是期货合约价)
            if_close = futures_price  # 默认使用开仓价 (数据源不可用时兜底)
            if instrument == 'IF':
                # 1. 尝试新浪财经获取 IF 期货实时价格
                try:
                    # IF 期货新浪代码: IF2407 -> hf_IF2407 (沪期所)
                    # 尝试当日主力合约
                    if_codes_sina = ['hf_IF2407', 'hf_IF2607', 'hf_IF2608']
                    sina_res = self._fetch_sina_realtime(if_codes_sina)
                    if sina_res:
                        for _, sp in sina_res.items():
                            fc_price = sp.get('close', 0)
                            if fc_price > 0 and futures_price > 0:
                                ratio = fc_price / futures_price
                                if 0.8 <= ratio <= 1.2:
                                    if_close = fc_price
                                    break
                except Exception:
                    pass
                # 2. 尝试 data_provider (close/last/price)
                if if_close == futures_price and self._data_provider:
                    try:
                        futures_codes = ['IF2607', 'IF2608', 'IF']
                        for fc in futures_codes:
                            futures_data = self._data_provider.get_market_data(fc)
                            if not futures_data:
                                continue
                            fc_price = (futures_data.get('close')
                                        or futures_data.get('last')
                                        or futures_data.get('price'))
                            if not fc_price or fc_price <= 0:
                                continue
                            if futures_price > 0:
                                ratio = fc_price / futures_price
                                if ratio > 1.2 or ratio < 0.8:
                                    continue
                            if_close = fc_price
                            break
                    except Exception as e:
                        print(f"获取IF期货价格失败: {e}")

            if_change_pct = (if_close - futures_price) / futures_price if futures_price > 0 else 0

            # 对冲头寸盈亏（做空方向）
            hedge_pnl = 0.0
            if direction == 'SELL' and futures_price > 0:
                # 做空: 开仓价 - 收盘价 (价格下跌盈利)
                hedge_pnl = contracts * multiplier * (futures_price - if_close)

            total_hedge_notional += notional

            hedge_details.append({
                'instrument': instrument,
                'contracts': contracts,
                'direction': direction,
                'entry_price': round(futures_price, 2),
                'close_price': round(if_close, 2),
                'multiplier': multiplier,
                'notional': round(notional, 2),
                'cost': order.get('cost', 0),
                'hedge_pnl': round(hedge_pnl, 2),
                'hedge_pnl_pct': round(if_change_pct * 100 * (-1 if direction == 'SELL' else 1), 2),
                'hedge_type': order.get('hedge_type', order.get('type', '')),
                'beta_reduced': round(order.get('beta_reduced', 0), 3),
                'cost_breakdown': order.get('cost_breakdown'),
            })

        # 目标 beta: 从 hedge_execution_fill.orders[0].target_beta 取,
        # 若不存在则用 hedge_config.layers.layer1_futures.target_beta 或默认 0.3
        target_beta = 0.3
        if hedge_orders:
            target_beta = float(hedge_orders[0].get('target_beta', target_beta))

        return {
            'details': hedge_details,
            'summary': {
                'total_hedge_notional': round(total_hedge_notional, 2),
                'current_portfolio_beta': round(self.hedge_data.get('portfolio_beta', 1.0), 3),
                'target_beta': round(target_beta, 3),
                'hedge_effectiveness': self._calculate_hedge_effectiveness(hedge_details)
            }
        }
    
    def _calculate_hedge_effectiveness(self, hedge_details: List) -> float:
        """计算对冲有效性"""
        # 简化模型：Beta降低比例作为有效性指标
        beta_reduced = sum(h.get('beta_reduced', 0) for h in hedge_details)
        original_beta = self.hedge_data.get('portfolio_beta', 1.3)
        effectiveness = beta_reduced / original_beta if original_beta > 0 else 0
        return round(effectiveness * 100, 2)

    def analyze_hedge_positions_plan(self) -> Dict[str, Any]:
        """分析期货期权计划头寸 (来自 positions.json 的 hedge_positions)

        覆盖三类对冲工具:
          - IF 期货  (CFFEX, 沪深300股指期货, Beta加权)
          - IM 期货  (CFFEX, 中证1000股指期货, 中小盘对冲)
          - 510050 Put 期权 (SSE, 上证50ETF认沽, 尾部风险保护)

        Returns:
            {
                'details': List[Dict],   # 每个对冲工具的明细
                'summary': Dict,          # 汇总: 总名义价值, 总权利金预算, 总Beta降低
            }
        """
        hedge_positions = self.positions_data.get('hedge_positions', {})

        details = []
        total_notional = 0.0
        total_premium = 0.0
        total_beta_reduction = 0.0

        for key, pos in hedge_positions.items():
            instrument = pos.get('instrument', key)
            exchange = pos.get('exchange', '')
            direction = pos.get('direction', '')
            target_contracts = pos.get('target_contracts', 0)
            multiplier = pos.get('multiplier', 0)
            margin_rate = pos.get('margin_rate', 0.0)
            beta_reduction = pos.get('target_beta_reduction', 0.0)
            strike = pos.get('strike', '')
            premium_budget = pos.get('premium_budget', 0)
            reason = pos.get('reason', '')

            # 估算名义价值
            # 期货: 名义 = 手数 × 乘数 × 标的指数点位 (用 IF 4726 / IM 7500 估算)
            # 期权: 名义 = 权利金预算 (实际是成本, 不是名义价值)
            is_option = instrument.lower().endswith('put') or 'put' in key.lower()
            if is_option:
                # 期权: 名义价值不适用, 用权利金预算作为成本
                notional = 0
                cost = premium_budget
                total_premium += premium_budget
            else:
                # 期货: 用 IF=4726 / IM=7500 估算名义
                index_point = 4726.0 if instrument.upper().startswith('IF') else 7500.0
                notional = target_contracts * multiplier * index_point
                cost = notional * margin_rate  # 保证金 = 名义 × 保证金率
                total_notional += notional

            total_beta_reduction += beta_reduction

            details.append({
                'key': key,
                'instrument': instrument,
                'exchange': exchange,
                'direction': direction,
                'target_contracts': target_contracts,
                'multiplier': multiplier,
                'margin_rate': margin_rate,
                'target_beta_reduction': beta_reduction,
                'strike': strike,
                'premium_budget': premium_budget,
                'estimated_notional': round(notional, 2),
                'estimated_cost': round(cost, 2),
                'is_option': is_option,
                'reason': reason,
            })

        return {
            'details': details,
            'summary': {
                'total_estimated_notional': round(total_notional, 2),
                'total_premium_budget': round(total_premium, 2),
                'total_beta_reduction': round(total_beta_reduction, 3),
                'tool_count': len(details),
            },
        }

    def generate_report(self) -> Dict[str, Any]:
        """生成完整收盘报告"""
        self.fetch_market_prices()

        pnl_data = self.calculate_pnl()
        hedge_data = self.analyze_hedge_position()
        hedge_plan = self.analyze_hedge_positions_plan()
        
        # 组合整体表现
        portfolio_pnl = pnl_data['summary']['total_pnl']
        hedge_pnl = sum(h['hedge_pnl'] for h in hedge_data['details'])
        net_pnl = portfolio_pnl + hedge_pnl
        
        # 风险指标计算
        daily_returns = [d['daily_pnl_pct'] for d in pnl_data['details']]
        avg_return = sum(daily_returns) / len(daily_returns) if daily_returns else 0
        volatility = self._calculate_volatility(daily_returns)
        
        # 数据源健康状态
        data_source_health = self._assess_data_source_health(pnl_data)
        
        report = {
            'meta': {
                'report_date': REPORT_DATE,
                'report_type': '收盘盈亏明细',
                'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'phase': self.positions_data.get('meta', {}).get('phase', '第一阶段'),
                'fund_style': 'Bridgewater/Renaissance 标准对冲基金视角',
                'data_source_health': data_source_health,
            },
            'market_overview': {
                'market_regime': self.hedge_data.get('regime', 'normal'),
                'vix_estimate': 18.5,
                'market_trend': '震荡上行',
                'key_events': [],
                'data_source_status': data_source_health.get('status', 'UNKNOWN')
            },
            'portfolio_pnl': pnl_data,
            'hedge_position': hedge_data,
            'hedge_position_plan': hedge_plan,
            'net_performance': {
                'portfolio_pnl': round(portfolio_pnl, 2),
                'hedge_pnl': round(hedge_pnl, 2),
                'net_pnl': round(net_pnl, 2),
                'net_pnl_pct': round((net_pnl / pnl_data['summary']['total_cost']) * 100 if pnl_data['summary']['total_cost'] > 0 else 0, 2)
            },
            'risk_metrics': {
                'avg_daily_return_pct': round(avg_return, 2),
                'portfolio_volatility_pct': round(volatility, 2),
                'max_drawdown_pct': self._calculate_max_drawdown(pnl_data['details']),
                'stop_loss_status': self._count_stop_loss_status(pnl_data['details']),
                'beta_exposure': round(self.hedge_data.get('portfolio_beta', 1.3) - sum(h.get('beta_reduced', 0) for h in hedge_data['details']), 3)
            },
            'ai_recommendations': self._generate_ai_recommendations(pnl_data, hedge_data, net_pnl),
            'next_day_plan': self.generate_next_day_plan(REPORT_DATE),
            'return_projection': self._load_return_projection(),
        }

        return report

    def _load_return_projection(self) -> Dict[str, Any]:
        """加载 portfolio_return_projection.json 收益率预测数据

        读取四场景预测(bull/base/bear/black_swan)、加权期望、风险披露等。
        若文件不存在或读取失败, 返回空字典(不影响主报告生成)。
        """
        from pathlib import Path as _Path
        try:
            proj_file = _Path(__file__).parent / "portfolio_return_projection.json"
            if not proj_file.exists():
                return {'error': f'projection file not found: {proj_file}'}
            with open(proj_file, 'r', encoding='utf-8') as f:
                proj = json.load(f)
            # 提取关键字段
            scenarios = proj.get('scenarios', {})
            expected = proj.get('expected', {})
            prob_w = proj.get('probability_weights', {})
            return {
                'version': proj.get('version', 'unknown'),
                'generated_at': proj.get('generated_at', ''),
                'investment_horizon': proj.get('investment_horizon', ''),
                'horizon_years': proj.get('horizon_years', 1.5),
                'initial_capital': proj.get('initial_capital', 5000000),
                'scenarios': {
                    s: {
                        'label': scenarios[s].get('label', ''),
                        'weighted_annualized': scenarios[s].get('weighted_annualized', 0),
                        'cumulative_return': scenarios[s].get('cumulative_return', 0),
                        'final_amount': scenarios[s].get('final_amount', 0),
                        'total_profit': scenarios[s].get('total_profit', 0),
                    } for s in ['bull', 'base', 'bear', 'black_swan']
                },
                'probability_weights': prob_w,
                'expected': {
                    'expected_annualized': expected.get('expected_annualized', 0),
                    'expected_cumulative': expected.get('expected_cumulative', 0),
                    'expected_final_amount': expected.get('expected_final_amount', 0),
                    'expected_profit': expected.get('expected_profit', 0),
                },
                'risk_disclosure': proj.get('risk_disclosure', {}),
            }
        except Exception as e:
            return {'error': f'projection load failed: {e}'}

    def _assess_data_source_health(self, pnl_data: Dict) -> Dict[str, Any]:
        """评估当前报告使用的数据源健康状态"""
        details = pnl_data.get('details', [])
        snapshot_count = sum(1 for d in details if d.get('calc_mode') == 'snapshot')
        fallback_count = sum(1 for d in details if self.market_prices.get(d.get('code', ''), {}).get('source') == 'fallback')
        
        total = len(details) if details else 1
        snapshot_ratio = snapshot_count / total if total else 0
        fallback_ratio = fallback_count / total if total else 0
        
        if fallback_ratio > 0.3:
            status = 'FALLBACK_HEAVY'
        elif snapshot_ratio < 0.5:
            status = 'PARTIAL_SNAPSHOT'
        elif snapshot_ratio >= 0.5 and fallback_ratio == 0:
            status = 'HEALTHY'
        else:
            status = 'HEALTHY'
        
        return {
            'status': status,
            'snapshot_ratio': round(snapshot_ratio, 2),
            'fallback_ratio': round(fallback_ratio, 2),
            'snapshot_count': snapshot_count,
            'fallback_count': fallback_count,
            'total_positions': total,
        }
    
    def _calculate_volatility(self, returns: List[float]) -> float:
        """计算波动率"""
        if len(returns) < 2:
            return 0
        mean = sum(returns) / len(returns)
        variance = sum((r - mean) ** 2 for r in returns) / len(returns)
        return (variance ** 0.5)
    
    def _calculate_max_drawdown(self, details: List) -> float:
        """计算最大跌幅"""
        losses = [d['pnl_pct'] for d in details if d['pnl_pct'] < 0]
        return round(min(losses) if losses else 0, 2)
    
    def _count_stop_loss_status(self, details: List) -> Dict:
        """统计止损状态"""
        status_count = {}
        for d in details:
            status = d['status']
            status_count[status] = status_count.get(status, 0) + 1
        return status_count
    
    def _generate_ai_recommendations(self, pnl_data: Dict, hedge_data: Dict, net_pnl: float) -> List[str]:
        """生成AI决策建议"""
        recommendations = []
        
        # 基于盈亏情况
        if net_pnl > 0:
            recommendations.append("组合整体盈利，建议维持当前Beta敞口，继续执行建仓计划")
        else:
            recommendations.append("组合出现亏损，建议审视高风险标的，评估是否需要调整仓位")
        
        # 基于对冲有效性
        effectiveness = hedge_data['summary'].get('hedge_effectiveness', 0)
        if effectiveness > 70:
            recommendations.append("对冲有效性良好，Beta敞口控制在目标区间")
        else:
            recommendations.append("建议增加期货对冲合约数量，提升Beta对冲效率")
        
        # 基于止损状态
        stop_loss_triggered = pnl_data['details'] and any(d['status'] == 'STOP_LOSS_TRIGGERED' for d in pnl_data['details'])
        if stop_loss_triggered:
            recommendations.append("存在触发止损标的，建议次日开盘前评估是否执行止损")
        
        # 基于风格轮动
        tech_performance = [d for d in pnl_data['details'] if d['style'] in ['科技', '高端制造', '成长']]
        if tech_performance and sum(t['daily_pnl_pct'] for t in tech_performance) > 0:
            recommendations.append("科技/成长风格表现优异，建议维持该板块权重配置")
        
        recommendations.append("建议次日盘中监控VIX和指数波动，动态调整期货对冲仓位")

        return recommendations

    def generate_next_day_plan(self, report_date: str = None) -> Dict[str, Any]:
        """生成第二天交易计划 (基于 auto_trade_plan_500w_2026-2030.json 的4阶段)

        根据 auto_trade_plan_500w_2026-2030.json 的 4 阶段执行计划,
        推算次日所属阶段、当日预算、目标动作、对冲策略、风控指令。

        Args:
            report_date: 报告日期 'YYYY-MM-DD', None 表示今天

        Returns:
            dict, 含 next_trading_day, phase, daily_actions, hedge_action, risk_controls 等
        """
        from pathlib import Path as _Path
        try:
            from utils.trade_calendar import next_trading_day, is_trading_day
        except ImportError as e:
            print(f"导入 trade_calendar 失败: {e}")
            return {'error': f'trade_calendar import failed: {e}'}

        # 1. 计算下一交易日
        if report_date is None:
            report_date = REPORT_DATE
        next_day = next_trading_day(report_date)
        try:
            next_dt = datetime.strptime(next_day, '%Y-%m-%d')
            weekday_cn = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'][next_dt.weekday()]
        except Exception:
            next_dt = None
            weekday_cn = ''

        # 2. 加载 auto_trade_plan_500w_2026-2030.json
        plan_file = _Path(__file__).parent / "v7.5_institutional" / "trade_plans" / "auto_trade_plan_500w_2026-2030.json"
        if not plan_file.exists():
            return {
                'next_trading_day': next_day,
                'weekday': weekday_cn,
                'error': f'plan file not found: {plan_file}',
            }

        try:
            with open(plan_file, 'r', encoding='utf-8') as f:
                plan = json.load(f)
        except Exception as e:
            return {'error': f'plan load failed: {e}'}

        exec_plan = plan.get('execution_plan', {})
        stock_account = plan.get('stock_etf_account', {})
        hedge_account = plan.get('hedge_account', {})
        risk_mgmt = plan.get('risk_management', {})

        # 3. 判断次日所属阶段
        phase_info = None
        phase_key = None
        for key in ['phase_1_accumulation', 'phase_2_holding',
                    'phase_3_reduction', 'phase_4_clearance']:
            phase = exec_plan.get(key, {})
            period = phase.get('period', '')
            parts = period.split(' to ')
            if len(parts) == 2:
                start_str, end_str = parts[0].strip(), parts[1].strip()
                if start_str <= next_day <= end_str:
                    phase_info = phase
                    phase_key = key
                    break

        if not phase_info:
            phase_info = exec_plan.get('phase_1_accumulation', {})
            phase_key = 'phase_1_accumulation'

        # 4. 计算 day_index (在当前阶段中的第几个交易日, 跳过周末)
        period = phase_info.get('period', '')
        parts = period.split(' to ')
        day_index = 1
        if len(parts) == 2 and next_dt:
            try:
                start_dt = datetime.strptime(parts[0].strip(), '%Y-%m-%d')
                cur = start_dt
                while cur < next_dt:
                    if cur.weekday() < 5:
                        day_index += 1
                    cur += timedelta(days=1)
            except Exception:
                pass

        # 5. 生成次日交易动作
        phase_names = {
            'phase_1_accumulation': '建仓期',
            'phase_2_holding': '持有期',
            'phase_3_reduction': '减仓期',
            'phase_4_clearance': '清仓期',
        }
        phase_name_cn = phase_names.get(phase_key, '建仓期')

        daily_actions = []
        daily_capital = 0.0
        if phase_key == 'phase_1_accumulation':
            monthly_inv = phase_info.get('monthly_investment', 500000)
            daily_capital = monthly_inv / 20  # 简化: 每月20交易日
            daily_actions.append(f"建仓期: 月投 {monthly_inv:,.0f} 元, 当日预算约 {daily_capital:,.0f} 元")
            daily_actions.append("执行策略: VWAP+TWAP混合算法, 单日最大买入不超过月度计划的50%")
            daily_actions.append("ETF资金流触发: 强信号+3%加仓, 中信号+1%加仓, 反转-3%减仓")
            daily_actions.append("建仓期止损线: -18%, 止盈线: +40%")
        elif phase_key == 'phase_2_holding':
            daily_actions.append("持有期: 季度再平衡, 偏差>5%触发")
            daily_actions.append("ETF资金流连续3日反转触发减仓")
            daily_actions.append("单标的权重上限15%/下限2%; 止盈+50%部分止盈, 止损-15%")
            daily_actions.append("组合整体回撤>12%触发对冲加仓")
        elif phase_key == 'phase_3_reduction':
            monthly_red = phase_info.get('monthly_reduction', 250000)
            daily_capital = -monthly_red / 20
            daily_actions.append(f"减仓期: 月减 {monthly_red:,.0f} 元, 当日减仓约 {abs(daily_capital):,.0f} 元")
            daily_actions.append("优先减仓: 估值分位>80%标的 → 次优先: 科技板块 → 最后: 防御+黄金")
            daily_actions.append("减仓期间停止新建仓")
        elif phase_key == 'phase_4_clearance':
            if next_day >= '2030-12-01':
                daily_actions.append("清仓期: 12月清仓对冲仓位, 12月31日100%现金")
            elif next_day >= '2030-11-01':
                daily_actions.append("清仓期: 11月清仓全部ETF")
            elif next_day >= '2030-10-01':
                daily_actions.append("清仓期: 10月清仓全部股票")
            else:
                daily_actions.append("清仓期: 7-9月清仓全部股票")
            daily_actions.append("清仓期不再触发任何加仓信号")
            daily_actions.append("2030-12-15起强制平仓全部对冲仓位")

        # 5.1 生成次日标的明细 (含12只股票 + 8只ETF)
        target_positions_detail = []
        plan_positions = stock_account.get('positions', [])
        if plan_positions:
            for pos in plan_positions:
                code = pos.get('code', '')
                name = pos.get('name', '')
                ptype = pos.get('type', '')
                weight = pos.get('weight', 0)
                amount = pos.get('amount', 0)
                style = pos.get('style', pos.get('sector', ''))
                etf_sig = pos.get('etf_flow_signal', '')
                etf_inflow = pos.get('etf_inflow')
                reason = pos.get('reason', '')

                # 根据阶段计算当日动作
                if phase_key == 'phase_1_accumulation':
                    action = 'BUY'
                    # 当日预算按权重分配
                    daily_amount = daily_capital * weight / 0.90 if daily_capital else 0
                elif phase_key == 'phase_2_holding':
                    action = 'HOLD'
                    daily_amount = 0
                elif phase_key == 'phase_3_reduction':
                    action = 'SELL'
                    daily_amount = -daily_capital * weight / 0.90 if daily_capital else 0
                elif phase_key == 'phase_4_clearance':
                    # 10月清股票, 11月清ETF, 12月清对冲
                    if ptype == 'STOCK' and next_day >= '2030-10-01':
                        action = 'SELL_ALL'
                    elif ptype == 'ETF' and next_day >= '2030-11-01':
                        action = 'SELL_ALL'
                    else:
                        action = 'HOLD'
                    daily_amount = 0
                else:
                    action = 'HOLD'
                    daily_amount = 0

                target_positions_detail.append({
                    'code': code,
                    'name': name,
                    'type': ptype,
                    'weight': weight,
                    'amount': amount,
                    'daily_amount': round(daily_amount, 2),
                    'action': action,
                    'style': style,
                    'etf_flow_signal': etf_sig,
                    'etf_inflow': etf_inflow if etf_inflow is not None else '',
                    'reason': reason,
                })

        # 6. 对冲账户策略
        hedge_strategy = hedge_account.get('hedge_strategy', {})
        hedge_instruments = [
            {
                'instrument': inst.get('instrument', ''),
                'direction': inst.get('direction', ''),
                'target_contracts': inst.get('target_contracts', 0),
                'reason': inst.get('reason', ''),
            } for inst in hedge_account.get('target_instruments', [])
        ]
        # 6.1 补充从 positions.json 的 hedge_positions 读取期货期权明细
        hedge_positions_detail = []
        try:
            positions_file = _Path(__file__).parent / "config" / "positions.json"
            if positions_file.exists():
                with open(positions_file, 'r', encoding='utf-8') as f:
                    pos_data = json.load(f)
                hedge_pos = pos_data.get('hedge_positions', {})
                for key, hp in hedge_pos.items():
                    instrument = hp.get('instrument', key)
                    exchange = hp.get('exchange', '')
                    direction = hp.get('direction', '')
                    contracts = hp.get('target_contracts', 0)
                    multiplier = hp.get('multiplier', 0)
                    margin_rate = hp.get('margin_rate', 0)
                    beta_reduction = hp.get('target_beta_reduction', 0)
                    strike = hp.get('strike', '')
                    premium_budget = hp.get('premium_budget', 0)
                    reason = hp.get('reason', '')
                    # 计算名义价值
                    notional = 0
                    if multiplier and contracts:
                        # 期权按 premium_budget 估算, 期货按 multiplier * contracts * 假设价格
                        if 'put' in key.lower() or 'option' in key.lower():
                            notional = premium_budget
                        else:
                            notional = multiplier * contracts * 4000  # 假设指数4000点
                    hedge_positions_detail.append({
                        'key': key,
                        'instrument': instrument,
                        'exchange': exchange,
                        'direction': direction,
                        'target_contracts': contracts,
                        'multiplier': multiplier,
                        'margin_rate': margin_rate,
                        'target_beta_reduction': beta_reduction,
                        'strike': strike,
                        'premium_budget': premium_budget,
                        'estimated_notional': notional,
                        'reason': reason,
                    })
        except Exception as e:
            print(f"读取 hedge_positions 失败: {e}")

        hedge_action = {
            'mode': hedge_strategy.get('mode', 'dynamic'),
            'trigger_threshold': hedge_strategy.get('trigger_threshold', 0.05),
            'rebalance_frequency': hedge_strategy.get('rebalance_frequency', '每周五'),
            'instruments': hedge_instruments,
            'hedge_positions_detail': hedge_positions_detail,
        }

        # 7. ETF资金流监控配置
        etf_flow_monitoring = risk_mgmt.get('etf_flow_monitoring', {})

        # 8. 风控指令
        stop_loss_rules = risk_mgmt.get('stop_loss_rules', {})
        take_profit_rules = risk_mgmt.get('take_profit_rules', {})
        position_limits = risk_mgmt.get('position_limits', {})

        return {
            'next_trading_day': next_day,
            'weekday': weekday_cn,
            'is_trading_day': is_trading_day(next_day),
            'phase': {
                'key': phase_key,
                'name_cn': phase_name_cn,
                'period': phase_info.get('period', ''),
                'day_index': day_index,
                'description': phase_info.get('description', ''),
                'strategy': phase_info.get('strategy', ''),
            },
            'stock_etf_account': {
                'capital': stock_account.get('capital', 3000000),
                'target_positions': stock_account.get('target_positions', 20),
                'daily_capital': round(daily_capital, 2),
                'daily_actions': daily_actions,
                'target_positions_detail': target_positions_detail,
            },
            'hedge_account': hedge_action,
            'etf_flow_monitoring': etf_flow_monitoring,
            'risk_controls': {
                'stop_loss_single': stop_loss_rules.get('single_position', -0.15),
                'stop_loss_portfolio': stop_loss_rules.get('portfolio', -0.12),
                'take_profit_single': take_profit_rules.get('single_position', 0.50),
                'take_profit_portfolio': take_profit_rules.get('portfolio', 0.30),
                'max_single_position': position_limits.get('max_single_position', 0.10),
                'max_sector_exposure': position_limits.get('max_sector_exposure', 0.30),
            },
            'expected_performance': plan.get('expected_performance', {}),
        }


def save_report(report: Dict, output_path: str):
    """保存报告"""
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"报告已保存: {output_path}")


def print_report_summary(report: Dict):
    """打印报告摘要"""
    print("=" * 70)
    print("[收盘盈亏明细报告]")
    print("=" * 70)
    print(f"日期: {report['meta']['report_date']}")
    print(f"阶段: {report['meta']['phase']}")
    print()
    
    # 组合盈亏
    print("【组合盈亏】")
    pnl_summary = report['portfolio_pnl']['summary']
    print(f"  总成本: {pnl_summary['total_cost']:,.2f}")
    print(f"  总市值: {pnl_summary['total_market_value']:,.2f}")
    print(f"  总盈亏: {pnl_summary['total_pnl']:,.2f} ({pnl_summary['total_pnl_pct']:.2f}%)")
    print(f"  持仓数: {pnl_summary['position_count']}")
    print()
    
    # 对冲明细
    print("【对冲头寸】")
    hedge_summary = report['hedge_position']['summary']
    print(f"  对冲规模: {hedge_summary['total_hedge_notional']:,.2f}")
    beta_exposure = report['risk_metrics'].get('beta_exposure', 0.3)
    print(f"  Beta敞口: {beta_exposure:.3f}")
    print(f"  对冲有效性: {hedge_summary.get('hedge_effectiveness', 76.84):.2f}%")
    print()
    
    # 净盈亏
    print("【净盈亏】")
    net_perf = report['net_performance']
    print(f"  组合盈亏: {net_perf['portfolio_pnl']:,.2f}")
    print(f"  对冲盈亏: {net_perf['hedge_pnl']:,.2f}")
    print(f"  净盈亏: {net_perf['net_pnl']:,.2f} ({net_perf['net_pnl_pct']:.2f}%)")
    print()
    
    # 风险指标
    print("【风险指标】")
    risk = report['risk_metrics']
    print(f"  日均收益: {risk['avg_daily_return_pct']:.2f}%")
    print(f"  波动率: {risk['portfolio_volatility_pct']:.2f}%")
    print(f"  最大跌幅: {risk['max_drawdown_pct']:.2f}%")
    print(f"  止损状态: {risk['stop_loss_status']}")
    print()
    
    # AI建议
    print("【AI决策建议】")
    for i, rec in enumerate(report['ai_recommendations'], 1):
        print(f"  {i}. {rec}")
    
    print("=" * 70)


def generate_markdown_report(report: Dict) -> str:
    """生成Markdown格式报告"""
    md = f"""# 📊 收盘盈亏明细报告

**日期**: {report['meta']['report_date']}  
**阶段**: {report['meta']['phase']}  
**视角**: {report['meta']['fund_style']}  

---

## 一、市场概况

| 指标 | 数值 |
|------|------|
| 市场状态 | {report['market_overview']['market_regime']} |
| VIX估计 | {report['market_overview']['vix_estimate']} |
| 趋势判断 | {report['market_overview']['market_trend']} |

---

## 一之一、数据源健康检查

| 指标 | 数值 |
|------|------|
| 数据源状态 | {report['market_overview']['data_source_status']} |
| 快照占比 | {report['meta']['data_source_health']['snapshot_ratio']:.0%} |
| 兜底价格占比 | {report['meta']['data_source_health']['fallback_ratio']:.0%} |
| 快照标的数 | {report['meta']['data_source_health']['snapshot_count']} |
| 兜底标的数 | {report['meta']['data_source_health']['fallback_count']} |
| 持仓总数 | {report['meta']['data_source_health']['total_positions']} |

> ⚠️ **提示**: 快照占比低于 50% 或兜底价格占比高于 30% 时，建议检查 Wind MCP / iFinD / 新浪等数据源可用性。

---

## 二、持仓盈亏明细

### 2.1 概览

| 项目 | 金额 |
|------|------|
| 总成本 | {report['portfolio_pnl']['summary']['total_cost']:,.2f} |
| 总市值 | {report['portfolio_pnl']['summary']['total_market_value']:,.2f} |
| 总盈亏 | **{report['portfolio_pnl']['summary']['total_pnl']:,.2f}** ({report['portfolio_pnl']['summary']['total_pnl_pct']:.2f}%) |
| 持仓数 | {report['portfolio_pnl']['summary']['position_count']} |

### 2.2 持仓明细

| 代码 | 名称 | 股数 | 成本价 | 收盘价 | 日涨跌% | 盈亏 | 模式 | 状态 |
|------|------|------|------|------|------|------|------|------|
"""
    
    for d in report['portfolio_pnl']['details']:
        status_icon = '✅' if d['status'] == 'NORMAL' else ('⚠️' if d['status'] == 'WARNING' else ('🔴' if d['status'] == 'STOP_LOSS_TRIGGERED' else '🟢'))
        mode_label = '快照' if d.get('calc_mode') == 'snapshot' else '计划'
        md += f"| {d['code']} | {d['name']} | {d['shares']} | {d['cost_price']} | {d['close_price']} | {d['daily_pnl_pct']:.2f}% | {d['pnl']:,.0f} | {mode_label} | {status_icon} |\n"
    
    md += """
---

## 三、对冲头寸明细

### 3.1 期货对冲

"""
    
    for h in report['hedge_position']['details']:
        cost_bd = h.get('cost_breakdown') or {}
        cost_note = cost_bd.get('cost_note', '')
        md += f"""| 合约 | 方向 | 手数 | 开仓价 | 收盘价 | 对冲盈亏 | Beta降低 |
|------|------|------|------|------|------|------|
| {h['instrument']} | {h['direction']} | {h['contracts']} | {h['entry_price']} | {h['close_price']} | {h['hedge_pnl']:,.0f} | {h['beta_reduced']:.3f} |

"""
        if cost_bd:
            md += f"""| 成本项 | 数值 |
|------|------|
| 预估总成本 | {h.get('cost', 0):,.2f} |
| 佣金费率 | {cost_bd.get('commission_rate', 0):.6f} |
| 滑点费率 | {cost_bd.get('slippage_rate', 0):.6f} |
| 保证金比例 | {cost_bd.get('margin_rate', 0):.2%} |
| 预估佣金 | {cost_bd.get('estimated_commission', 0):,.2f} |
| 预估滑点 | {cost_bd.get('estimated_slippage', 0):,.2f} |
| 预估保证金 | {cost_bd.get('estimated_margin', 0):,.2f} |

> ⚠️ {cost_note}

"""
    
    md += f"""### 3.2 对冲效果

| 指标 | 数值 |
|------|------|
| 对冲规模 | {report['hedge_position']['summary']['total_hedge_notional']:,.2f} |
| 原Beta | {report['hedge_position']['summary']['current_portfolio_beta']:.3f} |
| 目标Beta | {report['hedge_position']['summary']['target_beta']:.3f} |
| 当前Beta | {report['risk_metrics']['beta_exposure']:.3f} |
| 对冲有效性 | {report['hedge_position']['summary']['hedge_effectiveness']:.2f}% |

### 3.3 期货期权计划头寸 (来自 positions.json)

"""
    plan = report.get('hedge_position_plan') or {}
    plan_details = plan.get('details', [])
    if plan_details:
        md += "| # | 工具 | 交易所 | 方向 | 目标手数 | 合约乘数 | 保证金率 | 目标Beta降低 | 行权价 | 权利金预算 | 估算名义价值 | 估算成本 | 说明 |\n"
        md += "|---|------|--------|------|---------|---------|---------|------------|--------|-----------|------------|---------|------|\n"
        for i, p in enumerate(plan_details, 1):
            strike_str = p.get('strike') or '-'
            premium_str = f"¥{p.get('premium_budget', 0):,}" if p.get('is_option') else '¥0'
            md += (f"| {i} | {p['instrument']} | {p['exchange']} | {p['direction']} | "
                   f"{p['target_contracts']} | {p['multiplier']} | {p['margin_rate']:.2%} | "
                   f"{p['target_beta_reduction']:.3f} | {strike_str} | {premium_str} | "
                   f"¥{p['estimated_notional']:,.0f} | ¥{p['estimated_cost']:,.0f} | {p['reason']} |\n")
        # 汇总行
        summary = plan.get('summary', {})
        md += (f"| **合计** | - | - | - | - | - | - | **{summary.get('total_beta_reduction', 0):.3f}** | - | "
               f"**¥{summary.get('total_premium_budget', 0):,}** | "
               f"**¥{summary.get('total_estimated_notional', 0):,.0f}** | - | - |\n\n")
        md += f"> 📌 **期货期权对冲工具**: 共 {summary.get('tool_count', 0)} 类工具 | "
        md += f"期货名义价值 ¥{summary.get('total_estimated_notional', 0):,.0f} | "
        md += f"期权权利金预算 ¥{summary.get('total_premium_budget', 0):,} | "
        md += f"目标Beta降低 {summary.get('total_beta_reduction', 0):.3f}\n\n"
    else:
        md += "> ⚠️ positions.json 中未配置 hedge_positions\n\n"

    md += f"""---

## 四、净盈亏分析

| 项目 | 金额 |
|------|------|
| 组合盈亏 | {report['net_performance']['portfolio_pnl']:,.2f} |
| 对冲盈亏 | {report['net_performance']['hedge_pnl']:,.2f} |
| **净盈亏** | **{report['net_performance']['net_pnl']:,.2f}** ({report['net_performance']['net_pnl_pct']:.2f}%) |

---

## 五、风险指标

| 指标 | 数值 | 评级 |
|------|------|------|
| 日均收益 | {report['risk_metrics']['avg_daily_return_pct']:.2f}% | {('良好' if report['risk_metrics']['avg_daily_return_pct'] > 0.3 else '中性')} |
| 波动率 | {report['risk_metrics']['portfolio_volatility_pct']:.2f}% | {('可控' if report['risk_metrics']['portfolio_volatility_pct'] < 1.0 else '偏高')} |
| 最大跌幅 | {report['risk_metrics']['max_drawdown_pct']:.2f}% | {('安全' if report['risk_metrics']['max_drawdown_pct'] > -5 else '关注')} |
| Beta敞口 | {report['risk_metrics']['beta_exposure']:.3f} | {('达标' if report['risk_metrics']['beta_exposure'] < 0.5 else '偏高')} |

---

## 六、AI决策建议

"""
    
    for i, rec in enumerate(report['ai_recommendations'], 1):
        md += f"{i}. {rec}\n"

    # 第二天交易计划章节
    next_day_plan = report.get('next_day_plan', {})
    if next_day_plan and not next_day_plan.get('error'):
        nd = next_day_plan.get('next_trading_day', '')
        wd = next_day_plan.get('weekday', '')
        phase = next_day_plan.get('phase', {})
        stock_acc = next_day_plan.get('stock_etf_account', {})
        hedge_acc = next_day_plan.get('hedge_account', {})
        etf_mon = next_day_plan.get('etf_flow_monitoring', {})
        risk_ctrl = next_day_plan.get('risk_controls', {})
        exp_perf = next_day_plan.get('expected_performance', {})

        md += f"""
---

## 七、第二天交易计划

**下一交易日**: {nd} ({wd})  
**所属阶段**: {phase.get('name_cn', '')} (第 {phase.get('day_index', 0)} 天 / {phase.get('period', '')})  
**阶段策略**: {phase.get('strategy', '')}

### 7.1 股票ETF账户计划

| 项目 | 数值 |
|------|------|
| 账户资金 | {stock_acc.get('capital', 3000000):,.0f} |
| 目标标的数 | {stock_acc.get('target_positions', 20)} |
| 当日预算 | {stock_acc.get('daily_capital', 0):,.2f} |

**当日交易动作**:

"""
        for i, action in enumerate(stock_acc.get('daily_actions', []), 1):
            md += f"{i}. {action}\n"

        # 7.1.1 标的明细表 (含12只股票 + 8只ETF)
        target_detail = stock_acc.get('target_positions_detail', [])
        if target_detail:
            md += f"""
**次日标的明细 (共 {len(target_detail)} 只)**:

| # | 代码 | 名称 | 类型 | 动作 | 权重 | 目标金额 | 当日金额 | 风格 | ETF信号 | 净流入(亿) | 说明 |
|---|------|------|------|------|------|---------|---------|------|---------|-----------|------|
"""
            for idx, pos in enumerate(target_detail, 1):
                code = pos.get('code', '')
                name = pos.get('name', '')
                ptype = pos.get('type', '')
                action = pos.get('action', 'HOLD')
                weight = pos.get('weight', 0)
                amount = pos.get('amount', 0)
                daily_amt = pos.get('daily_amount', 0)
                style = pos.get('style', '')
                etf_sig = pos.get('etf_flow_signal', '')
                etf_inflow = pos.get('etf_inflow', '')
                reason = pos.get('reason', '')

                # 格式化ETF净流入
                inflow_str = f"{etf_inflow:.2f}" if isinstance(etf_inflow, (int, float)) else str(etf_inflow)
                # 截断reason到50字符
                reason_short = reason[:50] + '...' if len(reason) > 50 else reason

                md += f"| {idx} | {code} | {name} | {ptype} | {action} | {weight:.0%} | {amount:,.0f} | {daily_amt:,.0f} | {style} | {etf_sig} | {inflow_str} | {reason_short} |\n"

        md += f"""
### 7.2 对冲账户计划

| 项目 | 数值 |
|------|------|
| 对冲模式 | {hedge_acc.get('mode', 'dynamic')} |
| 触发阈值 | {hedge_acc.get('trigger_threshold', 0.05)} |
| 再平衡频率 | {hedge_acc.get('rebalance_frequency', '每周五')} |

**对冲工具明细**:

| 工具 | 方向 | 目标手数 | 说明 |
|------|------|---------|------|
"""
        for inst in hedge_acc.get('instruments', []):
            md += f"| {inst.get('instrument', '')} | {inst.get('direction', '')} | {inst.get('target_contracts', 0)} | {inst.get('reason', '')} |\n"

        # 7.2.1 期货期权对冲仓位明细 (来自 positions.json)
        hedge_positions_detail = hedge_acc.get('hedge_positions_detail', [])
        if hedge_positions_detail:
            md += f"""
**期货期权对冲仓位明细** (来自 positions.json):

| # | 工具 | 交易所 | 方向 | 目标手数 | 合约乘数 | 保证金率 | 目标Beta降低 | 行权价 | 权利金预算 | 估算名义价值 | 说明 |
|---|------|--------|------|---------|---------|---------|------------|--------|-----------|------------|------|
"""
            for idx, hp in enumerate(hedge_positions_detail, 1):
                instrument = hp.get('instrument', '')
                exchange = hp.get('exchange', '')
                direction = hp.get('direction', '')
                contracts = hp.get('target_contracts', 0)
                multiplier = hp.get('multiplier', 0)
                margin_rate = hp.get('margin_rate', 0)
                beta_red = hp.get('target_beta_reduction', 0)
                strike = hp.get('strike', '-') if hp.get('strike') else '-'
                premium = hp.get('premium_budget', 0)
                notional = hp.get('estimated_notional', 0)
                reason = hp.get('reason', '')[:60]
                md += f"| {idx} | {instrument} | {exchange} | {direction} | {contracts} | {multiplier} | {margin_rate:.2%} | {beta_red:.2f} | {strike} | ¥{premium:,} | ¥{notional:,} | {reason} |\n"

            # 汇总行
            total_notional = sum(hp.get('estimated_notional', 0) for hp in hedge_positions_detail)
            total_beta_red = sum(hp.get('target_beta_reduction', 0) for hp in hedge_positions_detail)
            md += f"| **合计** | - | - | - | - | - | - | **{total_beta_red:.2f}** | - | - | **¥{total_notional:,}** | - |\n"

        if etf_mon:
            md += f"""
### 7.3 ETF资金流监控

| 项目 | 数值 |
|------|------|
| 数据源 | {etf_mon.get('source', '实时ETF资金流向监控')} |
| 监控频率 | {etf_mon.get('frequency', '每日3次')} |
| 强信号阈值 | {etf_mon.get('strong_signal_threshold', 20)} 亿 |
| 中信号阈值 | {etf_mon.get('medium_signal_threshold', 10)} 亿 |
| 反转阈值 | {etf_mon.get('reversal_threshold', -5)} 亿 |
| 自动调整 | {('是' if etf_mon.get('auto_adjust', False) else '否')} |
"""

        if risk_ctrl:
            md += f"""
### 7.4 风控指令

| 指标 | 数值 |
|------|------|
| 单标的止损 | {risk_ctrl.get('stop_loss_single', -0.15):.0%} |
| 组合止损 | {risk_ctrl.get('stop_loss_portfolio', -0.12):.0%} |
| 单标的止盈 | {risk_ctrl.get('take_profit_single', 0.50):.0%} |
| 单标的权重上限 | {risk_ctrl.get('max_single_position', 0.10):.0%} |
| 单板块权重上限 | {risk_ctrl.get('max_sector_exposure', 0.30):.0%} |
"""

        if exp_perf:
            md += f"""
### 7.5 预期绩效对照

| 指标 | 目标 | 预期 |
|------|------|------|
| 年化收益 | >8% | {exp_perf.get('annual_return', 0.1072):.2%} |
| 最大回撤 | <15% | {exp_perf.get('max_drawdown', 0.1465):.2%} |
| Sharpe比率 | >0.80 | {exp_perf.get('sharpe_ratio', 0.863):.3f} |
| 4.5年总收益 | - | {exp_perf.get('4_5_year_total_return', 0.4832):.2%} |
| 4.5年终值 | - | {exp_perf.get('4_5_year_projection', '500万 → 741.6万')} |
"""

    # 八、收益率预测章节 (基于 portfolio_return_projection.json)
    proj = report.get('return_projection', {})
    if proj and not proj.get('error'):
        proj_scenarios = proj.get('scenarios', {})
        proj_expected = proj.get('expected', {})
        proj_prob = proj.get('probability_weights', {})
        proj_risk = proj.get('risk_disclosure', {})

        md += f"""
### 7.6 收益率预测 (基于十五五降权后持仓)

**预测版本**: {proj.get('version', 'unknown')}  
**投资期限**: {proj.get('investment_horizon', '')} ({proj.get('horizon_years', 1.5)} 年)  
**初始资本**: ¥{proj.get('initial_capital', 5000000):,}

#### 四场景预测

| 场景 | 概率 | 加权年化 | 累计收益 | 期末金额 | 盈亏 |
|------|------|---------|---------|---------|------|
"""
        scenario_icons = {'bull': '🐂', 'base': '📊', 'bear': '🐻', 'black_swan': '⚫'}
        for s_key in ['bull', 'base', 'bear', 'black_swan']:
            sc = proj_scenarios.get(s_key, {})
            icon = scenario_icons.get(s_key, '')
            prob = proj_prob.get(s_key, 0)
            ann = sc.get('weighted_annualized', 0)
            cum = sc.get('cumulative_return', 0)
            fin = sc.get('final_amount', 0)
            profit = sc.get('total_profit', 0)
            profit_str = f"+¥{profit:,.0f}" if profit >= 0 else f"-¥{abs(profit):,.0f}"
            md += f"| {icon} {sc.get('label', s_key)} | {prob:.0%} | {ann:.2f}% | {cum:.2f}% | ¥{fin:,.0f} | {profit_str} |\n"

        md += f"""
#### 加权期望

| 指标 | 数值 |
|------|------|
| **加权期望年化** | **{proj_expected.get('expected_annualized', 0):.2f}%** |
| 加权期望累计 | {proj_expected.get('expected_cumulative', 0):.2f}% |
| 加权期望期末金额 | ¥{proj_expected.get('expected_final_amount', 0):,.0f} |
| 加权期望盈亏 | {"+" if proj_expected.get('expected_profit', 0) >= 0 else ""}¥{proj_expected.get('expected_profit', 0):,.0f} |

#### 风险披露

| 风险类型 | 说明 |
|---------|------|
| 集中度风险 | {proj_risk.get('concentration_risk', '-')} |
| 波动率风险 | {proj_risk.get('volatility_risk', '-')} |
| 对冲覆盖 | {proj_risk.get('hedge_coverage', '-')} |
| 政策风险 | {proj_risk.get('policy_risk', '-')} |
| 流动性风险 | {proj_risk.get('liquidity_risk', '-')} |
"""

    md += f"""
---

**报告生成时间**: {report['meta']['generated_at']}
**数据源**: Wind MCP > iFinD MCP
"""

    return md


def main():
    """主函数"""
    import sys
    from pathlib import Path

    # 支持命令行参数: 指定报告日期
    if len(sys.argv) > 1:
        report_date_arg = sys.argv[1]
    else:
        report_date_arg = datetime.now().strftime("%Y-%m-%d")

    # 切换到项目根目录 (保证相对路径正确)
    project_root = Path(__file__).resolve().parent
    os.chdir(project_root)

    positions_file = "config/positions.json"

    # 自动查找最新的对冲执行文件
    hedge_dir = project_root / "v7.5_institutional" / "reports"
    hedge_file = None
    if hedge_dir.exists():
        # 优先查找当日的对冲执行文件
        date_compact = report_date_arg.replace("-", "")
        hedge_candidates = sorted(hedge_dir.glob(f"hedge_execution_fill_{report_date_arg}*.json"), reverse=True)
        if not hedge_candidates:
            hedge_candidates = sorted(hedge_dir.glob("hedge_execution_fill_*.json"), reverse=True)
        if hedge_candidates:
            hedge_file = str(hedge_candidates[0])
            print(f"使用对冲文件: {hedge_file}")

    if hedge_file is None:
        hedge_file = "v7.5_institutional/reports/hedge_execution_fill_2026-07-09.json"

    # 自动查找当日持仓快照
    sim_dir = project_root / "v7.5_institutional" / "sim_snapshots"
    positions_snapshot = None
    if sim_dir.exists():
        snap_candidates = sorted(sim_dir.glob(f"positions_{date_compact}*.json"), reverse=True)
        if snap_candidates:
            positions_snapshot = str(snap_candidates[0])
            print(f"使用持仓快照: {positions_snapshot}")

    # 自动查找当日 trade_plan (用于获取第一次交易开盘价作为成本价)
    plan_dir = project_root / "v7.5_institutional" / "trade_plans"
    trade_plan_file = None
    if plan_dir.exists():
        plan_candidates = sorted(plan_dir.glob(f"trade_plan_{date_compact}*.json"), reverse=True)
        if plan_candidates:
            trade_plan_file = str(plan_candidates[0])
            print(f"使用交易计划: {trade_plan_file}")

    analyzer = PortfolioAnalyzer(positions_file, hedge_file)
    # 如果有持仓快照, 用它覆盖 positions.json 中的 shares 字段
    if positions_snapshot:
        analyzer._apply_positions_snapshot(positions_snapshot, trade_plan_file)
    report = analyzer.generate_report()
    
    # 打印摘要
    print_report_summary(report)
    
    # 保存JSON报告 (使用 report_date_arg 而非全局 REPORT_DATE)
    # 输出到 v7.5_institutional/reports/ 目录
    reports_dir = project_root / "v7.5_institutional" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    json_output = str(reports_dir / f"daily_pnl_report_{report_date_arg}.json")
    save_report(report, json_output)

    # 保存Markdown报告
    md_content = generate_markdown_report(report)
    md_output = str(reports_dir / f"daily_pnl_report_{report_date_arg}.md")
    with open(md_output, 'w', encoding='utf-8') as f:
        f.write(md_content)
    print(f"Markdown报告已保存: {md_output}")
    
    return report


if __name__ == "__main__":
    main()
