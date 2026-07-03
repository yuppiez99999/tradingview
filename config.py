# -*- coding: utf-8 -*-
"""
量化策略系统配置文件

功能：
- 系统全局配置
- 策略参数配置
- 风险管理配置
- 数据源配置
- 执行配置

使用方法：
from config import *

然后在系统中使用：
config = Config.get_instance()
"""

import os
import json
import logging
from datetime import datetime, time
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, asdict
from enum import Enum

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('quant_strategy_system.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('config')


class MarketRegime(Enum):
    """市场状态枚举"""
    NORMAL = "normal"          # 正常市场
    WARNING = "warning"        # 警告市场
    CRISIS = "crisis"          # 危机市场
    VOLATILE = "volatile"      # 高波动市场
    TRENDING = "trending"      # 趋势市场


class RiskLevel(Enum):
    """风险等级枚举"""
    LOW = "low"               # 低风险
    MEDIUM = "medium"         # 中风险
    HIGH = "high"             # 高风险
    CRITICAL = "critical"     # 严重风险


@dataclass
class HedgeConfig:
    """对冲策略配置"""
    # Delta对冲配置
    delta_hedge_ratio: float = 0.7            # Delta对冲比例
    delta_adjust_threshold: float = 0.1       # Delta调整阈值
    delta_hedge_cost: float = 0.02            # Delta对冲成本
    
    # 波动率对冲配置
    vol_hedge_ratio: float = 0.3              # 波动率对冲比例
    vol_threshold_high: float = 0.2           # 高波动率阈值
    vol_threshold_low: float = 0.1            # 低波动率阈值
    vol_hedge_cost: float = 0.015            # 波动率对冲成本
    
    # 尾部风险对冲配置
    tail_hedge_ratio: float = 0.1            # 尾部风险对冲比例
    tail_hedge_trigger: float = 0.05          # 尾部风险触发阈值
    tail_hedge_cost: float = 0.05             # 尾部风险对冲成本
    
    # 对冲资金分配
    delta_capital_ratio: float = 0.6          # Delta对冲资金比例
    vol_capital_ratio: float = 0.3           # 波动率对冲资金比例
    tail_capital_ratio: float = 0.1          # 尾部风险对冲资金比例

    # ---- 紧急响应协议（v2.0 新增） ----
    # 建仓阶段保护性看跌期权
    build_put_budget_ratio: float = 0.03     # 建仓期Put期权预算（占总资金3%）
    build_put_strike_otm: float = 0.10       # Put行权价虚值幅度（低于现价10%）
    build_put_duration_months: int = 3        # Put期权期限（月）

    # 紧急响应阈值
    emergency_yellow_vix: float = 30.0        # 黄色预警VIX阈值
    emergency_orange_vix: float = 35.0        # 橙色预警VIX阈值
    emergency_red_vix: float = 40.0           # 红色预警VIX阈值
    emergency_extreme_vix: float = 50.0       # 极端预警VIX阈值

    emergency_yellow_daily_drop: float = 0.03  # 黄色预警单日跌幅
    emergency_orange_weekly_drop: float = 0.08  # 橙色预警单周跌幅
    emergency_red_biweekly_drop: float = 0.15   # 红色预警双周跌幅
    emergency_extreme_daily_drop: float = 0.07  # 极端预警单日跌幅

    emergency_margin_yellow: float = -0.05     # 两融5日降幅黄色阈值
    emergency_margin_red: float = -0.10        # 两融5日降幅红色阈值

    # 紧急响应资金倍率
    emergency_yellow_capital_mult: float = 0.50  # 黄色：减半建仓
    emergency_orange_capital_mult: float = 0.0   # 橙色：暂停建仓
    emergency_red_capital_mult: float = 0.0      # 红色：暂停建仓
    emergency_extreme_capital_mult: float = 0.0  # 极端：全部停止


@dataclass
class RiskConfig:
    """风险管理配置"""
    # 风险限额
    max_portfolio_var: float = 0.1           # 最大组合VaR
    max_portfolio_drawdown: float = 0.15      # 最大组合回撤
    max_single_position_risk: float = 0.05    # 单个头寸最大风险
    
    # 风险监控频率
    risk_monitor_frequency: int = 300         # 风险监控间隔(秒)
    position_update_frequency: int = 3600      # 头寸更新间隔(秒)
    
    # 预警阈值
    var_warning_threshold: float = 0.08      # VaR预警阈值
    drawdown_warning_threshold: float = 0.10   # 回撤预警阈值
    
    # 压力测试
    stress_test_scenarios: List[Dict] = None   # 压力测试场景

    # ---- 紧急响应协议（v2.0 新增） ----
    # 启用建仓阶段前瞻性压力测试
    build_plan_stress_test_enabled: bool = True  # 建仓阶段启用压力测试
    build_plan_stress_black_swan_max_loss: float = 0.50  # 黑天鹅最大可接受损失
    build_plan_stress_bear_max_loss: float = 0.35       # 熊市最大可接受损失

    # 建仓阶段市场监控
    build_plan_market_monitor_enabled: bool = True  # 启用实时市场监控
    build_plan_market_monitor_interval: int = 300    # 市场监控间隔(秒)
    
    def __post_init__(self):
        if self.stress_test_scenarios is None:
            self.stress_test_scenarios = [
                {"name": "市场崩盘", "market_drop": -0.3, "volatility": 0.4},
                {"name": "流动性危机", "market_drop": -0.2, "volatility": 0.5},
                {"name": "利率冲击", "rate_change": 0.01, "duration": 30}
            ]


@dataclass
class ExecutionConfig:
    """执行配置"""
    # 自动执行时间
    execution_time: str = "07:00"              # 执行时间
    execution_timezone: str = "Asia/Shanghai"  # 时区
    
    # 执行策略
    execution_mode: str = "auto"               # 执行模式：auto/manual/simulation
    execution_retry_count: int = 3            # 执行重试次数
    execution_retry_interval: int = 60        # 执行重试间隔(秒)
    
    # 交易配置
    trade_execution_delay: int = 30           # 交易执行延迟(秒)
    trade_execution_timeout: int = 300         # 交易执行超时(秒)
    trade_execution_max_slippage: float = 0.02  # 最大滑点


@dataclass
class DataConfig:
    """数据配置"""
    # 数据源
    data_source: str = "yahoo"                 # 数据源
    api_key: Optional[str] = None             # API密钥
    data_update_frequency: int = 300          # 数据更新频率(秒)
    
    # 数据缓存
    cache_enabled: bool = True                # 是否启用缓存
    cache_expiry_hours: int = 24               # 缓存过期时间(小时)
    
    # 数据质量检查
    data_quality_check: bool = True            # 数据质量检查
    data_validation_rules: Dict = None        # 数据验证规则
    
    def __post_init__(self):
        if self.data_validation_rules is None:
            self.data_validation_rules = {
                "price_range": {"min": 0, "max": 1000000},
                "volume_range": {"min": 0, "max": 1000000000},
                "return_threshold": 0.5
            }


@dataclass
class PerformanceConfig:
    """性能配置"""
    # 性能目标
    target_annual_return: float = 0.08         # 目标年化收益
    target_max_drawdown: float = 0.15          # 目标最大回撤
    target_sharpe_ratio: float = 1.5           # 目标夏普比率
    
    # 评估指标
    benchmark: str = "SPY"                    # 基准
    evaluation_frequency: int = 7              # 评估频率(天)
    
    # 优化参数
    optimization_frequency: int = 30           # 优化频率(天)
    optimization_timeout: int = 3600           # 优化超时(秒)


@dataclass
class SystemConfig:
    """系统配置"""
    # 基本参数
    total_capital: float = 5000000             # 总资金
    stock_etf_capital: float = 4000000         # 股票ETF资金
    hedge_capital: float = 1000000             # 对冲资金
    
    # 对冲配置
    hedge_config: HedgeConfig = None          # 对冲策略配置
    
    # 风险管理配置
    risk_config: RiskConfig = None            # 风险管理配置
    
    # 执行配置
    execution_config: ExecutionConfig = None    # 执行配置
    
    # 数据配置
    data_config: DataConfig = None             # 数据配置
    
    # 性能配置
    performance_config: PerformanceConfig = None  # 性能配置
    
    def __post_init__(self):
        if self.hedge_config is None:
            self.hedge_config = HedgeConfig()
        if self.risk_config is None:
            self.risk_config = RiskConfig()
        if self.execution_config is None:
            self.execution_config = ExecutionConfig()
        if self.data_config is None:
            self.data_config = DataConfig()
        if self.performance_config is None:
            self.performance_config = PerformanceConfig()


class Config:
    """配置管理器"""
    
    _instance = None
    _config_file = "system_config.json"
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if not hasattr(self, '_initialized'):
            self._initialized = True
            self._config = None
            self._load_config()
    
    def _load_config(self):
        """加载配置"""
        try:
            if os.path.exists(self._config_file):
                with open(self._config_file, 'r', encoding='utf-8') as f:
                    config_data = json.load(f)
                self._config = self._dict_to_config(config_data)
                logger.info("配置文件加载成功")
            else:
                self._config = SystemConfig()
                self._save_config()
                logger.info("创建默认配置文件")
        except Exception as e:
            logger.error(f"加载配置失败: {e}")
            self._config = SystemConfig()
    
    def _dict_to_config(self, config_data: Dict) -> SystemConfig:
        """将字典转换为配置对象"""
        try:
            # 递归转换嵌套字典
            hedge_config = HedgeConfig(**config_data.get('hedge_config', {}))
            risk_config = RiskConfig(**config_data.get('risk_config', {}))
            execution_config = ExecutionConfig(**config_data.get('execution_config', {}))
            data_config = DataConfig(**config_data.get('data_config', {}))
            performance_config = PerformanceConfig(**config_data.get('performance_config', {}))
            
            return SystemConfig(
                total_capital=config_data.get('total_capital', 5000000),
                stock_etf_capital=config_data.get('stock_etf_capital', 4000000),
                hedge_capital=config_data.get('hedge_capital', 1000000),
                hedge_config=hedge_config,
                risk_config=risk_config,
                execution_config=execution_config,
                data_config=data_config,
                performance_config=performance_config
            )
        except Exception as e:
            logger.error(f"配置转换失败: {e}")
            return SystemConfig()
    
    def _save_config(self):
        """保存配置"""
        try:
            config_dict = self._config_to_dict(self._config)
            with open(self._config_file, 'w', encoding='utf-8') as f:
                json.dump(config_dict, f, indent=2, ensure_ascii=False)
            logger.info("配置文件保存成功")
        except Exception as e:
            logger.error(f"保存配置失败: {e}")
    
    def _config_to_dict(self, config: SystemConfig) -> Dict:
        """将配置对象转换为字典"""
        return {
            'total_capital': config.total_capital,
            'stock_etf_capital': config.stock_etf_capital,
            'hedge_capital': config.hedge_capital,
            'hedge_config': asdict(config.hedge_config),
            'risk_config': asdict(config.risk_config),
            'execution_config': asdict(config.execution_config),
            'data_config': asdict(config.data_config),
            'performance_config': asdict(config.performance_config)
        }
    
    def get_instance(self) -> SystemConfig:
        """获取配置实例"""
        return self._config
    
    def update_config(self, updates: Dict):
        """更新配置"""
        try:
            # 更新配置
            for key, value in updates.items():
                if hasattr(self._config, key):
                    setattr(self._config, key, value)
            
            # 保存配置
            self._save_config()
            logger.info("配置更新成功")
            return True
        except Exception as e:
            logger.error(f"配置更新失败: {e}")
            return False
    
    def reset_config(self):
        """重置配置"""
        self._config = SystemConfig()
        self._save_config()
        logger.info("配置重置成功")
    
    def get_config_summary(self) -> Dict:
        """获取配置摘要"""
        return {
            'total_capital': self._config.total_capital,
            'stock_etf_capital': self._config.stock_etf_capital,
            'hedge_capital': self._config.hedge_capital,
            'hedge_allocation': {
                'delta': self._config.hedge_config.delta_capital_ratio,
                'volatility': self._config.hedge_config.vol_capital_ratio,
                'tail_risk': self._config.hedge_config.tail_capital_ratio
            },
            'risk_limits': {
                'max_var': self._config.risk_config.max_portfolio_var,
                'max_drawdown': self._config.risk_config.max_portfolio_drawdown
            },
            'execution_time': self._config.execution_config.execution_time,
            'data_source': self._config.data_config.data_source,
            'performance_targets': {
                'annual_return': self._config.performance_config.target_annual_return,
                'max_drawdown': self._config.performance_config.target_max_drawdown
            }
        }


def create_sample_config():
    """创建示例配置文件"""
    config = Config()
    sample_config = config.get_config_summary()
    
    # 保存示例配置
    with open('sample_config.json', 'w', encoding='utf-8') as f:
        json.dump(sample_config, f, indent=2, ensure_ascii=False)
    
    logger.info("示例配置文件创建成功")
    return sample_config


if __name__ == "__main__":
    # 创建配置管理器
    config = Config()
    
    # 获取配置摘要
    summary = config.get_config_summary()
    print("系统配置摘要:")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    
    # 创建示例配置
    create_sample_config()