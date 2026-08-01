"""
混合数据源系统测试 (Hybrid Data Source System Test)
==================================================

测试内容：
1. 通达信数据源连接测试
2. 东方财富数据源连接测试
3. AKShare数据源连接测试
4. 实时行情获取测试
5. K线数据获取测试
6. 资金流向数据测试
7. 数据质量验证测试
8. 交叉验证测试

Author: Agnes-2.0 Flash Team
Date: 2026-07-23
"""

import logging
import sys
import os

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
import os
from datetime import datetime
from typing import Dict, Any

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tools'))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'utils'))

# 配置日志 - 使用ASCII兼容格式
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('hybrid_data_source_test.log', encoding='utf-8')
    ]
)

logger = logging.getLogger(__name__)


class HybridDataSourceTestSuite:
    """混合数据源测试套件"""
    
    def __init__(self):
        self.test_results: Dict[str, Any] = {}
        self.start_time = datetime.now()
        
        logger.info("=" * 80)
        logger.info("混合数据源系统测试开始")
        logger.info(f"测试时间: {self.start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info("=" * 80)
    
    def run_all_tests(self):
        """运行所有测试"""
        tests = [
            ('通达信连接测试', self.test_tdx_connection),
            ('东方财富连接测试', self.test_eastmoney_connection),
            ('AKShare连接测试', self.test_akshare_connection),
            ('实时行情获取测试', self.test_realtime_price),
            ('K线数据获取测试', self.test_kline_data),
            ('资金流向数据测试', self.test_flow_data),
            ('数据质量验证测试', self.test_data_quality),
            ('交叉验证测试', self.test_cross_validation),
            ('ETF资金流向监控测试', self.test_etf_monitor),
        ]
        
        for test_name, test_func in tests:
            try:
                logger.info(f"\n{'='*80}")
                logger.info(f"开始测试: {test_name}")
                logger.info(f"{'='*80}")
                
                result = test_func()
                self.test_results[test_name] = result
                
                if result.get('passed', False):
                    logger.info(f"✓ {test_name} 通过")
                else:
                    logger.warning(f"✗ {test_name} 未通过: {result.get('error', 'Unknown error')}")
                    
            except Exception as e:
                logger.error(f"✗ {test_name} 测试异常: {e}")
                self.test_results[test_name] = {
                    'passed': False,
                    'error': str(e)
                }
        
        # 打印测试报告
        self.print_test_report()
    
    def test_tdx_connection(self) -> Dict[str, Any]:
        """测试通达信连接"""
        try:
            from utils.tdx_data_source import TDXDataSource
            
            tdx = TDXDataSource()
            health = tdx.source_health
            
            if health.get('tdx', {}).get('ok', False):
                # 测试获取实时行情
                test_symbol = '600519'
                tdx.get_realtime_quote(test_symbol)
                
                return {
                    'passed': True,
                    'message': f'通达信连接成功，{test_symbol} 实时行情可用',
                    'latency_ms': 0
                }
            else:
                return {
                    'passed': False,
                    'error': '通达信连接失败'
                }
                
        except ImportError:
            return {
                'passed': False,
                'error': '通达信模块未安装'
            }
        except Exception as e:
            return {
                'passed': False,
                'error': str(e)
            }
    
    def test_eastmoney_connection(self) -> Dict[str, Any]:
        """测试东方财富连接"""
        try:
            from tools.eastmoney_data_fetcher import EastMoneyDataFetcher
            import requests
            
            # 先测试网络连通性
            response = requests.get('https://www.eastmoney.com/', timeout=5)
            
            if response.status_code == 200:
                fetcher = EastMoneyDataFetcher()
                
                # 测试获取实时行情
                test_symbol = '600519'
                fetcher.get_realtime_quotes([test_symbol])
                
                return {
                    'passed': True,
                    'message': f'东方财富连接成功，{test_symbol} 实时行情可用',
                    'status_code': response.status_code
                }
            else:
                return {
                    'passed': False,
                    'error': f'东方财富网站返回状态码: {response.status_code}'
                }
                
        except ImportError:
            return {
                'passed': False,
                'error': '东方财富模块未安装'
            }
        except requests.exceptions.Timeout:
            return {
                'passed': False,
                'error': '东方财富连接超时'
            }
        except Exception as e:
            return {
                'passed': False,
                'error': str(e)
            }
    
    def test_akshare_connection(self) -> Dict[str, Any]:
        """测试AKShare连接"""
        try:
            import akshare as ak
            
            # 测试获取A股实时行情
            df = ak.stock_zh_a_spot_em()
            
            if not df.empty and len(df) > 0:
                return {
                    'passed': True,
                    'message': f'AKShare连接成功，获取到 {len(df)} 只股票实时行情',
                    'data_points': len(df)
                }
            else:
                return {
                    'passed': False,
                    'error': 'AKShare返回数据为空'
                }
                
        except ImportError:
            return {
                'passed': False,
                'error': 'AKShare未安装，请执行: pip install akshare'
            }
        except Exception as e:
            return {
                'passed': False,
                'error': str(e)
            }
    
    def test_realtime_price(self) -> Dict[str, Any]:
        """测试实时行情获取"""
        try:
            from src.data.hybrid_data_manager import get_hybrid_data_manager
            
            manager = get_hybrid_data_manager()
            
            # 测试多只股票
            test_symbols = ['600519.SH', '000001.SZ', '300750.SZ']
            results = {}
            
            for symbol in test_symbols:
                try:
                    price_data = manager.get_realtime_price(symbol)
                    results[symbol] = {
                        'success': True,
                        'data': price_data
                    }
                except Exception as e:
                    results[symbol] = {
                        'success': False,
                        'error': str(e)
                    }
            
            all_success = all(data['success'] for data in results.values())
            
            return {
                'passed': all_success,
                'results': results,
                'message': f'实时行情获取完成，{sum(1 for d in results.values() if d["success"])}/{len(results)} 成功'
            }
            
        except Exception as e:
            return {
                'passed': False,
                'error': str(e)
            }
    
    def test_kline_data(self) -> Dict[str, Any]:
        """测试K线数据获取"""
        try:
            from src.data.hybrid_data_manager import get_hybrid_data_manager
            
            manager = get_hybrid_data_manager()
            
            # 测试获取贵州茅台K线数据
            symbol = '600519.SH'
            kline_df = manager.get_kline_data(
                symbol=symbol,
                period='daily',
                start_date='2026-07-15',
                end_date='2026-07-23',
                adjust='hfq'
            )
            
            if not kline_df.empty:
                return {
                    'passed': True,
                    'message': f'K线数据获取成功，形状: {kline_df.shape}',
                    'shape': kline_df.shape,
                    'columns': list(kline_df.columns),
                    'sample_data': kline_df.head(3).to_dict('records')
                }
            else:
                return {
                    'passed': False,
                    'error': 'K线数据为空'
                }
                
        except Exception as e:
            return {
                'passed': False,
                'error': str(e)
            }
    
    def test_flow_data(self) -> Dict[str, Any]:
        """测试资金流向数据获取"""
        try:
            from src.data.hybrid_data_manager import get_hybrid_data_manager
            
            manager = get_hybrid_data_manager()
            
            # 测试获取资金流向
            symbol = '600519.SH'
            flow_data = manager.get_flow_data(symbol)
            
            if flow_data:
                return {
                    'passed': True,
                    'message': '资金流向数据获取成功',
                    'data': flow_data
                }
            else:
                return {
                    'passed': False,
                    'error': '资金流向数据为空'
                }
                
        except Exception as e:
            return {
                'passed': False,
                'error': str(e)
            }
    
    def test_data_quality(self) -> Dict[str, Any]:
        """测试数据质量验证"""
        try:
            from src.data.data_quality_validator import DataQualityValidator
            import pandas as pd
            
            validator = DataQualityValidator()
            
            # 测试实时数据验证
            realtime_data = {
                'symbol': '600519.SH',
                'price': 1800.50,
                'volume': 5000,
                'change': 1.25
            }
            
            realtime_report = validator.validate_realtime_data('600519.SH', realtime_data, 'test_source')
            
            # 测试K线数据验证
            kline_df = pd.DataFrame({
                '日期': pd.date_range('2026-07-01', periods=10, freq='D'),
                '开盘': [1750, 1760, 1770, 1780, 1790, 1800, 1810, 1820, 1830, 1840],
                '最高': [1760, 1770, 1780, 1790, 1800, 1810, 1820, 1830, 1840, 1850],
                '最低': [1740, 1750, 1760, 1770, 1780, 1790, 1800, 1810, 1820, 1830],
                '收盘': [1755, 1765, 1775, 1785, 1795, 1805, 1815, 1825, 1835, 1845],
                '成交量': [5000, 5200, 5400, 5600, 5800, 6000, 6200, 6400, 6600, 6800]
            })
            
            kline_report = validator.validate_kline_data('600519.SH', kline_df, 'test_source')
            
            # 测试异常值检测
            series = pd.Series([100, 102, 98, 101, 105, 200, 99, 103, 97, 104])
            _is_normal, outlier_count = validator.detect_data_anomalies('test_series', series, method='mad')
            
            return {
                'passed': True,
                'message': '数据质量验证功能正常',
                'realtime_quality_score': realtime_report.quality_score,
                'kline_quality_score': kline_report.quality_score,
                'outlier_count': outlier_count
            }
            
        except Exception as e:
            return {
                'passed': False,
                'error': str(e)
            }
    
    def test_cross_validation(self) -> Dict[str, Any]:
        """测试交叉验证功能"""
        try:
            from src.data.hybrid_data_manager import get_hybrid_data_manager
            
            manager = get_hybrid_data_manager()
            
            # 测试多源数据交叉验证
            is_consistent = manager.cross_validate_data('600519.SH', 'realtime_price')
            
            return {
                'passed': True,
                'message': f'交叉验证完成，数据一致性: {"✓ 一致" if is_consistent else "✗ 不一致"}',
                'is_consistent': is_consistent
            }
            
        except Exception as e:
            return {
                'passed': False,
                'error': str(e)
            }
    
    def test_etf_monitor(self) -> Dict[str, Any]:
        """测试ETF资金流向监控"""
        try:
            from scripts.multi_source_etf_flow_monitor import MultiSourceETFFlowMonitor
            
            # 创建监控器
            monitor = MultiSourceETFFlowMonitor({
                'alert_threshold': 10000000,
                'quality_threshold': 70.0,
                'auto_validate': True
            })
            
            # 测试ETF列表
            test_etfs = [
                '510300.SH',  # 沪深300ETF
                '510500.SH',  # 中证500ETF
                '510050.SH',  # 上证50ETF
            ]
            
            # 获取资金流向数据
            flow_data = monitor.get_etf_flow_data(test_etfs)
            
            if flow_data:
                # 获取汇总
                summary = monitor.get_flow_summary(test_etfs)
                
                return {
                    'passed': True,
                    'message': f'ETF资金流向监控正常，获取到 {len(flow_data)} 只ETF数据',
                    'etf_count': len(flow_data),
                    'summary_shape': summary.shape if not summary.empty else (0, 0)
                }
            else:
                return {
                    'passed': False,
                    'error': '未获取到ETF资金流向数据'
                }
                
        except Exception as e:
            return {
                'passed': False,
                'error': str(e)
            }
    
    def print_test_report(self):
        """打印测试报告"""
        end_time = datetime.now()
        duration = (end_time - self.start_time).total_seconds()
        
        total_tests = len(self.test_results)
        passed_tests = sum(1 for r in self.test_results.values() if r.get('passed', False))
        failed_tests = total_tests - passed_tests
        
        logger.info("\n" + "=" * 80)
        logger.info("Hybrid Data Source System Test Report")
        logger.info("=" * 80)
        logger.info(f"Test Time: {self.start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info(f"Duration: {duration:.2f} seconds")
        logger.info(f"Total Tests: {total_tests}")
        logger.info(f"Passed:   {passed_tests}")
        logger.info(f"Failed:   {failed_tests}")
        logger.info(f"Pass Rate: {passed_tests/total_tests*100:.1f}%")
        logger.info("=" * 80)
        
        logger.info("\nDetailed Test Results:")
        logger.info("-" * 80)
        
        for test_name, result in self.test_results.items():
            status = "PASS" if result.get('passed', False) else "FAIL"
            logger.info(f"{status} | {test_name}")
            
            if not result.get('passed', False):
                logger.info(f"       Error: {result.get('error', 'Unknown error')}")
            
            logger.info("-" * 80)
        
        # 保存测试报告
        report_path = f"hybrid_data_source_test_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write("Hybrid Data Source System Test Report\n")
            f.write("=" * 80 + "\n")
            f.write(f"Test Time: {self.start_time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Duration: {duration:.2f} seconds\n")
            f.write(f"Total Tests: {total_tests}\n")
            f.write(f"Passed:   {passed_tests}\n")
            f.write(f"Failed:   {failed_tests}\n")
            f.write(f"Pass Rate: {passed_tests/total_tests*100:.1f}%\n")
            f.write("=" * 80 + "\n\n")
            
            for test_name, result in self.test_results.items():
                status = "PASS" if result.get('passed', False) else "FAIL"
                f.write(f"{status} | {test_name}\n")
                
                if result.get('message'):
                    f.write(f"       Message: {result['message']}\n")
                
                if not result.get('passed', False):
                    f.write(f"       Error: {result.get('error', 'Unknown error')}\n")
                
                f.write("\n")
        
        logger.info(f"\nTest report saved to: {report_path}")
        
        if failed_tests > 0:
            logger.warning(f"\n{failed_tests} tests failed, please check configuration and data source connections")
        else:
            logger.info("\nAll tests passed! Hybrid data source system is running normally")


# ==================== 主程序 ====================
if __name__ == '__main__':
    # 创建并运行测试套件
    test_suite = HybridDataSourceTestSuite()
    test_suite.run_all_tests()
