"""
东方财富数据获取模块 (EastMoney Data Fetcher)
==============================================

从东方财富网获取A股实时行情、K线数据、资金流向等信息

Author: Agnes-2.0 Flash Team
Date: 2026-07-23
"""

import logging
import requests
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
import pandas as pd

logger = logging.getLogger(__name__)


class EastMoneyDataFetcher:
    """东方财富数据获取器"""
    
    def __init__(self):
        self.base_url = "https://push2.eastmoney.com/api"
        self.quote_url = "https://quote.eastmoney.com/gridtable"
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8'
        })
        
        logger.info("东方财富数据获取器初始化完成")
    
    def get_realtime_quotes(self, symbols: List[str]) -> Dict[str, Any]:
        """
        获取实时行情
        
        Args:
            symbols: 股票代码列表，如 ['600519', '000001']
            
        Returns:
            字典格式的股票实时行情数据
        """
        try:
            results = {}
            
            for symbol in symbols:
                # 确定市场前缀
                if symbol.startswith('6'):
                    secid = f"1.{symbol}"
                elif symbol.startswith(('0', '3')):
                    secid = f"0.{symbol}"
                else:
                    secid = f"1.{symbol}"
                
                # 调用东方财富API
                url = f"{self.base_url}/unified.wss/secapi/qq/stock?secids={secid}&fields=f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13,f14,f15,f16,f17,f18"
                
                try:
                    response = self.session.get(url, timeout=5)
                    
                    if response.status_code == 200:
                        data = response.json()
                        
                        # 解析数据
                        if data.get('data'):
                            stock_data = data['data'].get(secid, {})
                            
                            results[symbol] = {
                                'code': symbol,
                                'name': stock_data.get('f14', ''),
                                'price': stock_data.get('f2'),
                                'change': stock_data.get('f3'),
                                'change_pct': stock_data.get('f3'),
                                'volume': stock_data.get('f5'),
                                'turnover': stock_data.get('f6'),
                                'high': stock_data.get('f15'),
                                'low': stock_data.get('f16'),
                                'open': stock_data.get('f18'),
                                'date': stock_data.get('f23', ''),
                                'time': stock_data.get('f22', '')
                            }
                        else:
                            results[symbol] = {'error': '无数据'}
                            
                except Exception as e:
                    logger.warning(f"获取 {symbol} 数据失败: {e}")
                    results[symbol] = {'error': str(e)}
            
            return results
            
        except Exception as e:
            logger.error(f"获取实时行情失败: {e}")
            return {'error': str(e)}
    
    def get_historical_klines(
        self,
        symbol: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        period: str = 'daily'
    ) -> pd.DataFrame:
        """
        获取历史K线数据
        
        Args:
            symbol: 股票代码，如 '600519'
            start_date: 开始日期，格式 'YYYY-MM-DD'
            end_date: 结束日期，格式 'YYYY-MM-DD'
            period: K线周期 ('daily', 'weekly', 'monthly')
            
        Returns:
            DataFrame格式的K线数据
        """
        try:
            if end_date is None:
                end_date = datetime.now().strftime('%Y-%m-%d')
            if start_date is None:
                start_date = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
            
            # 确定市场前缀
            if symbol.startswith('6'):
                secid = f"1.{symbol}"
            elif symbol.startswith(('0', '3')):
                secid = f"0.{symbol}"
            else:
                secid = f"1.{symbol}"
            
            # 调用东方财富API
            fields = "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13,f14,f15,f16,f17,f18"
            url = f"{self.base_url}/kline/wss/secapi/qq/kline?secids={secid}&field1={secid}&field2=0&field3={period}&field4={start_date}&field5={end_date}&fields={fields}"
            
            response = self.session.get(url, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                
                # 解析K线数据
                klines = []
                if data.get('data'):
                    stock_data = data['data'].get(secid, {})
                    
                    for _key, value in stock_data.items():
                        if isinstance(value, list):
                            for item in value:
                                if isinstance(item, dict):
                                    klines.append(item)
                
                if klines:
                    df = pd.DataFrame(klines)
                    return df
                
                return pd.DataFrame()
                
            else:
                logger.warning(f"获取K线数据失败，状态码: {response.status_code}")
                return pd.DataFrame()
                
        except Exception as e:
            logger.error(f"获取K线数据异常: {e}")
            return pd.DataFrame()
    
    def get_stock_info(self, symbol: str) -> Dict[str, Any]:
        """
        获取股票基本信息
        
        Args:
            symbol: 股票代码
            
        Returns:
            股票基本信息字典
        """
        try:
            if symbol.startswith('6'):
                secid = f"1.{symbol}"
            elif symbol.startswith(('0', '3')):
                secid = f"0.{symbol}"
            else:
                secid = f"1.{symbol}"
            
            fields = "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13,f14,f15,f16,f17,f18,f100,f101,f102,f103,f104,f105"
            url = f"{self.base_url}/unified.wss/secapi/qq/stock?secids={secid}&fields={fields}"
            
            response = self.session.get(url, timeout=5)
            
            if response.status_code == 200:
                data = response.json()
                
                if data.get('data'):
                    stock_data = data['data'].get(secid, {})
                    
                    return {
                        'code': symbol,
                        'name': stock_data.get('f14', ''),
                        'current_price': stock_data.get('f2'),
                        'market_cap': stock_data.get('f100'),
                        'total_shares': stock_data.get('f101'),
                        'float_shares': stock_data.get('f102'),
                        'pe_ratio': stock_data.get('f103'),
                        'pb_ratio': stock_data.get('f104'),
                        'eps': stock_data.get('f105'),
                    }
            
            return {'error': '无法获取股票信息'}
            
        except Exception as e:
            logger.error(f"获取股票信息失败: {e}")
            return {'error': str(e)}
    
    def get_flow_data(self, symbol: str) -> Dict[str, Any]:
        """
        获取资金流向数据
        
        Args:
            symbol: 股票代码
            
        Returns:
            资金流向数据
        """
        try:
            if symbol.startswith('6'):
                secid = f"1.{symbol}"
            elif symbol.startswith(('0', '3')):
                secid = f"0.{symbol}"
            else:
                secid = f"1.{symbol}"
            
            # 获取主力资金流向
            url = f"{self.base_url}/wss/secapi/qq/stock?secids={secid}&fields=f59,f60,f61,f62,f63,f64,f65,f66"
            
            response = self.session.get(url, timeout=5)
            
            if response.status_code == 200:
                data = response.json()
                
                if data.get('data'):
                    stock_data = data['data'].get(secid, {})
                    
                    return {
                        'symbol': symbol,
                        'main_inflow': stock_data.get('f59'),
                        'small_inflow': stock_data.get('f60'),
                        'medium_inflow': stock_data.get('f61'),
                        'large_inflow': stock_data.get('f62'),
                        'super_large_inflow': stock_data.get('f63'),
                    }
            
            return {'error': '无法获取资金流向数据'}
            
        except Exception as e:
            logger.error(f"获取资金流向失败: {e}")
            return {'error': str(e)}


if __name__ == '__main__':
    # 测试代码
    fetcher = EastMoneyDataFetcher()
    
    # 测试实时行情
    test_symbols = ['600519', '000001', '300750']
    quotes = fetcher.get_realtime_quotes(test_symbols)
    
    logger.info("\n东方财富实时行情测试:")
    logger.info("=" * 80)
    for symbol, quote in quotes.items():
        if 'error' not in quote:
            logger.info(f"\n{symbol} - {quote.get('name', 'N/A')}")
            logger.info(f"  价格: {quote.get('price')}")
            logger.info(f"  涨跌幅: {quote.get('change_pct')}%")
            logger.info(f"  成交量: {quote.get('volume')}")
        else:
            logger.info(f"\n{symbol}: {quote['error']}")
