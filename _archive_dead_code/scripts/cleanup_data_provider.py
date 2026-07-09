# -*- coding: utf-8 -*-
"""一次性清理 data_provider.py 中除 Wind MCP 外的旧数据回退分支。"""
from pathlib import Path

file_path = Path(r'e:\各种PY程序\28-终极量化交易系统7.1\utils\data_provider.py')
text = file_path.read_text(encoding='utf-8')

old = '''    def _try_ifind_mcp_realtime(self, symbol: str) -> Optional[Dict]:
        if not self._ifind_mcp_client:
            return None
        try:
            if self._is_fund(symbol):
                res = self._ifind_mcp_client['call']('fund', 'fund_highfreq_quotes', {
                    'symbols': symbol,
                    'indicators': '最新价,涨跌幅,成交额,成交量',
                    'data_mode': 'real_time',
                    'interval': 1,
                })
                if not res.get('ok') or not res.get('data'):
                    self.source_health['ifind_mcp']['last_error'] = f"fund_quote_failed: {res.get('error')}"
                    logger.warning(f"iFinD fund_highfreq_quotes 失败: {symbol}")
                    return None
                data = res['data']
                content = (((data.get('result') or {}).get('content') or [{}])[0].get('text') or '')
                try:
                    parsed = json.loads(content)
                except Exception:
                    self.source_health['ifind_mcp']['last_error'] = 'fund_parse_failed'
                    logger.warning(f"iFinD fund 返回解析失败: {symbol}")
                    return None
                payload = parsed.get('data') or parsed
                if isinstance(payload, str):
                    payload = json.loads(payload)
                tables = payload.get('tables') or []
                if len(tables) < 2:
                    self.source_health['ifind_mcp']['last_error'] = 'fund_tables_too_short'
                    logger.warning(f"iFinD fund tables 过短: {symbol}")
                    return None
                row = tables[-1]
                if len(row) < 7:
                    self.source_health['ifind_mcp']['last_error'] = 'fund_row_too_short'
                    logger.warning(f"iFinD fund row 过短: {symbol}")
                    return None
                self.source_health['ifind_mcp']['ok'] = True
                return {
                    'timestamp': datetime.now().isoformat(),
                    'symbol': symbol,
                    'index_price': safe_float(row[3]),
                    'prev_close': safe_float(row[3]) / (1 + safe_float(row[4]) / 100) if safe_float(row[4]) is not None else None,
                    'open': safe_float(row[3]),
                    'high': safe_float(row[3]),
                    'low': safe_float(row[3]),
                    'volume': safe_float(row[6], default=0),
                    'source': 'ifind_mcp',
                }
            else:
                res = self._ifind_mcp_client['call']('stock', 'get_stock_summary', {
                    'query': f"{symbol} 最新行情",
                })
                if not res.get('ok') or not res.get('data'):
                    self.source_health['ifind_mcp']['last_error'] = f"stock_summary_failed: {res.get('error')}"
                    logger.warning(f"iFinD get_stock_summary 失败: {symbol}")
                    return None
                data = res['data']
                content = (((data.get('result') or {}).get('content') or [{}])[0].get('text') or '')
                try:
                    parsed = json.loads(content)
                except Exception:
                    self.source_health['ifind_mcp']['last_error'] = 'stock_parse_failed'
                    logger.warning(f"iFinD stock 返回解析失败: {symbol}")
                    return None
                payload = parsed.get('data') or parsed
                if isinstance(payload, str):
                    try:
                        payload = json.loads(payload)
                    except Exception:
                        payload = {}
                answer = payload.get('answer') or ''
                rows = _parse_markdown_table(answer)
                if not rows:
                    self.source_health['ifind_mcp']['last_error'] = 'stock_table_empty'
                    logger.warning(f"iFinD stock Markdown 表格为空: {symbol}")
                    return None
                row = rows[-1]
                self.source_health['ifind_mcp']['ok'] = True
                return {
                    'timestamp': datetime.now().isoformat(),
                    'symbol': symbol,
                    'index_price': safe_float(row.get('收盘价') or row.get('最新价') or row.get('close')),
                    'prev_close': safe_float(row.get('前收盘价') or row.get('prev_close')),
                    'open': safe_float(row.get('开盘价') or row.get('open')),
                    'high': safe_float(row.get('最高价') or row.get('high')),
                    'low': safe_float(row.get('最低价') or row.get('low')),
                    'volume': safe_float(row.get('成交量') or row.get('volume'), default=0),
                    'source': 'ifind_mcp',
                }
        except Exception as e:
            self.source_health['ifind_mcp']['ok'] = False
            self.source_health['ifind_mcp']['last_error'] = str(e)
            logger.error(f"iFinD MCP 获取实时数据失败: {e}")
            return None
    
    def _try_ifind_mcp_historical(self, symbol: str, period: str) -> Optional[pd.DataFrame]:
        if not self._ifind_mcp_client:
            return None
        try:
            # iFinD 历史数据接口较复杂，这里先返回 None，后续按需补充
            return None
        except Exception as e:
            logger.error(f"iFinD MCP 获取历史数据失败: {e}")
            return None
    
    def _try_akshare_futures_realtime(self, symbol: str) -> Optional[Dict]:
        """AKShare 期货/商品实时报价回退"""
        try:
            from utils.akshare_futures import fetch_futures_realtime, is_futures_like
        except Exception as e:
            self.source_health.setdefault('akshare_futures', {'ok': False, 'last_error': str(e)})
            logger.debug("akshare_futures 导入失败: %s", e)
            return None
        if not is_futures_like(symbol):
            return None
        try:
            data = fetch_futures_realtime(symbol)
        except Exception as e:
            self.source_health.setdefault('akshare_futures', {'ok': False, 'last_error': str(e)})
            logger.debug("AKShare 期货实时获取失败 %s: %s", symbol, e)
            return None
        if not data:
            self.source_health.setdefault('akshare_futures', {'ok': False, 'last_error': 'empty'})
            return None
        price = data.get('latest') or data.get('close') or data.get('price')
        try:
            price = float(price)
        except (TypeError, ValueError):
            return None
        if price <= 0:
            return None
        self.source_health.setdefault('akshare_futures', {'ok': False, 'last_error': None})
        self.source_health['akshare_futures']['ok'] = True
        return {
            'timestamp': datetime.now().isoformat(),
            'symbol': symbol,
            'index_price': price,
            'prev_close': safe_float(data.get('prev_close')),
            'open': safe_float(data.get('open')) or price,
            'high': safe_float(data.get('high')) or price,
            'low': safe_float(data.get('low')) or price,
            'volume': safe_float(data.get('volume'), default=0),
            'source': 'akshare_futures',
        }
    
    def _try_akshare_stock_historical(self, symbol: str, period: str) -> Optional[pd.DataFrame]:
        """AKShare 股票/ETF 历史日线回退"""
        if not HAS_AKSHARE or _ak is None:
            return None
        try:
            from utils.akshare_futures import is_futures_like
            if is_futures_like(symbol):
                return None
        except Exception:
            pass
        period_map = {
            '1d': 1, '1w': 5, '1m': 20, '3m': 60, '6m': 120, '1y': 252, '2y': 504, '3y': 756, '5y': 1260,
        }.get(period, 252)
        try:
            df = _ak.stock_zh_a_hist(symbol=symbol, period='daily', adjust='qfq')
        except Exception as e:
            logger.debug("AKShare stock_zh_a_hist 失败 %s: %s", symbol, e)
            return None
        if df is None or df.empty:
            return None
        df = df.tail(period_map)
        if df.empty:
            return None
        out = pd.DataFrame({
            'date': pd.to_datetime(df['日期']),
            'open': df['开盘'].astype(float),
            'high': df['最高'].astype(float),
            'low': df['最低'].astype(float),
            'close': df['收盘'].astype(float),
            'volume': df['成交量'].astype(float),
        })
        out.set_index('date', inplace=True)
        out.sort_index(inplace=True)
        return out
    
    def _try_akshare_futures_historical(self, symbol: str, period: str) -> Optional[pd.DataFrame]:
        """AKShare 期货/商品历史日线回退"""
        if not HAS_AKSHARE or _ak is None:
            return None
        try:
            from utils.akshare_futures import is_futures_like, get_futures_hist
            if not is_futures_like(symbol):
                return None
            df = get_futures_hist(symbol)
        except Exception as e:
            logger.debug("AKShare futures hist 失败 %s: %s", symbol, e)
            return None
        if df is None or df.empty:
            return None
        period_map = {
            '1d': 1, '1w': 5, '1m': 20, '3m': 60, '6m': 120, '1y': 252, '2y': 504, '3y': 756, '5y': 1260,
        }.get(period, 252)
        df = df.tail(period_map)
        if df.empty:
            return None
        out = pd.DataFrame({
            'date': pd.to_datetime(df.index if 'date' not in df.columns else df['date']),
            'open': df.get('open', df.iloc[:, 0]).astype(float),
            'high': df.get('high', df.iloc[:, 0]).astype(float),
            'low': df.get('low', df.iloc[:, 0]).astype(float),
            'close': df.get('close', df.iloc[:, 0]).astype(float),
            'volume': df.get('volume', pd.Series(0, index=df.index)).astype(float),
        })
        out.set_index('date', inplace=True)
        out.sort_index(inplace=True)
        return out
    
    def _try_akshare_stock_realtime(self, symbol: str) -> Optional[Dict]:
        """AKShare 股票/ETF 实时报价回退"""
        if not HAS_AKSHARE or _ak is None:
            return None
        try:
            from utils.akshare_futures import is_futures_like
            if is_futures_like(symbol):
                return None
            df = _ak.stock_zh_a_spot_em()
        except Exception as e:
            logger.debug("AKShare stock_zh_a_spot_em 失败: %s", e)
            return None
        if df is None or df.empty:
            return None
        mask = df['代码'] == str(symbol)
        row = df.loc[mask]
        if row.empty:
            return None
        r = row.iloc[0]
        price = r.get('最新价') or r.get('最新价格') or r.get('close')
        try:
            price = float(price)
        except (TypeError, ValueError):
            return None
        if price <= 0:
            return None
        return {
            'timestamp': datetime.now().isoformat(),
            'symbol': symbol,
            'index_price': price,
            'prev_close': safe_float(r.get('昨收') or r.get('昨收价')),
            'open': safe_float(r.get('今开') or r.get('开盘价')) or price,
            'high': safe_float(r.get('最高') or r.get('最高价')) or price,
            'low': safe_float(r.get('最低') or r.get('最低价')) or price,
            'volume': safe_float(r.get('成交量'), default=0),
            'source': 'akshare_stock_realtime',
        }
    
    def _try_sina_realtime(self, symbol: str) -> Optional[Dict]:
        """新浪财经实时行情"""
        try:
            from utils.akshare_futures import is_futures_like
            if is_futures_like(symbol):
                return None
        except Exception:
            pass
        sina_symbol = symbol if str(symbol).startswith(("sh", "sz", "bj")) else f"sh{symbol}"
        url = f"https://hq.sinajs.cn/list={sina_symbol}"
        req = urllib.request.Request(url)
        req.add_header('Referer', 'https://finance.sina.com.cn')
        req.add_header('User-Agent', 'Mozilla/5.0')
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = resp.read().decode('gbk', errors='replace')
        if not raw or '="' not in raw:
            return None
        parts = raw.split('=', 1)[1].strip().strip('";').split(',')
        if len(parts) < 32:
            return None
        try:
            price = float(parts[3])
        except (TypeError, ValueError):
            return None
        if price <= 0:
            return None
        return {
            'timestamp': datetime.now().isoformat(),
            'symbol': symbol,
            'index_price': price,
            'prev_close': safe_float(parts[2]),
            'open': safe_float(parts[1]),
            'high': safe_float(parts[4]),
            'low': safe_float(parts[5]),
            'volume': safe_float(parts[8], default=0),
            'source': 'sina_realtime',
        }
    
    def _try_sina_futures_realtime(self, symbol: str) -> Optional[Dict]:
        """新浪期货实时报价"""
        sina_symbol = symbol if str(symbol).startswith("nf_") else f"nf_{symbol}"
        url = f"https://hq.sinajs.cn/list={sina_symbol}"
        req = urllib.request.Request(url)
        req.add_header('Referer', 'https://finance.sina.com.cn')
        req.add_header('User-Agent', 'Mozilla/5.0')
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = resp.read().decode('gbk', errors='replace')
        if not raw or '="' not in raw:
            return None
        parts = raw.split('=', 1)[1].strip().strip('";').split(',')
        if len(parts) < 6:
            return None
        try:
            price = float(parts[0])
        except (TypeError, ValueError):
            return None
        if price <= 0:
            return None
        return {
            'timestamp': datetime.now().isoformat(),
            'symbol': symbol,
            'index_price': price,
            'prev_close': safe_float(parts[2]) if len(parts) > 2 else None,
            'open': safe_float(parts[1]) if len(parts) > 1 else price,
            'high': safe_float(parts[3]) if len(parts) > 3 else price,
            'low': safe_float(parts[4]) if len(parts) > 4 else price,
            'volume': safe_float(parts[5], default=0) if len(parts) > 5 else 0,
            'source': 'sina_futures_realtime',
        }
    
    def _try_sina_futures_historical(self, symbol: str, period: str) -> Optional[pd.DataFrame]:
        """新浪期货历史日线回退

        先尝试多个新浪历史接口；失败后回退到 AKShare 新浪接口；
        再失败则返回 None。
        """
        period_days = {
            '1d': 1, '1w': 5, '1m': 20, '3m': 60, '6m': 120, '1y': 252,
            '2y': 504, '3y': 756, '5y': 1260,
        }.get(period, 252)

        # 标准化为新浪 nf_ 代码
        sina_symbol = symbol if str(symbol).startswith("nf_") else f"nf_{symbol}"
        raw_symbol = str(symbol).replace("nf_", "")

        # 尝试多个新浪接口
        candidates = [
            f"http://stock2.finance.sina.com.cn/futures/api/jsonp.php/var%20_V21052021_4_12=/InnerFuturesNewService.getDailyKLine?symbol={raw_symbol}",
            f"http://stock.finance.sina.com.cn/futures/api/jsonp.php/var%20_V21052021_4_12=/InnerFuturesNewService.getDailyKLine?symbol={raw_symbol}",
            f"http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol={raw_symbol}&scale=240&ma=no&datalen={period_days}",
        ]
        for url in candidates:
            try:
                req = urllib.request.Request(url)
                req.add_header('Referer', 'https://finance.sina.com.cn')
                req.add_header('User-Agent', 'Mozilla/5.0')
                with urllib.request.urlopen(req, timeout=5) as resp:
                    raw = resp.read().decode('utf-8', errors='replace')
                if not raw or 'null' in raw.strip().lower() or '""' in raw:
                    continue
                # 解析 JSON/JSONP
                text = raw
                if '=' in text:
                    text = text.split('=', 1)[1].strip()
                if text.endswith(';'):
                    text = text[:-1]
                data = json.loads(text)
                if not data:
                    continue
                if isinstance(data, dict):
                    data = data.get('data') or data
                if not isinstance(data, list):
                    continue
                rows = []
                for item in data:
                    d = item.get('d') or item.get('date') or item.get('trade_date')
                    c = item.get('c') or item.get('close') or item.get('match')
                    if not d or c is None:
                        continue
                    try:
                        close = float(c)
                    except (TypeError, ValueError):
                        continue
                    if close <= 0:
                        continue
                    rows.append({'date': d, 'close': close})
                if not rows:
                    continue
                out = pd.DataFrame(rows)
                out['date'] = pd.to_datetime(out['date'])
                out.set_index('date', inplace=True)
                out.sort_index(inplace=True)
                return out
            except Exception:
                continue
    
    def _try_ifind_ths_rq_realtime(self, symbol: str) -> Optional[Dict]:
        """自建命令回退：iFinD THS_RQ 期货实时行情"""
        if not _HAS_IFIND_FUTURES_QUOTES:
            return None
        try:
            quotes = _ifind_futures_quotes([symbol])
            item = quotes.get(symbol)
            if not item:
                return None
            latest = safe_float(item.get('latest') or item.get('latest_price'))
            if not latest:
                return None
            self.source_health.setdefault('ifind_ths_rq', {'ok': False, 'last_error': None})
            self.source_health['ifind_ths_rq']['ok'] = True
            return {
                'timestamp': datetime.now().isoformat(),
                'symbol': symbol,
                'index_price': latest,
                'prev_close': safe_float(item.get('prev_close')),
                'open': safe_float(item.get('open')),
                'high': safe_float(item.get('high')),
                'low': safe_float(item.get('low')),
                'volume': safe_float(item.get('volume'), default=0),
                'source': 'ifind_ths_rq',
            }
        except Exception as e:
            self.source_health.setdefault('ifind_ths_rq', {'ok': False, 'last_error': None})
            self.source_health['ifind_ths_rq']['ok'] = False
            self.source_health['ifind_ths_rq']['last_error'] = str(e)
            logger.debug("iFinD THS_RQ 获取失败 %s: %s", symbol, e)
            return None
'''

if old not in text:
    print('目标旧方法块未找到，可能已清理或格式有变化')
else:
    text = text.replace(old, '')
    file_path.write_text(text, encoding='utf-8')
    print('已清理旧数据源回退方法')
