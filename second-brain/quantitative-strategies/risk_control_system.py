"""
多层次风险控制系统 (Multi-Level Risk Control System)
实现了完整的金融市场风险管理框架，包含5个不同维度的风险控制模块。

作者: AI Assistant
创建日期: 2026-06-29
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Tuple, Optional, Union
from dataclasses import dataclass
from enum import Enum
import warnings
from sklearn.preprocessing import StandardScaler
from scipy import stats


class RiskLevel(Enum):
    """风险等级枚举"""
    LOW = 0.0          # 低风险 [0.0, 0.3)
    MEDIUM = 0.3       # 中风险 [0.3, 0.6)
    HIGH = 0.6         # 高风险 [0.6, 0.8)
    CRITICAL = 0.8     # 严重风险 [0.8, 1.0]


class RiskType(Enum):
    """风险类型枚举"""
    MARKET = "market"               # 市场风险
    SINGLE_STOCK = "single_stock"   # 单个股票风险
    PORTFOLIO = "portfolio"         # 组合风险
    OPERATIONAL = "operational"     # 操作风险
    EMOTIONAL = "emotional"         # 情绪风险


@dataclass
class RiskAlert:
    """风险告警信息"""
    risk_type: RiskType
    risk_level: RiskLevel
    risk_score: float
    description: str
    timestamp: str
    suggested_action: str
    confidence: float = 0.8


class BaseRiskControl:
    """风险控制基类"""
    
    def __init__(self, name: str, risk_type: RiskType):
        self.name = name
        self.risk_type = risk_type
        self.is_active = True
        self.thresholds = {
            'low': 0.1,
            'medium': 0.25,
            'high': 0.4,
            'critical': 0.6
        }
    
    def calculate_risk_score(self, data: Dict) -> float:
        """计算风险分数，子类需要重写此方法"""
        raise NotImplementedError("子类必须实现calculate_risk_score方法")
    
    def get_risk_level(self, score: float) -> RiskLevel:
        """根据分数获取风险等级"""
        if score < self.thresholds['low']:
            return RiskLevel.LOW
        elif score < self.thresholds['medium']:
            return RiskLevel.MEDIUM
        elif score < self.thresholds['high']:
            return RiskLevel.HIGH
        else:
            return RiskLevel.CRITICAL
    
    def generate_alert(self, data: Dict) -> Optional[RiskAlert]:
        """生成风险告警"""
        if not self.is_active:
            return None
            
        score = self.calculate_risk_score(data)
        level = self.get_risk_level(score)
        
        if level in [RiskLevel.HIGH, RiskLevel.CRITICAL]:
            return RiskAlert(
                risk_type=self.risk_type,
                risk_level=level,
                risk_score=score,
                description=self._generate_description(score, level),
                timestamp=pd.Timestamp.now().isoformat(),
                suggested_action=self._get_suggested_action(level),
                confidence=self._get_confidence_score(score)
            )
        return None
    
    def _generate_description(self, score: float, level: RiskLevel) -> str:
        """生成风险描述，子类需要重写"""
        return f"风险分数: {score:.3f}, 风险等级: {level.value}"
    
    def _get_suggested_action(self, level: RiskLevel) -> str:
        """获取建议行动"""
        actions = {
            RiskLevel.LOW: "持续监控",
            RiskLevel.MEDIUM: "加强观察，准备应对措施",
            RiskLevel.HIGH: "采取风险控制措施，降低仓位",
            RiskLevel.CRITICAL: "立即采取紧急措施，清仓或大幅减仓"
        }
        return actions.get(level, "评估具体情况")
    
    def _get_confidence_score(self, score: float) -> float:
        """获取置信度分数"""
        # 分数越高，置信度越高
        return min(score * 1.2, 1.0)
    
    def update_thresholds(self, thresholds: Dict[str, float]):
        """更新风险阈值"""
        self.thresholds.update(thresholds)


class MarketRiskControl(BaseRiskControl):
    """市场风险控制"""
    
    def __init__(self):
        super().__init__("市场风险控制", RiskType.MARKET)
        self.volatility_window = 20
        self.drawdown_window = 30
        self.max_volatility = 0.25
        self.max_drawdown = 0.20
        self.max_sharpe_ratio = 0.8
        
    def calculate_risk_score(self, data: Dict) -> float:
        """计算市场风险分数"""
        market_data = data.get('market', {})
        
        # 计算市场波动风险
        volatility_score = self._calculate_volatility_risk(market_data)
        
        # 计算最大回撤风险
        drawdown_score = self._calculate_drawdown_risk(market_data)
        
        # 计算系统风险
        systemic_score = self._calculate_systemic_risk(market_data)
        
        # 综合风险分数
        risk_score = max(
            volatility_score * 0.4,
            drawdown_score * 0.3,
            systemic_score * 0.3
        )
        
        return min(risk_score, 1.0)
    
    def _calculate_volatility_risk(self, market_data: Dict) -> float:
        """计算波动率风险"""
        if 'returns' not in market_data or not market_data['returns']:
            return 0.0
        
        returns = np.array(market_data['returns'])
        volatility = np.std(returns) * np.sqrt(252)  # 年化波动率
        
        # 使用更敏感的风险计算
        if volatility < 0.1:
            return volatility * 2  # 低波动时线性增长
        elif volatility < 0.2:
            return 0.2 + (volatility - 0.1) * 3  # 中等波动时加速增长
        else:
            return min(0.5 + (volatility - 0.2) * 5, 1.0)  # 高波动时急剧增长
    
    def _calculate_drawdown_risk(self, market_data: Dict) -> float:
        """计算最大回撤风险"""
        if 'prices' not in market_data or not market_data['prices']:
            return 0.0
        
        prices = np.array(market_data['prices'])
        cumulative_returns = np.cumprod(1 + np.diff(prices, prepend=prices[0])/prices[0])
        drawdown = (np.maximum.accumulate(cumulative_returns) - cumulative_returns) / np.maximum.accumulate(cumulative_returns)
        
        max_drawdown = np.max(drawdown)
        
        # 使用更敏感的风险计算
        if max_drawdown < 0.05:
            return max_drawdown * 10  # 低回撤时线性增长
        elif max_drawdown < 0.15:
            return 0.5 + (max_drawdown - 0.05) * 4  # 中等回撤时加速增长
        else:
            return min(0.9 + (max_drawdown - 0.15) * 2, 1.0)  # 高回撤时急剧增长
    
    def _calculate_systemic_risk(self, market_data: Dict) -> float:
        """计算系统性风险"""
        if 'vix' not in market_data:
            return 0.0
        
        vix = market_data['vix']
        
        # VIX越高，系统性风险越高
        # VIX正常范围：10-20，恐慌时可能超过30
        normalized_vix = min(max(vix - 10, 0) / 30, 1.0)
        
        # 检查相关性指标
        correlation_score = 0.0
        if 'correlation' in market_data:
            # 相关性过高通常表示系统性风险
            correlation = market_data['correlation']
            if correlation > 0.8:
                correlation_score = (correlation - 0.8) / 0.2
        
        # 综合系统性风险
        systemic_risk = max(normalized_vix, correlation_score)
        return systemic_risk
    
    def _generate_description(self, score: float, level: RiskLevel) -> str:
        """生成市场风险描述"""
        if level == RiskLevel.CRITICAL:
            return "市场处于极端波动状态，系统性风险极高，建议暂停交易或大幅降低仓位"
        elif level == RiskLevel.HIGH:
            return "市场波动加剧，系统性风险较高，建议谨慎操作并降低杠杆"
        elif level == RiskLevel.MEDIUM:
            return "市场波动性有所上升，风险中等，建议加强风险监控"
        else:
            return "市场相对平稳，风险较低，可正常交易"


class SingleStockRiskControl(BaseRiskControl):
    """单个股票风险控制"""
    
    def __init__(self):
        super().__init__("单股票风险控制", RiskType.SINGLE_STOCK)
        self.max_position_size = 0.1  # 最大单一股票仓位10%
        self.max_beta = 1.5
        self.max_pe_ratio = 50
        self.max_pb_ratio = 10
        self.min_dividend_yield = 0.02
        self.valuation_threshold = 0.8  # 估值风险阈值
        
    def calculate_risk_score(self, data: Dict) -> float:
        """计算单股票风险分数"""
        stock_data = data.get('stock', {})
        
        # 计算仓位风险
        position_score = self._calculate_position_risk(stock_data)
        
        # 计算Beta风险
        beta_score = self._calculate_beta_risk(stock_data)
        
        # 计算估值风险
        valuation_score = self._calculate_valuation_risk(stock_data)
        
        # 计算流动性风险
        liquidity_score = self._calculate_liquidity_risk(stock_data)
        
        # 综合风险分数
        risk_score = max(
            position_score * 0.3,
            beta_score * 0.25,
            valuation_score * 0.25,
            liquidity_score * 0.2
        )
        
        return min(risk_score, 1.0)
    
    def _calculate_position_risk(self, stock_data: Dict) -> float:
        """计算仓位风险"""
        if 'position_size' not in stock_data:
            return 0.0
        
        position_size = stock_data['position_size']
        
        # 使用更敏感的风险计算
        if position_size < 0.05:
            return 0.0
        elif position_size < 0.1:
            return (position_size - 0.05) * 5  # 低仓位时线性增长
        elif position_size < 0.2:
            return 0.25 + (position_size - 0.1) * 5  # 中等仓位时加速增长
        else:
            return min(0.75 + (position_size - 0.2) * 3, 1.0)  # 高仓位时急剧增长
    
    def _calculate_beta_risk(self, stock_data: Dict) -> float:
        """计算Beta风险"""
        if 'beta' not in stock_data:
            return 0.0
        
        beta = stock_data['beta']
        
        # Beta越高，系统性风险越高
        if beta > self.max_beta:
            return min((beta - self.max_beta) / (3.0 - self.max_beta), 1.0)
        return 0.0
    
    def _calculate_valuation_risk(self, stock_data: Dict) -> float:
        """计算估值风险"""
        valuation_risk = 0.0
        
        # PE比率风险
        if 'pe_ratio' in stock_data:
            pe_ratio = stock_data['pe_ratio']
            if pe_ratio > self.max_pe_ratio:
                valuation_risk += min((pe_ratio - self.max_pe_ratio) / 100, 1.0) * 0.5
        
        # PB比率风险
        if 'pb_ratio' in stock_data:
            pb_ratio = stock_data['pb_ratio']
            if pb_ratio > self.max_pb_ratio:
                valuation_risk += min((pb_ratio - self.max_pb_ratio) / 20, 1.0) * 0.3
        
        # 股息率风险（股息率越低，估值风险越高）
        if 'dividend_yield' in stock_data:
            dividend_yield = stock_data['dividend_yield']
            if dividend_yield < self.min_dividend_yield:
                valuation_risk += min((self.min_dividend_yield - dividend_yield) / self.min_dividend_yield, 1.0) * 0.2
        
        return min(valuation_risk, 1.0)
    
    def _calculate_liquidity_risk(self, stock_data: Dict) -> float:
        """计算流动性风险"""
        if 'volume' not in stock_data or 'market_cap' not in stock_data:
            return 0.0
        
        volume = stock_data['volume']
        market_cap = stock_data['market_cap']
        
        # 换手率 = 成交量 / 流通市值
        turnover_ratio = volume / market_cap
        
        # 换手率越低，流动性风险越高
        if turnover_ratio < 0.01:  # 日换手率低于1%
            return 1.0 - min(turnover_ratio * 100, 1.0)
        return 0.0
    
    def _generate_description(self, score: float, level: RiskLevel) -> str:
        """生成单股票风险描述"""
        if level == RiskLevel.CRITICAL:
            return "单个股票风险极高，建议立即清仓或大幅减仓"
        elif level == RiskLevel.HIGH:
            return "单股票风险较高，建议降低仓位或考虑替代品种"
        elif level == RiskLevel.MEDIUM:
            return "单股票风险中等，建议加强监控和风险控制"
        else:
            return "单股票风险可控，可正常投资"


class PortfolioRiskControl(BaseRiskControl):
    """组合风险控制"""
    
    def __init__(self):
        super().__init__("组合风险控制", RiskType.PORTFOLIO)
        self.max_concentration = 0.3  # 最大单一行业集中度30%
        self.min_diversification = 8   # 最少持有8个不同股票
        self.max_correlation = 0.7     # 最大相关性
        self.max_sector_exposure = 0.4  # 最大单一行业暴露
        
    def calculate_risk_score(self, data: Dict) -> float:
        """计算组合风险分数"""
        portfolio_data = data.get('portfolio', {})
        
        # 计算集中度风险
        concentration_score = self._calculate_concentration_risk(portfolio_data)
        
        # 计算分散度风险
        diversification_score = self._calculate_diversification_risk(portfolio_data)
        
        # 计算相关性风险
        correlation_score = self._calculate_correlation_risk(portfolio_data)
        
        # 计算行业风险
        sector_score = self._calculate_sector_risk(portfolio_data)
        
        # 综合风险分数
        risk_score = max(
            concentration_score * 0.3,
            diversification_score * 0.2,
            correlation_score * 0.25,
            sector_score * 0.25
        )
        
        return min(risk_score, 1.0)
    
    def _calculate_concentration_risk(self, portfolio_data: Dict) -> float:
        """计算集中度风险"""
        if 'positions' not in portfolio_data:
            return 0.0
        
        positions = portfolio_data['positions']
        position_sizes = list(positions.values())
        
        # 计算集中度（HHI指数）
        hhi = sum(size ** 2 for size in position_sizes)
        
        # 使用更敏感的风险计算
        if hhi < 0.15:
            return 0.0
        elif hhi < 0.3:
            return (hhi - 0.15) * 3  # 低集中度时线性增长
        elif hhi < 0.5:
            return 0.45 + (hhi - 0.3) * 4  # 中等集中度时加速增长
        else:
            return min(0.85 + (hhi - 0.5) * 3, 1.0)  # 高集中度时急剧增长
    
    def _calculate_diversification_risk(self, portfolio_data: Dict) -> float:
        """计算分散度风险"""
        if 'positions' not in portfolio_data:
            return 0.0
        
        positions = portfolio_data['positions']
        num_stocks = len(positions)
        
        # 股票数量越少，分散度风险越高
        if num_stocks < self.min_diversification:
            return max(0, (self.min_diversification - num_stocks) / self.min_diversification)
        return 0.0
    
    def _calculate_correlation_risk(self, portfolio_data: Dict) -> float:
        """计算相关性风险"""
        if 'correlation_matrix' not in portfolio_data:
            return 0.0
        
        correlation_matrix = portfolio_data['correlation_matrix']
        
        # 计算平均相关性
        upper_triangle = np.triu(correlation_matrix, k=1)
        avg_correlation = np.mean(upper_triangle[upper_triangle != 0])
        
        # 相关性越高，风险越高
        if avg_correlation > self.max_correlation:
            return (avg_correlation - self.max_correlation) / (1.0 - self.max_correlation)
        return 0.0
    
    def _calculate_sector_risk(self, portfolio_data: Dict) -> float:
        """计算行业风险"""
        if 'sector_allocation' not in portfolio_data:
            return 0.0
        
        sector_allocation = portfolio_data['sector_allocation']
        
        # 计算最大行业暴露
        max_sector_exposure = max(sector_allocation.values())
        
        # 行业暴露越高，风险越高
        if max_sector_exposure > self.max_sector_exposure:
            return (max_sector_exposure - self.max_sector_exposure) / (1.0 - self.max_sector_exposure)
        return 0.0
    
    def _generate_description(self, score: float, level: RiskLevel) -> str:
        """生成组合风险描述"""
        if level == RiskLevel.CRITICAL:
            return "组合风险极高，需要立即重新平衡组合结构"
        elif level == RiskLevel.HIGH:
            return "组合风险较高，建议增加分散度或调整仓位配置"
        elif level == RiskLevel.MEDIUM:
            return "组合风险中等，建议定期监控和调整"
        else:
            return "组合结构合理，风险可控"


class OperationalRiskControl(BaseRiskControl):
    """操作风险控制"""
    
    def __init__(self):
        super().__init__("操作风险控制", RiskType.OPERATIONAL)
        self.max_trades_per_day = 100
        self.max_slippage = 0.02    # 最大滑点2%
        self.max_trade_frequency = 0.8  # 最大交易频率
        self.system_health_threshold = 0.9
        
    def calculate_risk_score(self, data: Dict) -> float:
        """计算操作风险分数"""
        operational_data = data.get('operational', {})
        
        # 计算交易频率风险
        frequency_score = self._calculate_frequency_risk(operational_data)
        
        # 计算滑点风险
        slippage_score = self._calculate_slippage_risk(operational_data)
        
        # 计算系统健康风险
        system_score = self._calculate_system_risk(operational_data)
        
        # 计算执行质量风险
        execution_score = self._calculate_execution_risk(operational_data)
        
        # 综合风险分数
        risk_score = max(
            frequency_score * 0.3,
            slippage_score * 0.25,
            system_score * 0.25,
            execution_score * 0.2
        )
        
        return min(risk_score, 1.0)
    
    def _calculate_frequency_risk(self, operational_data: Dict) -> float:
        """计算交易频率风险"""
        if 'trades_per_day' not in operational_data:
            return 0.0
        
        trades_per_day = operational_data['trades_per_day']
        
        # 交易频率越高，操作风险越高
        if trades_per_day > self.max_trades_per_day:
            return min((trades_per_day - self.max_trades_per_day) / (200 - self.max_trades_per_day), 1.0)
        return 0.0
    
    def _calculate_slippage_risk(self, operational_data: Dict) -> float:
        """计算滑点风险"""
        if 'avg_slippage' not in operational_data:
            return 0.0
        
        avg_slippage = operational_data['avg_slippage']
        
        # 滑点越大，风险越高
        if avg_slippage > self.max_slippage:
            return min((avg_slippage - self.max_slippage) / (0.1 - self.max_slippage), 1.0)
        return 0.0
    
    def _calculate_system_risk(self, operational_data: Dict) -> float:
        """计算系统健康风险"""
        if 'system_health' not in operational_data:
            return 0.0
        
        system_health = operational_data['system_health']
        
        # 系统健康度越低，风险越高
        if system_health < self.system_health_threshold:
            return (self.system_health_threshold - system_health) / self.system_health_threshold
        return 0.0
    
    def _calculate_execution_risk(self, operational_data: Dict) -> float:
        """计算执行质量风险"""
        if 'execution_quality' not in operational_data:
            return 0.0
        
        execution_quality = operational_data['execution_quality']
        
        # 执行质量越低，风险越高
        return 1.0 - execution_quality
    
    def _generate_description(self, score: float, level: RiskLevel) -> str:
        """生成操作风险描述"""
        if level == RiskLevel.CRITICAL:
            return "操作风险极高，建议暂停交易并检查系统状态"
        elif level == RiskLevel.HIGH:
            return "操作风险较高，建议降低交易频率并优化执行策略"
        elif level == RiskLevel.MEDIUM:
            return "操作风险中等，建议定期检查和优化交易流程"
        else:
            return "操作风险可控，交易系统运行正常"


class EmotionalRiskControl(BaseRiskControl):
    """情绪风险控制"""
    
    def __init__(self):
        super().__init__("情绪风险控制", RiskType.EMOTIONAL)
        self.max_fear_greed_index = 80  # 最大贪婪恐惧指数
        self.max_herding_score = 0.7     # 最大跟风指数
        self.max_sentiment_extreme = 0.8 # 最大情绪极端程度
        
    def calculate_risk_score(self, data: Dict) -> float:
        """计算情绪风险分数"""
        emotional_data = data.get('emotional', {})
        
        # 计算贪婪恐惧风险
        fear_greed_score = self._calculate_fear_greed_risk(emotional_data)
        
        # 计算跟风行为风险
        herding_score = self._calculate_herding_risk(emotional_data)
        
        # 计算极端情绪风险
        sentiment_score = self._calculate_sentiment_risk(emotional_data)
        
        # 综合风险分数
        risk_score = max(
            fear_greed_score * 0.4,
            herding_score * 0.35,
            sentiment_score * 0.25
        )
        
        return min(risk_score, 1.0)
    
    def _calculate_fear_greed_risk(self, emotional_data: Dict) -> float:
        """计算贪婪恐惧风险"""
        if 'fear_greed_index' not in emotional_data:
            return 0.0
        
        fear_greed_index = emotional_data['fear_greed_index']
        
        # 恐惧或贪婪情绪越极端，风险越高
        # 贪婪指数 > 80 或 恐惧指数 < 20 都表示极端情绪
        if fear_greed_index > 70 or fear_greed_index < 30:
            deviation = abs(fear_greed_index - 50) / 50  # 偏离50的程度
            return min(deviation * 1.2, 1.0)
        return 0.0
    
    def _calculate_herding_risk(self, emotional_data: Dict) -> float:
        """计算跟风行为风险"""
        if 'herding_score' not in emotional_data:
            return 0.0
        
        herding_score = emotional_data['herding_score']
        
        # 跟风行为越严重，风险越高
        if herding_score > self.max_herding_score:
            return (herding_score - self.max_herding_score) / (1.0 - self.max_herding_score)
        return 0.0
    
    def _calculate_sentiment_risk(self, emotional_data: Dict) -> float:
        """计算极端情绪风险"""
        if 'sentiment_extreme' not in emotional_data:
            return 0.0
        
        sentiment_extreme = emotional_data['sentiment_extreme']
        
        # 情绪越极端，风险越高
        if sentiment_extreme > self.max_sentiment_extreme:
            return (sentiment_extreme - self.max_sentiment_extreme) / (1.0 - self.max_sentiment_extreme)
        return 0.0
    
    def _generate_description(self, score: float, level: RiskLevel) -> str:
        """生成情绪风险描述"""
        if level == RiskLevel.CRITICAL:
            return "市场情绪极度极端，建议暂停交易或采取逆向思维策略"
        elif level == RiskLevel.HIGH:
            return "市场情绪较为极端，建议避免跟风操作，保持理性判断"
        elif level == RiskLevel.MEDIUM:
            return "市场情绪有一定倾向性，建议保持独立思考"
        else:
            return "市场情绪相对平衡，可正常进行理性投资"


class MultiLevelRiskControlSystem:
    """多层次风险控制系统"""
    
    def __init__(self):
        self.risk_controls = {
            RiskType.MARKET: MarketRiskControl(),
            RiskType.SINGLE_STOCK: SingleStockRiskControl(),
            RiskType.PORTFOLIO: PortfolioRiskControl(),
            RiskType.OPERATIONAL: OperationalRiskControl(),
            RiskType.EMOTIONAL: EmotionalRiskControl()
        }
        
        self.alerts = []
        self.risk_history = []
        self.confidence_threshold = 0.7
        
    def calculate_overall_risk(self, data: Dict) -> Tuple[float, Dict[str, float]]:
        """计算整体风险分数"""
        individual_scores = {}
        
        for risk_type, control in self.risk_controls.items():
            if control.is_active:
                score = control.calculate_risk_score(data)
                individual_scores[risk_type.value] = score
        
        # 计算加权综合风险分数
        weights = {
            'market': 0.25,
            'single_stock': 0.2,
            'portfolio': 0.2,
            'operational': 0.15,
            'emotional': 0.2
        }
        
        overall_score = sum(
            individual_scores.get(risk_type, 0.0) * weight
            for risk_type, weight in weights.items()
        )
        
        return overall_score, individual_scores
    
    def generate_all_alerts(self, data: Dict) -> List[RiskAlert]:
        """生成所有风险告警"""
        self.alerts = []
        
        for risk_type, control in self.risk_controls.items():
            alert = control.generate_alert(data)
            if alert and alert.confidence >= self.confidence_threshold:
                self.alerts.append(alert)
        
        return self.alerts
    
    def get_risk_summary(self, data: Dict) -> Dict:
        """获取风险摘要"""
        overall_score, individual_scores = self.calculate_overall_risk(data)
        alerts = self.generate_all_alerts(data)
        
        return {
            'overall_risk_score': overall_score,
            'overall_risk_level': self._get_overall_risk_level(overall_score),
            'individual_risk_scores': individual_scores,
            'active_alerts': len(alerts),
            'alert_count_by_type': self._count_alerts_by_type(alerts),
            'timestamp': pd.Timestamp.now().isoformat()
        }
    
    def _get_overall_risk_level(self, score: float) -> str:
        """获取整体风险等级"""
        if score < 0.3:
            return "LOW"
        elif score < 0.6:
            return "MEDIUM"
        elif score < 0.8:
            return "HIGH"
        else:
            return "CRITICAL"
    
    def _count_alerts_by_type(self, alerts: List[RiskAlert]) -> Dict[str, int]:
        """按类型统计告警数量"""
        alert_counts = {}
        for alert in alerts:
            risk_type = alert.risk_type.value
            alert_counts[risk_type] = alert_counts.get(risk_type, 0) + 1
        return alert_counts
    
    def update_control_thresholds(self, risk_type: RiskType, thresholds: Dict[str, float]):
        """更新特定风险控制器的阈值"""
        if risk_type in self.risk_controls:
            self.risk_controls[risk_type].update_thresholds(thresholds)
    
    def enable_control(self, risk_type: RiskType):
        """启用特定的风险控制器"""
        if risk_type in self.risk_controls:
            self.risk_controls[risk_type].is_active = True
    
    def disable_control(self, risk_type: RiskType):
        """禁用特定的风险控制器"""
        if risk_type in self.risk_controls:
            self.risk_controls[risk_type].is_active = False
    
    def set_confidence_threshold(self, threshold: float):
        """设置置信度阈值"""
        self.confidence_threshold = max(0.0, min(1.0, threshold))
    
    def get_risk_recommendations(self, data: Dict) -> List[str]:
        """获取风险控制建议"""
        alerts = self.generate_all_alerts(data)
        recommendations = []
        
        for alert in alerts:
            if alert.risk_level in [RiskLevel.HIGH, RiskLevel.CRITICAL]:
                recommendations.append(alert.suggested_action)
        
        # 添加基于整体风险的建议
        overall_score, _ = self.calculate_overall_risk(data)
        if overall_score >= 0.8:
            recommendations.append("建议暂停所有交易活动，进行全面风险评估")
        elif overall_score >= 0.6:
            recommendations.append("建议降低交易规模，加强风险监控")
        elif overall_score >= 0.4:
            recommendations.append("建议保持现有策略，但加强风险监控")
        
        return list(set(recommendations))  # 去重
    
    def simulate_risk_scenarios(self, base_data: Dict, scenarios: List[Dict]) -> List[Dict]:
        """模拟不同风险场景"""
        results = []
        
        for i, scenario in enumerate(scenarios):
            # 将场景数据合并到基础数据中
            scenario_data = {**base_data, **scenario}
            
            overall_score, individual_scores = self.calculate_overall_risk(scenario_data)
            alerts = self.generate_all_alerts(scenario_data)
            
            results.append({
                'scenario_name': f"场景{i+1}",
                'overall_risk_score': overall_score,
                'individual_risk_scores': individual_scores,
                'alert_count': len(alerts),
                'risk_level': self._get_overall_risk_level(overall_score),
                'timestamp': pd.Timestamp.now().isoformat()
            })
        
        return results
    
    def get_risk_trend_analysis(self, historical_data: List[Dict]) -> Dict:
        """分析风险趋势"""
        if not historical_data:
            return {'error': '没有历史数据'}
        
        # 计算每个时间点的风险分数
        risk_scores = []
        timestamps = []
        
        for data in historical_data:
            overall_score, _ = self.calculate_overall_risk(data)
            risk_scores.append(overall_score)
            timestamps.append(data.get('timestamp', pd.Timestamp.now().isoformat()))
        
        # 计算趋势指标
        risk_series = pd.Series(risk_scores, index=pd.to_datetime(timestamps))
        
        # 计算移动平均
        moving_avg = risk_series.rolling(window=min(7, len(risk_series))).mean()
        
        # 计算趋势方向
        if len(risk_series) >= 3:
            recent_trend = risk_series.iloc[-3:].mean() - risk_series.iloc[-6:-3].mean() if len(risk_series) >= 6 else risk_series.iloc[-3:].mean() - risk_series.iloc[0].mean()
        else:
            recent_trend = 0.0
        
        # 风险变化率
        if len(risk_scores) > 1:
            change_rate = (risk_scores[-1] - risk_scores[0]) / risk_scores[0]
        else:
            change_rate = 0.0
        
        return {
            'current_risk_score': risk_scores[-1],
            'average_risk_score': risk_series.mean(),
            'risk_volatility': risk_series.std(),
            'recent_trend': recent_trend,
            'change_rate': change_rate,
            'trend_direction': 'upward' if recent_trend > 0.05 else 'downward' if recent_trend < -0.05 else 'stable',
            'risk_level': self._get_overall_risk_level(risk_scores[-1]),
            'peak_risk_score': max(risk_scores),
            'trough_risk_score': min(risk_scores),
            'timestamp': pd.Timestamp.now().isoformat()
        }


def create_test_data() -> Dict:
    """创建测试数据"""
    return {
        'market': {
            'returns': [0.02, -0.01, 0.03, -0.02, 0.01, -0.03, 0.02, 0.01, -0.01, 0.02],
            'prices': [100, 102, 101, 104, 102, 101, 98, 100, 99, 101],
            'vix': 25.0,
            'correlation': 0.75
        },
        'stock': {
            'position_size': 0.15,
            'beta': 1.8,
            'pe_ratio': 60,
            'pb_ratio': 12,
            'dividend_yield': 0.01,
            'volume': 1000000,
            'market_cap': 10000000000
        },
        'portfolio': {
            'positions': {'AAPL': 0.15, 'MSFT': 0.12, 'GOOGL': 0.10, 'AMZN': 0.08, 'TSLA': 0.06},
            'correlation_matrix': np.array([
                [1.0, 0.7, 0.6, 0.8, 0.5],
                [0.7, 1.0, 0.5, 0.6, 0.4],
                [0.6, 0.5, 1.0, 0.7, 0.3],
                [0.8, 0.6, 0.7, 1.0, 0.5],
                [0.5, 0.4, 0.3, 0.5, 1.0]
            ]),
            'sector_allocation': {'Technology': 0.4, 'Healthcare': 0.2, 'Finance': 0.2, 'Energy': 0.2}
        },
        'operational': {
            'trades_per_day': 80,
            'avg_slippage': 0.015,
            'system_health': 0.95,
            'execution_quality': 0.88
        },
        'emotional': {
            'fear_greed_index': 75,
            'herding_score': 0.65,
            'sentiment_extreme': 0.7
        }
    }


def test_risk_control_system():
    """测试风险控制系统"""
    print("开始测试多层次风险控制系统...")
    
    # 创建风险控制系统
    risk_system = MultiLevelRiskControlSystem()
    
    # 创建测试数据
    test_data = create_test_data()
    
    # 测试基本风险计算
    print("\n=== 基本风险计算测试 ===")
    overall_score, individual_scores = risk_system.calculate_overall_risk(test_data)
    print(f"整体风险分数: {overall_score:.3f}")
    print(f"风险等级: {risk_system._get_overall_risk_level(overall_score)}")
    print("各维度风险分数:")
    for risk_type, score in individual_scores.items():
        print(f"  {risk_type}: {score:.3f}")
    
    # 测试风险告警
    print("\n=== 风险告警测试 ===")
    alerts = risk_system.generate_all_alerts(test_data)
    print(f"生成告警数量: {len(alerts)}")
    for alert in alerts:
        print(f"  {alert.risk_type.value}: {alert.risk_level.value} ({alert.risk_score:.3f})")
        print(f"    描述: {alert.description}")
        print(f"    建议: {alert.suggested_action}")
        print(f"    置信度: {alert.confidence:.3f}")
    
    # 测试风险摘要
    print("\n=== 风险摘要测试 ===")
    summary = risk_system.get_risk_summary(test_data)
    print(f"整体风险分数: {summary['overall_risk_score']:.3f}")
    print(f"整体风险等级: {summary['overall_risk_level']}")
    print(f"活跃告警数量: {summary['active_alerts']}")
    print("各维度风险分数:")
    for risk_type, score in summary['individual_risk_scores'].items():
        print(f"  {risk_type}: {score:.3f}")
    
    # 测试风险建议
    print("\n=== 风险建议测试 ===")
    recommendations = risk_system.get_risk_recommendations(test_data)
    print("风险控制建议:")
    for i, rec in enumerate(recommendations, 1):
        print(f"  {i}. {rec}")
    
    # 测试风险场景模拟
    print("\n=== 风险场景模拟测试 ===")
    scenarios = [
        {'market': {'vix': 40.0, 'correlation': 0.9}},
        {'stock': {'position_size': 0.25, 'beta': 2.5}},
        {'portfolio': {'positions': {'AAPL': 0.3, 'MSFT': 0.25}}},
        {'emotional': {'fear_greed_index': 90}}
    ]
    
    simulation_results = risk_system.simulate_risk_scenarios(test_data, scenarios)
    for result in simulation_results:
        print(f"\n场景: {result['scenario_name']}")
        print(f"  整体风险分数: {result['overall_risk_score']:.3f}")
        print(f"  风险等级: {result['risk_level']}")
        print(f"  告警数量: {result['alert_count']}")
    
    # 测试风险趋势分析
    print("\n=== 风险趋势分析测试 ===")
    historical_data = [test_data] * 5
    trend_analysis = risk_system.get_risk_trend_analysis(historical_data)
    print(f"当前风险分数: {trend_analysis['current_risk_score']:.3f}")
    print(f"平均风险分数: {trend_analysis['average_risk_score']:.3f}")
    print(f"风险波动性: {trend_analysis['risk_volatility']:.3f}")
    print(f"趋势方向: {trend_analysis['trend_direction']}")
    print(f"变化率: {trend_analysis['change_rate']:.3f}")
    
    print("\n=== 测试完成 ===")


if __name__ == "__main__":
    test_risk_control_system()