"""
多数据源ETF资金流向监控系统 (Multi-Source ETF Flow Monitor)
========================================================

核心功能：
1. 整合通达信、东方财富、AKShare多个数据源
2. 实时监测ETF资金流向
3. 主力资金/散户资金分离统计
4. 异常资金流动告警
5. 多源数据交叉验证

Author: Agnes-2.0 Flash Team
Date: 2026-07-23
"""

import logging
from typing import Dict, List, Any, Optional
from datetime import datetime
from dataclasses import dataclass
import pandas as pd

from src.data.hybrid_data_manager import get_hybrid_data_manager
from src.data.data_quality_validator import DataQualityValidator

logger = logging.getLogger(__name__)


@dataclass
class ETFFlowData:
    """ETF资金流向数据"""
    symbol: str
    name: str
    date: str
    main_inflow: float           # 主力净流入
    main_ratio: float            # 主力净流入占比
    retail_inflow: float         # 散户净流入
    retail_ratio: float          # 散户净流入占比
    total_volume: float          # 总成交量
    total_amount: float          # 总成交额
    price: float                 # 最新价格
    change_pct: float            # 涨跌幅
    source: str                  # 数据来源
    quality_score: float = 0.0   # 数据质量评分
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'symbol': self.symbol,
            'name': self.name,
            'date': self.date,
            'main_inflow': self.main_inflow,
            'main_ratio': self.main_ratio,
            'retail_inflow': self.retail_inflow,
            'retail_ratio': self.retail_ratio,
            'total_volume': self.total_volume,
            'total_amount': self.total_amount,
            'price': self.price,
            'change_pct': self.change_pct,
            'source': self.source,
            'quality_score': self.quality_score
        }


class MultiSourceETFFlowMonitor:
    """
    多数据源ETF资金流向监控器
    
    数据源优先级：
    1. 东方财富 - 资金流向数据最详细
    2. 通达信 - 实时资金流
    3. AKShare - 补充数据源
    """
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        初始化监控器
        
        Args:
            config: 配置字典
        """
        self.config = config or {
            'alert_threshold': 10000000,  # 告警阈值（1000万）
            'quality_threshold': 70.0,
            'auto_validate': True
        }
        
        # 获取混合数据源管理器
        self.data_manager = get_hybrid_data_manager()
        
        # 数据质量验证器
        self.validator = DataQualityValidator()
        
        # 历史数据缓存
        self._history: Dict[str, List[ETFFlowData]] = {}
        
        # 告警记录
        self._alerts: List[Dict[str, Any]] = []
        
        logger.info("多数据源ETF资金流向监控器已初始化")
    
    def get_etf_flow_data(self, symbols: List[str], 
                         date: Optional[str] = None) -> List[ETFFlowData]:
        """
        获取ETF资金流向数据
        
        Args:
            symbols: ETF代码列表
            date: 日期 (YYYY-MM-DD)，默认为今日
            
        Returns:
            ETF资金流向数据列表
        """
        if date is None:
            date = datetime.now().strftime('%Y-%m-%d')
        
        all_flow_data = []
        
        for symbol in symbols:
            try:
                # 优先从东方财富获取
                flow_data = self._fetch_from_eastmoney(symbol, date)
                
                if flow_data:
                    # 数据质量验证
                    if self.config.get('auto_validate', True):
                        quality_report = self.validator.validate_realtime_data(
                            symbol, flow_data, 'eastmoney'
                        )
                        flow_data['quality_score'] = quality_report.quality_score
                        
                        if quality_report.quality_score < self.config['quality_threshold']:
                            logger.warning(f"ETF {symbol} 数据质量不合格: {quality_report.quality_score}")
                    
                    all_flow_data.append(flow_data)
                    
                    # 交叉验证
                    if self.config.get('cross_validation', False):
                        self._cross_validate(symbol, 'flow_data', {'eastmoney': flow_data})
                else:
                    logger.warning(f"无法获取ETF {symbol} 的资金流向数据")
                    
            except Exception as e:
                logger.error(f"获取ETF {symbol} 资金流向失败: {e}")
                continue
        
        # 更新历史记录
        self._update_history(symbols, all_flow_data)
        
        # 检查告警
        self._check_alerts(all_flow_data)
        
        logger.info(f"成功获取 {len(all_flow_data)} 只ETF的资金流向数据")
        
        return all_flow_data
    
    def _fetch_from_eastmoney(self, symbol: str, date: str) -> Optional[Dict[str, Any]]:
        """从东方财富获取ETF资金流向"""
        try:
            from tools.eastmoney_data_fetcher import EastMoneyDataFetcher
            fetcher = EastMoneyDataFetcher()
            
            # 获取资金流向数据
            flow_data = fetcher.get_stock_main_money_flow(date, stock_code=symbol)
            
            if flow_data:
                return {
                    'symbol': symbol,
                    'main_inflow': flow_data.get('main_net_flow', 0),
                    'main_ratio': flow_data.get('main_net_flow_ratio', 0),
                    'retail_inflow': flow_data.get('retail_net_flow', 0),
                    'retail_ratio': flow_data.get('retail_net_flow_ratio', 0),
                    'total_volume': flow_data.get('volume', 0),
                    'total_amount': flow_data.get('amount', 0),
                    'price': flow_data.get('price', 0),
                    'change_pct': flow_data.get('change', 0),
                    'source': 'eastmoney'
                }
        except Exception as e:
            logger.error(f"东方财富获取ETF {symbol} 数据失败: {e}")
        
        return None
    
    def _fetch_from_tdx(self, symbol: str, date: str) -> Optional[Dict[str, Any]]:
        """从通达信获取ETF资金流向"""
        try:
            from utils.tdx_data_source import TDXDataSource
            tdx = TDXDataSource()
            
            flow_data = tdx.get_stock_fund_flow_adjust(symbol, date)
            
            if flow_data:
                return {
                    'symbol': symbol,
                    'main_inflow': flow_data.get('main_net_flow', 0),
                    'main_ratio': flow_data.get('main_net_flow_ratio', 0),
                    'retail_inflow': flow_data.get('retail_net_flow', 0),
                    'retail_ratio': flow_data.get('retail_net_flow_ratio', 0),
                    'total_volume': flow_data.get('volume', 0),
                    'total_amount': flow_data.get('amount', 0),
                    'price': flow_data.get('price', 0),
                    'change_pct': flow_data.get('change', 0),
                    'source': 'tdx'
                }
        except Exception as e:
            logger.error(f"通达信获取ETF {symbol} 数据失败: {e}")
        
        return None
    
    def _fetch_from_akshare(self, symbol: str, date: str) -> Optional[Dict[str, Any]]:
        """从AKShare获取ETF资金流向"""
        try:
            import akshare as ak
            
            # 获取ETF资金流向
            df = ak.stock_individual_fund_flow(stock=symbol.split('.')[0], market='')
            
            if not df.empty:
                return {
                    'symbol': symbol,
                    'main_inflow': df.iloc[-1].get('main_net_flow', 0),
                    'main_ratio': df.iloc[-1].get('main_net_flow_ratio', 0),
                    'retail_inflow': df.iloc[-1].get('retail_net_flow', 0),
                    'retail_ratio': df.iloc[-1].get('retail_net_flow_ratio', 0),
                    'total_volume': df.iloc[-1].get('volume', 0),
                    'total_amount': df.iloc[-1].get('amount', 0),
                    'price': df.iloc[-1].get('price', 0),
                    'change_pct': df.iloc[-1].get('change', 0),
                    'source': 'akshare'
                }
        except Exception as e:
            logger.error(f"AKShare获取ETF {symbol} 数据失败: {e}")
        
        return None
    
    def _cross_validate(self, symbol: str, data_type: str, 
                       source_data: Dict[str, Any]):
        """交叉验证多源数据"""
        try:
            report = self.validator.cross_validate_multiple_sources(
                symbol, data_type, source_data
            )
            
            if not report.is_acceptable:
                logger.warning(f"ETF {symbol} 多源数据交叉验证失败: {report.issues}")
                
                # 触发告警
                self._alerts.append({
                    'timestamp': datetime.now().isoformat(),
                    'symbol': symbol,
                    'type': 'data_validation_failed',
                    'issues': report.issues,
                    'quality_score': report.quality_score
                })
        except Exception as e:
            logger.error(f"交叉验证失败: {e}")
    
    def _check_alerts(self, flow_data_list: List[Dict[str, Any]]):
        """检查是否需要告警"""
        for flow_data in flow_data_list:
            main_inflow = flow_data.get('main_inflow', 0)
            
            # 大额资金流动告警
            if abs(main_inflow) > self.config['alert_threshold']:
                alert_message = (
                    f"[资金流动告警] ETF {flow_data.get('symbol')} "
                    f"主力净流入: {main_inflow:,.0f}元 "
                    f"({flow_data.get('main_ratio', 0):.2%})"
                )
                
                self._alerts.append({
                    'timestamp': datetime.now().isoformat(),
                    'symbol': flow_data.get('symbol'),
                    'type': 'large_flow',
                    'main_inflow': main_inflow,
                    'message': alert_message
                })
                
                logger.warning(alert_message)
    
    def _update_history(self, symbols: List[str], flow_data: List[Dict[str, Any]]):
        """更新历史数据"""
        for data in flow_data:
            symbol = data.get('symbol')
            if symbol not in self._history:
                self._history[symbol] = []
            
            etf_flow = ETFFlowData(
                symbol=data.get('symbol'),
                name=data.get('name', ''),
                date=data.get('date', datetime.now().strftime('%Y-%m-%d')),
                main_inflow=data.get('main_inflow', 0),
                main_ratio=data.get('main_ratio', 0),
                retail_inflow=data.get('retail_inflow', 0),
                retail_ratio=data.get('retail_ratio', 0),
                total_volume=data.get('total_volume', 0),
                total_amount=data.get('total_amount', 0),
                price=data.get('price', 0),
                change_pct=data.get('change_pct', 0),
                source=data.get('source', 'unknown'),
                quality_score=data.get('quality_score', 0)
            )
            
            self._history[symbol].append(etf_flow)
            
            # 只保留最近90天
            if len(self._history[symbol]) > 90:
                self._history[symbol] = self._history[symbol][-90:]
    
    def get_flow_summary(self, symbols: List[str]) -> pd.DataFrame:
        """
        获取资金流向汇总
        
        Args:
            symbols: ETF代码列表
            
        Returns:
            汇总DataFrame
        """
        flow_data = self.get_etf_flow_data(symbols)
        
        if not flow_data:
            return pd.DataFrame()
        
        df = pd.DataFrame([data.to_dict() for data in flow_data])
        
        # 排序
        df = df.sort_values(by='main_inflow', ascending=False)
        
        return df
    
    def get_top_inflow_etfs(self, symbols: List[str], top_n: int = 10) -> pd.DataFrame:
        """
        获取资金流入最多的ETF
        
        Args:
            symbols: ETF代码列表
            top_n: 返回数量
            
        Returns:
            Top N资金流入ETF
        """
        summary = self.get_flow_summary(symbols)
        
        if summary.empty:
            return pd.DataFrame()
        
        return summary.head(top_n)
    
    def get_top_outflow_etfs(self, symbols: List[str], top_n: int = 10) -> pd.DataFrame:
        """
        获取资金流出最多的ETF
        
        Args:
            symbols: ETF代码列表
            top_n: 返回数量
            
        Returns:
            Top N资金流出ETF
        """
        summary = self.get_flow_summary(symbols)
        
        if summary.empty:
            return pd.DataFrame()
        
        return summary.nsmallest(top_n, 'main_inflow')
    
    def get_alerts(self, clear: bool = False) -> List[Dict[str, Any]]:
        """
        获取告警记录
        
        Args:
            clear: 是否清除告警列表
            
        Returns:
            告警列表
        """
        alerts = self._alerts.copy()
        
        if clear:
            self._alerts.clear()
        
        return alerts
    
    def get_data_source_status(self) -> Dict[str, Any]:
        """获取数据源状态"""
        return self.data_manager.get_data_source_status()


# ==================== 使用示例 ====================
if __name__ == '__main__':
    # 配置
    config = {
        'alert_threshold': 10000000,  # 1000万告警阈值
        'quality_threshold': 70.0,
        'auto_validate': True,
        'cross_validation': True
    }
    
    # 创建监控器
    monitor = MultiSourceETFFlowMonitor(config)
    
    # 测试ETF列表
    test_etfs = [
        '510300.SH',  # 沪深300ETF
        '510500.SH',  # 中证500ETF
        '159919.SZ',  # 创业板ETF
        '510050.SH',  # 上证50ETF
        '512880.SH',  # 证券公司ETF
        '512000.SH',  # 券商ETF
        '512760.SH',  # 芯片ETF
        '518880.SH',  # 黄金ETF
    ]
    
    print("\n=== 获取ETF资金流向数据 ===")
    flow_data = monitor.get_etf_flow_data(test_etfs)
    
    if flow_data:
        # 打印汇总
        summary = monitor.get_flow_summary(test_etfs)
        print("\n=== 资金流向汇总 ===")
        print(summary[['symbol', 'name', 'main_inflow', 'main_ratio', 'price', 'change_pct']].to_string())
        
        # 打印资金流入Top 5
        print("\n=== 资金流入Top 5 ===")
        top_inflow = monitor.get_top_inflow_etfs(test_etfs, top_n=5)
        print(top_inflow[['symbol', 'name', 'main_inflow', 'main_ratio']].to_string())
        
        # 打印资金流出Top 5
        print("\n=== 资金流出Top 5 ===")
        top_outflow = monitor.get_top_outflow_etfs(test_etfs, top_n=5)
        print(top_outflow[['symbol', 'name', 'main_inflow', 'main_ratio']].to_string())
        
        # 检查告警
        alerts = monitor.get_alerts(clear=True)
        if alerts:
            print(f"\n=== 告警记录 ({len(alerts)}) ===")
            for alert in alerts:
                print(f"- {alert.get('message', alert.get('type'))}")
    
    # 打印数据源状态
    print("\n=== 数据源状态 ===")
    status = monitor.get_data_source_status()
    for name, info in status.items():
        print(f"{name}: 健康度={info['health_score']}, 优先级={info['priority']}")
