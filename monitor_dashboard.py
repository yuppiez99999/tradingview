# -*- coding: utf-8 -*-
"""
量化策略系统监控仪表板

功能：
- 实时监控系统状态
- 显示关键性能指标
- 风险状况监控
- 策略执行状态
- 系统日志查看

使用方法：
python monitor_dashboard.py

或通过Web界面访问（需要安装flask）:
python monitor_dashboard.py --web
"""

import sys
import os
import time
import threading
import json
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, asdict

# 添加路径
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(current_dir)

# 导入系统组件
from utils.logger import get_logger
from config import Config
from quantitative_strategy_system import QuantitativeStrategySystem
from automated_execution_system import AutomatedExecutionSystem
from utils.data_provider import get_market_data
from utils.risk_metrics import calculate_performance_metrics

# 尝试导入web框架
try:
    from flask import Flask, jsonify, render_template_string
    WEB_AVAILABLE = True
except ImportError:
    WEB_AVAILABLE = False

# 日志配置
logger = get_logger('monitor_dashboard')


@dataclass
class SystemMetrics:
    """系统指标"""
    timestamp: datetime
    portfolio_value: float
    total_return: float
    daily_return: float
    max_drawdown: float
    sharpe_ratio: float
    volatility: float
    var_95: float
    es_95: float
    positions_count: int
    hedge_coverage: float
    risk_level: str
    last_update: str


class MonitorDashboard:
    """监控仪表板"""
    
    def __init__(self):
        self.config = Config.get_instance()
        self.system = None
        self.execution_system = None
        self.running = False
        self.metrics_history = []
        self.max_history_points = 100
        self.update_interval = 30  # 30秒更新一次
        
        # 系统状态
        self.status = {
            'initialized': False,
            'running': False,
            'last_update': None,
            'errors': [],
            'warnings': []
        }
        
        # 初始化系统
        self._init_system()
    
    def _init_system(self):
        """初始化系统"""
        try:
            self.system = QuantitativeStrategySystem(
                total_capital=self.config.total_capital,
                stock_etf_capital=self.config.stock_etf_capital,
                hedge_capital=self.config.hedge_capital
            )
            
            self.execution_system = AutomatedExecutionSystem(
                total_capital=self.config.total_capital
            )
            
            self.status['initialized'] = True
            logger.info("监控仪表板初始化成功")
            
        except Exception as e:
            logger.error(f"系统初始化失败: {e}")
            self.status['errors'].append(f"系统初始化失败: {str(e)}")
    
    def _calculate_metrics(self) -> SystemMetrics:
        """计算系统指标"""
        try:
            # 获取系统摘要
            summary = self.system.get_system_summary()
            
            # 获取市场数据
            market_data = get_market_data()
            
            # 计算性能指标
            if 'portfolio_returns' in summary:
                returns = summary['portfolio_returns']
                prices = summary.get('portfolio_prices', [1] * len(returns))
                
                performance_metrics = calculate_performance_metrics(returns, prices)
            else:
                # 使用默认值
                performance_metrics = {
                    'total_return': 0.0,
                    'max_drawdown': 0.0,
                    'sharpe_ratio': 0.0,
                    'volatility': 0.0,
                    'var_95': 0.0,
                    'es_95': 0.0
                }
            
            # 计算风险等级
            risk_level = self._calculate_risk_level(performance_metrics)
            
            # 创建系统指标
            metrics = SystemMetrics(
                timestamp=datetime.now(),
                portfolio_value=summary.get('portfolio_value', self.config.total_capital),
                total_return=performance_metrics['total_return'],
                daily_return=summary.get('daily_return', 0.0),
                max_drawdown=performance_metrics['max_drawdown'],
                sharpe_ratio=performance_metrics['sharpe_ratio'],
                volatility=performance_metrics['volatility'],
                var_95=performance_metrics['var_95'],
                es_95=performance_metrics['es_95'],
                positions_count=len(summary.get('positions', [])),
                hedge_coverage=summary.get('hedge_coverage', 0.0),
                risk_level=risk_level,
                last_update=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            )
            
            # 添加到历史记录
            self.metrics_history.append(metrics)
            if len(self.metrics_history) > self.max_history_points:
                self.metrics_history.pop(0)
            
            self.status['last_update'] = datetime.now()
            
            return metrics
            
        except Exception as e:
            logger.error(f"计算指标失败: {e}")
            self.status['errors'].append(f"计算指标失败: {str(e)}")
            
            # 返回默认值
            return SystemMetrics(
                timestamp=datetime.now(),
                portfolio_value=self.config.total_capital,
                total_return=0.0,
                daily_return=0.0,
                max_drawdown=0.0,
                sharpe_ratio=0.0,
                volatility=0.0,
                var_95=0.0,
                es_95=0.0,
                positions_count=0,
                hedge_coverage=0.0,
                risk_level="unknown",
                last_update=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            )
    
    def _calculate_risk_level(self, metrics: Dict) -> str:
        """计算风险等级"""
        try:
            var_95 = metrics.get('var_95', 0.0)
            max_dd = metrics.get('max_drawdown', 0.0)
            volatility = metrics.get('volatility', 0.0)
            
            # 风险等级判断
            if max_dd > 0.15 or var_95 > 0.1:
                return "critical"
            elif max_dd > 0.1 or var_95 > 0.08 or volatility > 0.2:
                return "high"
            elif max_dd > 0.05 or var_95 > 0.05 or volatility > 0.15:
                return "medium"
            else:
                return "low"
                
        except Exception as e:
            logger.error(f"计算风险等级失败: {e}")
            return "unknown"
    
    def _get_performance_summary(self) -> Dict:
        """获取性能摘要"""
        try:
            if not self.metrics_history:
                return {}
            
            # 计算统计指标
            portfolio_values = [m.portfolio_value for m in self.metrics_history]
            returns = [m.total_return for m in self.metrics_history]
            
            summary = {
                'current_value': portfolio_values[-1] if portfolio_values else 0,
                'initial_value': portfolio_values[0] if portfolio_values else 0,
                'total_return': (portfolio_values[-1] / portfolio_values[0] - 1) if len(portfolio_values) > 1 else 0,
                'max_value': max(portfolio_values) if portfolio_values else 0,
                'min_value': min(portfolio_values) if portfolio_values else 0,
                'avg_return': sum(returns) / len(returns) if returns else 0,
                'max_return': max(returns) if returns else 0,
                'min_return': min(returns) if returns else 0,
                'last_update': self.status.get('last_update', None)
            }
            
            return summary
            
        except Exception as e:
            logger.error(f"计算性能摘要失败: {e}")
            return {}
    
    def _get_risk_summary(self) -> Dict:
        """获取风险摘要"""
        try:
            if not self.metrics_history:
                return {}
            
            # 计算风险指标
            var_95_values = [m.var_95 for m in self.metrics_history if m.var_95 > 0]
            max_dd_values = [m.max_drawdown for m in self.metrics_history if m.max_drawdown > 0]
            volatility_values = [m.volatility for m in self.metrics_history if m.volatility > 0]
            
            summary = {
                'current_var_95': var_95_values[-1] if var_95_values else 0,
                'max_var_95': max(var_95_values) if var_95_values else 0,
                'current_max_dd': max_dd_values[-1] if max_dd_values else 0,
                'max_max_dd': max(max_dd_values) if max_dd_values else 0,
                'current_volatility': volatility_values[-1] if volatility_values else 0,
                'max_volatility': max(volatility_values) if volatility_values else 0,
                'risk_level_trend': self._calculate_risk_trend(),
                'risk_alerts': self._check_risk_alerts()
            }
            
            return summary
            
        except Exception as e:
            logger.error(f"计算风险摘要失败: {e}")
            return {}
    
    def _calculate_risk_trend(self) -> str:
        """计算风险趋势"""
        try:
            if len(self.metrics_history) < 5:
                return "unknown"
            
            # 检查最近5个指标的风险等级
            recent_risks = [m.risk_level for m in self.metrics_history[-5:]]
            
            # 统计各风险等级出现次数
            risk_counts = {
                'low': recent_risks.count('low'),
                'medium': recent_risks.count('medium'),
                'high': recent_risks.count('high'),
                'critical': recent_risks.count('critical')
            }
            
            # 判断趋势
            if risk_counts['critical'] > 0:
                return "increasing"
            elif risk_counts['high'] > 2:
                return "increasing"
            elif risk_counts['low'] > 3:
                return "decreasing"
            else:
                return "stable"
                
        except Exception as e:
            logger.error(f"计算风险趋势失败: {e}")
            return "unknown"
    
    def _check_risk_alerts(self) -> List[str]:
        """检查风险警报"""
        alerts = []
        
        try:
            if not self.metrics_history:
                return alerts
            
            latest = self.metrics_history[-1]
            
            # 检查VaR
            if latest.var_95 > 0.1:
                alerts.append(f"高VaR风险: {latest.var_95:.4f}")
            
            # 检查最大回撤
            if latest.max_drawdown > 0.15:
                alerts.append(f"最大回撤超限: {latest.max_drawdown:.4f}")
            elif latest.max_drawdown > 0.1:
                alerts.append(f"高回撤警告: {latest.max_drawdown:.4f}")
            
            # 检查波动率
            if latest.volatility > 0.2:
                alerts.append(f"高波动率: {latest.volatility:.4f}")
            
            # 检查收益率
            if latest.total_return < -0.1:
                alerts.append(f"负收益率: {latest.total_return:.4f}")
            
            return alerts
            
        except Exception as e:
            logger.error(f"检查风险警报失败: {e}")
            return [f"风险检查失败: {str(e)}"]
    
    def get_current_status(self) -> Dict:
        """获取当前状态"""
        try:
            metrics = self._calculate_metrics()
            performance_summary = self._get_performance_summary()
            risk_summary = self._get_risk_summary()
            
            status = {
                'system': self.status,
                'metrics': asdict(metrics),
                'performance_summary': performance_summary,
                'risk_summary': risk_summary,
                'positions_count': metrics.positions_count,
                'hedge_coverage': metrics.hedge_coverage,
                'alerts': self._check_risk_alerts(),
                'timestamp': datetime.now().isoformat()
            }
            
            return status
            
        except Exception as e:
            logger.error(f"获取状态失败: {e}")
            return {'error': str(e)}
    
    def start_monitoring(self):
        """开始监控"""
        self.running = True
        logger.info("开始监控系统...")
        
        while self.running:
            try:
                # 更新指标
                self._calculate_metrics()
                
                # 检查风险警报
                alerts = self._check_risk_alerts()
                if alerts:
                    logger.warning(f"风险警报: {alerts}")
                    self.status['warnings'].extend(alerts)
                
                # 等待下次更新
                time.sleep(self.update_interval)
                
            except Exception as e:
                logger.error(f"监控循环出错: {e}")
                self.status['errors'].append(str(e))
                time.sleep(self.update_interval)
    
    def stop_monitoring(self):
        """停止监控"""
        self.running = False
        logger.info("停止监控系统")


def create_web_app(dashboard: MonitorDashboard) -> Flask:
    """创建Web应用"""
    app = Flask(__name__)
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-key-change-me')
    
    @app.route('/')
    def index():
        return render_template_string("""
            <!DOCTYPE html>
            <html>
            <head>
                <title>量化策略系统监控</title>
                <meta charset="utf-8">
                <style>
                    body { font-family: Arial, sans-serif; margin: 20px; }
                    .container { max-width: 1200px; margin: 0 auto; }
                    .header { background: #2c3e50; color: white; padding: 20px; margin-bottom: 20px; }
                    .metric-card { background: #fff; border: 1px solid #ddd; padding: 15px; margin: 10px 0; border-radius: 5px; }
                    .metric-value { font-size: 24px; font-weight: bold; color: #2c3e50; }
                    .metric-label { font-size: 14px; color: #7f8c8d; }
                    .alert { background: #f39c12; padding: 10px; margin: 10px 0; border-radius: 5px; }
                    .error { background: #e74c3c; color: white; padding: 10px; margin: 10px 0; border-radius: 5px; }
                    .status { padding: 15px; margin: 10px 0; border-radius: 5px; }
                    .status-running { background: #27ae60; color: white; }
                    .status-stopped { background: #95a5a6; color: white; }
                    .auto-refresh { margin-top: 20px; text-align: center; }
                    table { width: 100%; border-collapse: collapse; margin: 10px 0; }
                    th, td { padding: 8px; text-align: left; border-bottom: 1px solid #ddd; }
                    th { background: #f2f2f2; }
                </style>
            </head>
            <body>
                <div class="container">
                    <div class="header">
                        <h1>量化策略系统监控</h1>
                        <p>实时监控系统状态和性能指标</p>
                    </div>
                    
                    <div id="status-container"></div>
                    <div id="metrics-container"></div>
                    <div id="performance-container"></div>
                    <div id="risk-container"></div>
                    <div id="alerts-container"></div>
                    
                    <div class="auto-refresh">
                        <button onclick="refreshData()">刷新数据</button>
                        <p>页面每30秒自动刷新</p>
                    </div>
                </div>
                
                <script>
                    function refreshData() {
                        fetch('/api/status')
                            .then(response => response.json())
                            .then(data => {
                                updateStatus(data);
                                updateMetrics(data);
                                updatePerformance(data);
                                updateRisk(data);
                                updateAlerts(data);
                            })
                            .catch(error => console.error('Error:', error));
                    }
                    
                    function updateStatus(data) {
                        const container = document.getElementById('status-container');
                        const statusClass = data.system.running ? 'status-running' : 'status-stopped';
                        container.innerHTML = `
                            <div class="status ${statusClass}">
                                <h2>系统状态</h2>
                                <p>状态: ${data.system.running ? '运行中' : '已停止'}</p>
                                <p>初始化: ${data.system.initialized ? '是' : '否'}</p>
                                <p>最后更新: ${new Date(data.timestamp).toLocaleString()}</p>
                            </div>
                        `;
                    }
                    
                    function updateMetrics(data) {
                        const container = document.getElementById('metrics-container');
                        container.innerHTML = `
                            <div class="metric-card">
                                <h2>当前指标</h2>
                                <table>
                                    <tr><td>组合价值:</td><td class="metric-value">${data.metrics.portfolio_value.toLocaleString()} 元</td></tr>
                                    <tr><td>总收益率:</td><td class="metric-value">${(data.metrics.total_return * 100).toFixed(2)}%</td></tr>
                                    <tr><td>日收益率:</td><td class="metric-value">${(data.metrics.daily_return * 100).toFixed(2)}%</td></tr>
                                    <tr><td>最大回撤:</td><td class="metric-value">${(data.metrics.max_drawdown * 100).toFixed(2)}%</td></tr>
                                    <tr><td>夏普比率:</td><td class="metric-value">${data.metrics.sharpe_ratio.toFixed(2)}</td></tr>
                                    <tr><td>波动率:</td><td class="metric-value">${(data.metrics.volatility * 100).toFixed(2)}%</td></tr>
                                    <tr><td>VaR(95%):</td><td class="metric-value">${(data.metrics.var_95 * 100).toFixed(2)}%</td></tr>
                                    <tr><td>ES(95%):</td><td class="metric-value">${(data.metrics.es_95 * 100).toFixed(2)}%</td></tr>
                                    <tr><td>持仓数量:</td><td class="metric-value">${data.metrics.positions_count}</td></tr>
                                    <tr><td>对冲覆盖:</td><td class="metric-value">${(data.metrics.hedge_coverage * 100).toFixed(1)}%</td></tr>
                                    <tr><td>风险等级:</td><td class="metric-value">${data.metrics.risk_level}</td></tr>
                                </table>
                            </div>
                        `;
                    }
                    
                    function updatePerformance(data) {
                        const container = document.getElementById('performance-container');
                        const perf = data.performance_summary;
                        if (Object.keys(perf).length > 0) {
                            container.innerHTML = `
                                <div class="metric-card">
                                    <h2>性能摘要</h2>
                                    <table>
                                        <tr><td>当前价值:</td><td>${perf.current_value.toLocaleString()} 元</td></tr>
                                        <tr><td>初始价值:</td><td>${perf.initial_value.toLocaleString()} 元</td></tr>
                                        <tr><td>总收益率:</td><td>${(perf.total_return * 100).toFixed(2)}%</td></tr>
                                        <tr><td>最大价值:</td><td>${perf.max_value.toLocaleString()} 元</td></tr>
                                        <tr><td>最小价值:</td><td>${perf.min_value.toLocaleString()} 元</td></tr>
                                        <tr><td>平均收益率:</td><td>${(perf.avg_return * 100).toFixed(2)}%</td></tr>
                                        <tr><td>最大收益率:</td><td>${(perf.max_return * 100).toFixed(2)}%</td></tr>
                                        <tr><td>最小收益率:</td><td>${(perf.min_return * 100).toFixed(2)}%</td></tr>
                                    </table>
                                </div>
                            `;
                        }
                    }
                    
                    function updateRisk(data) {
                        const container = document.getElementById('risk-container');
                        const risk = data.risk_summary;
                        if (Object.keys(risk).length > 0) {
                            container.innerHTML = `
                                <div class="metric-card">
                                    <h2>风险摘要</h2>
                                    <table>
                                        <tr><td>当前VaR:</td><td>${(risk.current_var_95 * 100).toFixed(2)}%</td></tr>
                                        <tr><td>最大VaR:</td><td>${(risk.max_var_95 * 100).toFixed(2)}%</td></tr>
                                        <tr><td>当前回撤:</td><td>${(risk.current_max_dd * 100).toFixed(2)}%</td></tr>
                                        <tr><td>最大回撤:</td><td>${(risk.max_max_dd * 100).toFixed(2)}%</td></tr>
                                        <tr><td>当前波动率:</td><td>${(risk.current_volatility * 100).toFixed(2)}%</td></tr>
                                        <tr><td>最大波动率:</td><td>${(risk.max_volatility * 100).toFixed(2)}%</td></tr>
                                        <tr><td>风险趋势:</td><td>${risk.risk_level_trend}</td></tr>
                                    </table>
                                </div>
                            `;
                        }
                    }
                    
                    function updateAlerts(data) {
                        const container = document.getElementById('alerts-container');
                        if (data.alerts.length > 0) {
                            container.innerHTML = '<div class="alert"><h2>风险警报</h2><ul>' + 
                                data.alerts.map(alert => `<li>${alert}</li>`).join('') + 
                                '</ul></div>';
                        } else {
                            container.innerHTML = '<div class="metric-card"><h2>风险警报</h2>暂无警报</div>';
                        }
                    }
                    
                    // 自动刷新
                    setInterval(refreshData, 30000);
                    
                    // 初始加载
                    refreshData();
                </script>
            </body>
            </html>
        """)
    
    @app.route('/api/status')
    def api_status():
        return jsonify(dashboard.get_current_status())
    
    return app


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description="量化策略系统监控仪表板")
    parser.add_argument('--web', action='store_true', help='启动Web界面')
    args = parser.parse_args()
    
    # 创建监控仪表板
    dashboard = MonitorDashboard()
    
    if args.web and WEB_AVAILABLE:
        # 启动Web界面
        logger.info("启动Web监控界面...")
        app = create_web_app(dashboard)
        app.run(host='0.0.0.0', port=5000, debug=True)
    else:
        # 启动命令行监控
        logger.info("启动命令行监控...")
        
        def monitor_loop():
            while dashboard.running:
                try:
                    # 获取状态
                    status = dashboard.get_current_status()
                    
                    # 打印状态
                    print(f"\n{'='*60}")
                    print(f"量化策略系统监控 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                    print(f"{'='*60}")
                    
                    # 打印基本状态
                    print(f"系统状态: {'运行中' if status['system']['running'] else '已停止'}")
                    print(f"组合价值: {status['metrics']['portfolio_value']:,.2f} 元")
                    print(f"总收益率: {status['metrics']['total_return']*100:.2f}%")
                    print(f"最大回撤: {status['metrics']['max_drawdown']*100:.2f}%")
                    print(f"风险等级: {status['metrics']['risk_level']}")
                    print(f"持仓数量: {status['metrics']['positions_count']}")
                    print(f"对冲覆盖: {status['metrics']['hedge_coverage']*100:.1f}%")
                    
                    # 打印风险指标
                    print(f"VaR(95%): {status['metrics']['var_95']*100:.2f}%")
                    print(f"ES(95%): {status['metrics']['es_95']*100:.2f}%")
                    print(f"波动率: {status['metrics']['volatility']*100:.2f}%")
                    
                    # 打印警报
                    if status['alerts']:
                        print("\n⚠️ 风险警报:")
                        for alert in status['alerts']:
                            print(f"  - {alert}")
                    
                    # 等待30秒
                    time.sleep(30)
                    
                except KeyboardInterrupt:
                    print("\n收到中断信号，停止监控")
                    break
                except Exception as e:
                    logger.error(f"监控循环出错: {e}")
                    time.sleep(30)
        
        try:
            dashboard.running = True
            monitor_loop()
        finally:
            dashboard.running = False
            logger.info("监控系统已停止")


if __name__ == "__main__":
    main()