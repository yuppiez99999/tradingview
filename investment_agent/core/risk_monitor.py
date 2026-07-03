"""
三级风控监控系统
实现个股止损、组合回撤控制、日内断路器三级风控体系
"""

from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import numpy as np
import pandas as pd

from ..config.config import RISK_CONTROL_CONFIG, PORTFOLIO_CONFIG
from .portfolio_manager import PortfolioManager

class RiskLevel(Enum):
    """风险等级"""
    LOW = "LOW"           # 低风险
    MEDIUM = "MEDIUM"     # 中风险
    HIGH = "HIGH"         # 高风险
    CRITICAL = "CRITICAL" # 严重风险

@dataclass
class RiskAlert:
    """风险告警"""
    alert_id: str
    risk_type: str        # 风险类型
    risk_level: RiskLevel
    symbol: Optional[str]     # 相关标的
    current_value: float      # 当前值
    threshold: float          # 阈值
    message: str              # 告警消息
    recommended_action: str   # 建议操作
    confidence: float         # 置信度
    timestamp: datetime       # 告警时间
    is_resolved: bool = False # 是否已解决

@dataclass
class RiskMetrics:
    """组合风险指标"""
    portfolio_value: float          # 组合市值
    portfolio_drawdown: float       # 组合回撤
    var_95: float                   # 95%置信度VaR
    var_99: float                   # 99%置信度VaR
    sharpe_ratio: float             # 夏普比率
    max_drawdown: float             # 最大回撤
    volatility: float               # 波动率
    beta: float                     # Beta系数
    correlation_with_market: float  # 与市场相关性
    risk_level: RiskLevel           # 风险等级
    timestamp: datetime             # 计算时间

class RiskMonitor:
    """三级风控监控器"""
    
    def __init__(self, portfolio_manager: PortfolioManager):
        """
        初始化风控监控器
        
        Args:
            portfolio_manager: 组合管理器
        """
        self.portfolio_manager = portfolio_manager
        self.alerts: List[RiskAlert] = []
        self.risk_history: List[RiskMetrics] = []
        
        # 风控配置
        self.level1_config = RISK_CONTROL_CONFIG['level1_individual_stop']
        self.level2_config = RISK_CONTROL_CONFIG['level2_portfolio_drawdown']
        self.level3_config = RISK_CONTROL_CONFIG['level3_circuit_breaker']
        
        # 日内追踪
        self.daily_start_value: Optional[float] = None
        self.daily_low_value: Optional[float] = None
        self.daily_high_value: Optional[float] = None
        
    def start_trading_day(self):
        """开始交易日，初始化日内追踪"""
        self.daily_start_value = self.portfolio_manager.portfolio.total_value
        self.daily_low_value = self.daily_start_value
        self.daily_high_value = self.daily_start_value
        
    def update_daily_tracking(self):
        """更新日内追踪"""
        current_value = self.portfolio_manager.portfolio.total_value
        
        if self.daily_low_value is not None:
            self.daily_low_value = min(self.daily_low_value, current_value)
            self.daily_high_value = max(self.daily_high_value, current_value)
    
    def check_level1_individual_stop_loss(self) -> List[RiskAlert]:
        """
        第一级风控：个股止损检查
        
        Returns:
            触发的告警列表
        """
        alerts = []
        
        for symbol, position in self.portfolio_manager.portfolio.positions.items():
            if symbol not in PORTFOLIO_CONFIG:
                continue
            
            config = PORTFOLIO_CONFIG[symbol]
            current_pnl = position.unrealized_pnl_percent
            stop_loss = config.stop_loss
            
            if current_pnl < stop_loss:
                # 确定止损类别
                category = config.category.value
                stop_threshold = self.level1_config.get(category, -0.10)
                
                if current_pnl < stop_threshold:
                    # 超过止损线，生成告警
                    alert_id = f"STOP_{symbol}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
                    
                    # 确定建议操作
                    if abs(current_pnl) > 0.10:
                        action = "清仓：立即全部卖出"
                        risk_level = RiskLevel.CRITICAL
                    else:
                        action = "减仓：减半仓以控制风险"
                        risk_level = RiskLevel.HIGH
                    
                    alert = RiskAlert(
                        alert_id=alert_id,
                        risk_type="INDIVIDUAL_STOP_LOSS",
                        risk_level=risk_level,
                        symbol=symbol,
                        current_value=current_pnl,
                        threshold=stop_loss,
                        message=f"{position.name}({symbol}) 涨跌幅 {current_pnl:.2%} 低于止损线 {stop_loss:.2%}",
                        recommended_action=action,
                        confidence=0.95,
                        timestamp=datetime.now()
                    )
                    
                    alerts.append(alert)
                    self.alerts.append(alert)
        
        return alerts
    
    def check_level2_portfolio_drawdown(self) -> List[RiskAlert]:
        """
        第二级风控：组合回撤控制检查
        
        Returns:
            触发的告警列表
        """
        alerts = []
        
        portfolio_drawdown = self.portfolio_manager.portfolio.total_pnl_percent
        
        # 检查各级阈值
        if portfolio_drawdown < self.level2_config['full_stop']:
            # -15%全部止损
            alert = RiskAlert(
                alert_id=f"PORTFOLIO_FULL_STOP_{datetime.now().strftime('%Y%m%d%H%M%S')}",
                risk_type="PORTFOLIO_DRAWDOWN",
                risk_level=RiskLevel.CRITICAL,
                symbol=None,
                current_value=portfolio_drawdown,
                threshold=self.level2_config['full_stop'],
                message=f"组合回撤 {portfolio_drawdown:.2%} 超过-15%风险红线",
                recommended_action="紧急清仓：立即停止所有交易，将全部持仓转换为现金",
                confidence=0.98,
                timestamp=datetime.now()
            )
            alerts.append(alert)
            self.alerts.append(alert)
            
        elif portfolio_drawdown < self.level2_config['reduce_to_50']:
            # -10%仓位降至50%
            alert = RiskAlert(
                alert_id=f"PORTFOLIO_REDUCE_50_{datetime.now().strftime('%Y%m%d%H%M%S')}",
                risk_type="PORTFOLIO_DRAWDOWN",
                risk_level=RiskLevel.HIGH,
                symbol=None,
                current_value=portfolio_drawdown,
                threshold=self.level2_config['reduce_to_50'],
                message=f"组合回撤 {portfolio_drawdown:.2%} 超过-10%阈值",
                recommended_action="大幅减仓：将权益仓位降至50%，50%转为现金",
                confidence=0.90,
                timestamp=datetime.now()
            )
            alerts.append(alert)
            self.alerts.append(alert)
            
        elif portfolio_drawdown < self.level2_config['reduce_to_70']:
            # -8%仓位降至70%
            alert = RiskAlert(
                alert_id=f"PORTFOLIO_REDUCE_70_{datetime.now().strftime('%Y%m%d%H%M%S')}",
                risk_type="PORTFOLIO_DRAWDOWN",
                risk_level=RiskLevel.HIGH,
                symbol=None,
                current_value=portfolio_drawdown,
                threshold=self.level2_config['reduce_to_70'],
                message=f"组合回撤 {portfolio_drawdown:.2%} 超过-8%阈值",
                recommended_action="适度减仓：将权益仓位降至70%，30%转为现金",
                confidence=0.85,
                timestamp=datetime.now()
            )
            alerts.append(alert)
            self.alerts.append(alert)
            
        elif portfolio_drawdown < self.level2_config['warning']:
            # -5%预警检查
            alert = RiskAlert(
                alert_id=f"PORTFOLIO_WARNING_{datetime.now().strftime('%Y%m%d%H%M%S')}",
                risk_type="PORTFOLIO_DRAWDOWN",
                risk_level=RiskLevel.MEDIUM,
                symbol=None,
                current_value=portfolio_drawdown,
                threshold=self.level2_config['warning'],
                message=f"组合回撤 {portfolio_drawdown:.2%} 超过-5%预警线",
                recommended_action="风险预警：密切监控，检查市场环境，准备应对措施",
                confidence=0.80,
                timestamp=datetime.now()
            )
            alerts.append(alert)
            self.alerts.append(alert)
        
        return alerts
    
    def check_level3_circuit_breaker(self) -> List[RiskAlert]:
        """
        第三级风控：日内断路器检查
        
        Returns:
            触发的告警列表
        """
        alerts = []
        
        if self.daily_start_value is None or self.daily_low_value is None:
            return alerts
        
        current_value = self.portfolio_manager.portfolio.total_value
        daily_return = (current_value - self.daily_start_value) / self.daily_start_value
        
        # 检查日内断路器
        if daily_return < self.level3_config['force_reduce']:
            # 单日-5%强制减仓30%
            alert = RiskAlert(
                alert_id=f"CIRCUIT_BREAKER_FORCE_{datetime.now().strftime('%Y%m%d%H%M%S')}",
                risk_type="CIRCUIT_BREAKER",
                risk_level=RiskLevel.CRITICAL,
                symbol=None,
                current_value=daily_return,
                threshold=self.level3_config['force_reduce'],
                message=f"单日回撤 {daily_return:.2%} 触发断路器阈值",
                recommended_action="强制减仓：立即卖出30%持仓，控制风险暴露",
                confidence=0.95,
                timestamp=datetime.now()
            )
            alerts.append(alert)
            self.alerts.append(alert)
            
        elif daily_return < self.level3_config['stop_buying']:
            # 单日-3%停止买入
            alert = RiskAlert(
                alert_id=f"CIRCUIT_BREAKER_STOP_{datetime.now().strftime('%Y%m%d%H%M%S')}",
                risk_type="CIRCUIT_BREAKER",
                risk_level=RiskLevel.HIGH,
                symbol=None,
                current_value=daily_return,
                threshold=self.level3_config['stop_buying'],
                message=f"单日回撤 {daily_return:.2%} 触发停止买入阈值",
                recommended_action="停止买入：暂停所有买入操作，仅允许卖出",
                confidence=0.90,
                timestamp=datetime.now()
            )
            alerts.append(alert)
            self.alerts.append(alert)
        
        return alerts
    
    def calculate_risk_metrics(self, market_returns: pd.Series = None) -> RiskMetrics:
        """
        计算组合风险指标
        
        Args:
            market_returns: 市场收益率序列（用于计算Beta）
            
        Returns:
            风险指标对象
        """
        portfolio_value = self.portfolio_manager.portfolio.total_value
        portfolio_drawdown = self.portfolio_manager.portfolio.total_pnl_percent
        
        # 简化的风险指标计算（实际应基于历史数据）
        var_95 = -0.05 if abs(portfolio_drawdown) < 0.08 else -0.12
        var_99 = -0.08 if abs(portfolio_drawdown) < 0.10 else -0.18
        
        sharpe_ratio = 0.0
        if portfolio_value > 0:
            # 简化的夏普比率估算
            expected_return = 0.08  # 目标年化8%
            volatility = 0.15       # 目标波动率15%
            risk_free_rate = 0.03   # 无风险利率3%
            sharpe_ratio = (expected_return - risk_free_rate) / volatility
        
        # 确定风险等级
        if portfolio_drawdown < -0.15:
            risk_level = RiskLevel.CRITICAL
        elif portfolio_drawdown < -0.10:
            risk_level = RiskLevel.HIGH
        elif portfolio_drawdown < -0.05:
            risk_level = RiskLevel.MEDIUM
        else:
            risk_level = RiskLevel.LOW
        
        metrics = RiskMetrics(
            portfolio_value=portfolio_value,
            portfolio_drawdown=portfolio_drawdown,
            var_95=var_95,
            var_99=var_99,
            sharpe_ratio=sharpe_ratio,
            max_drawdown=max(-0.15, portfolio_drawdown),  # 使用最大值
            volatility=0.15,  # 简化设定
            beta=1.0,  # 简化设定
            correlation_with_market=0.8,  # 简化设定
            risk_level=risk_level,
            timestamp=datetime.now()
        )
        
        self.risk_history.append(metrics)
        return metrics
    
    def get_active_alerts(self) -> List[RiskAlert]:
        """获取未解决的活动告警"""
        return [alert for alert in self.alerts if not alert.is_resolved]
    
    def resolve_alert(self, alert_id: str):
        """解决告警"""
        for alert in self.alerts:
            if alert.alert_id == alert_id:
                alert.is_resolved = True
                break
    
    def get_risk_summary(self) -> Dict:
        """
        获取风险摘要
        
        Returns:
            风险摘要字典
        """
        active_alerts = self.get_active_alerts()
        
        # 按严重程度统计
        critical_count = sum(1 for a in active_alerts if a.risk_level == RiskLevel.CRITICAL)
        high_count = sum(1 for a in active_alerts if a.risk_level == RiskLevel.HIGH)
        medium_count = sum(1 for a in active_alerts if a.risk_level == RiskLevel.MEDIUM)
        
        # 计算当前风险指标
        current_metrics = self.calculate_risk_metrics()
        
        summary = {
            'risk_level': current_metrics.risk_level.value,
            'portfolio_value': current_metrics.portfolio_value,
            'portfolio_drawdown': f"{current_metrics.portfolio_drawdown:.2%}",
            'var_95': f"{current_metrics.var_95:.2%}",
            'var_99': f"{current_metrics.var_99:.2%}",
            'sharpe_ratio': f"{current_metrics.sharpe_ratio:.2f}",
            'active_alerts_count': len(active_alerts),
            'critical_alerts': critical_count,
            'high_alerts': high_count,
            'medium_alerts': medium_count,
            'recent_alerts': active_alerts[-5:] if active_alerts else [],
            'recommended_actions': [alert.recommended_action for alert in active_alerts if not alert.is_resolved]
        }
        
        return summary
    
    def should_block_trading(self) -> Tuple[bool, str]:
        """
        判断是否应该阻止交易
        
        Returns:
            (是否阻止, 原因)
        """
        # 检查严重级别告警
        critical_alerts = [alert for alert in self.get_active_alerts() 
                          if alert.risk_level == RiskLevel.CRITICAL]
        
        if critical_alerts:
            reasons = [f"严重风险告警: {alert.message}" for alert in critical_alerts]
            return True, "; ".join(reasons)
        
        # 检查组合回撤
        portfolio_drawdown = self.portfolio_manager.portfolio.total_pnl_percent
        if portfolio_drawdown < self.level2_config['full_stop']:
            return True, f"组合回撤 {portfolio_drawdown:.2%} 超过-15%风险红线"
        
        # 检查日内断路器
        if self.daily_start_value is not None:
            current_value = self.portfolio_manager.portfolio.total_value
            daily_return = (current_value - self.daily_start_value) / self.daily_start_value
            
            if daily_return < self.level3_config['force_reduce']:
                return True, f"单日回撤 {daily_return:.2%} 触发断路器，仅允许卖出"
        
        return False, ""
    
    def get_alerts_report(self) -> pd.DataFrame:
        """
        获取告警报告DataFrame
        
        Returns:
            告警报告DataFrame
        """
        data = []
        for alert in self.alerts[-20:]:  # 最近20个告警
            data.append({
                '告警ID': alert.alert_id,
                '风险类型': alert.risk_type,
                '风险等级': alert.risk_level.value,
                '标的': alert.symbol if alert.symbol else '组合',
                '当前值': f"{alert.current_value:.2%}" if isinstance(alert.current_value, float) else alert.current_value,
                '阈值': f"{alert.threshold:.2%}" if isinstance(alert.threshold, float) else alert.threshold,
                '消息': alert.message,
                '建议操作': alert.recommended_action,
                '置信度': f"{alert.confidence:.0%}",
                '时间': alert.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
                '状态': '已解决' if alert.is_resolved else '活跃'
            })
        
        return pd.DataFrame(data)