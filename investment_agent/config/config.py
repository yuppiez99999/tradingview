"""
智能投资代理核心配置
配置文件：20个标的、三级风控、API密钥
"""

from dataclasses import dataclass
from typing import Dict, List
from enum import Enum

class AssetCategory(Enum):
    """资产类别"""
    CORE_ETF = "core_ETF"           # 核心宽基ETF
    TECH_GROWTH = "tech_growth"     # 科技成长个股
    MANUFACTURING = "manufacturing" # 高端制造/基建
    DEFENSIVE = "defensive"         # 防御/红利
    COMMODITY = "commodity"         # 商品/避险
    CASH = "cash"                   # 现金缓冲

@dataclass
class AssetConfig:
    """资产配置"""
    symbol: str              # 代码
    name: str                # 名称
    category: AssetCategory  # 类别
    weight: float            # 权重
    amount: float            # 金额（万）
    stop_loss: float         # 止损线（负数）
    investment_logic: str    # 投资逻辑

# 20个标的配置（总资金300万）
PORTFOLIO_CONFIG = {
    # === 核心宽基ETF (90万, 30%) ===
    '510300': AssetConfig(
        symbol='510300',
        name='沪深300ETF（华泰柏瑞）',
        category=AssetCategory.CORE_ETF,
        weight=0.08,
        amount=24.0,
        stop_loss=-0.08,
        investment_logic='A股核心资产，大盘Beta基石'
    ),
    '510500': AssetConfig(
        symbol='510500',
        name='中证500ETF（南方）',
        category=AssetCategory.CORE_ETF,
        weight=0.06,
        amount=18.0,
        stop_loss=-0.08,
        investment_logic='中盘成长，弹性优于沪深300'
    ),
    '512100': AssetConfig(
        symbol='512100',
        name='中证1000ETF（南方）',
        category=AssetCategory.CORE_ETF,
        weight=0.05,
        amount=15.0,
        stop_loss=-0.10,
        investment_logic='小盘成长，高波动高收益'
    ),
    '588000': AssetConfig(
        symbol='588000',
        name='科创50ETF（华夏）',
        category=AssetCategory.CORE_ETF,
        weight=0.06,
        amount=18.0,
        stop_loss=-0.12,
        investment_logic='科创板核心，硬科技集中地'
    ),
    '159915': AssetConfig(
        symbol='159915',
        name='创业板ETF（易方达）',
        category=AssetCategory.CORE_ETF,
        weight=0.05,
        amount=15.0,
        stop_loss=-0.12,
        investment_logic='创业板龙头，新能源+医药'
    ),

    # === 科技成长个股 (87万, 29%) ===
    '688041': AssetConfig(
        symbol='688041',
        name='海光信息',
        category=AssetCategory.TECH_GROWTH,
        weight=0.04,
        amount=12.0,
        stop_loss=-0.10,
        investment_logic='AI算力国产替代，DCU芯片龙头'
    ),
    '300308': AssetConfig(
        symbol='300308',
        name='中际旭创',
        category=AssetCategory.TECH_GROWTH,
        weight=0.04,
        amount=12.0,
        stop_loss=-0.12,
        investment_logic='光模块全球龙头，800G放量'
    ),
    '300274': AssetConfig(
        symbol='300274',
        name='阳光电源',
        category=AssetCategory.TECH_GROWTH,
        weight=0.05,
        amount=15.0,
        stop_loss=-0.12,
        investment_logic='光伏逆变器全球龙头，储能第二曲线'
    ),
    '002371': AssetConfig(
        symbol='002371',
        name='北方华创',
        category=AssetCategory.TECH_GROWTH,
        weight=0.04,
        amount=12.0,
        stop_loss=-0.12,
        investment_logic='半导体设备龙头，国产替代核心'
    ),
    '688017': AssetConfig(
        symbol='688017',
        name='绿的谐波',
        category=AssetCategory.TECH_GROWTH,
        weight=0.04,
        amount=12.0,
        stop_loss=-0.15,
        investment_logic='谐波减速器龙头，机器人核心部件'
    ),
    '600276': AssetConfig(
        symbol='600276',
        name='恒瑞医药',
        category=AssetCategory.TECH_GROWTH,
        weight=0.04,
        amount=12.0,
        stop_loss=-0.10,
        investment_logic='创新药龙头，研发管线丰富'
    ),
    '603019': AssetConfig(
        symbol='603019',
        name='中科曙光',
        category=AssetCategory.TECH_GROWTH,
        weight=0.04,
        amount=12.0,
        stop_loss=-0.10,
        investment_logic='国产服务器龙头，算力基建受益'
    ),

    # === 高端制造/基建 (60万, 20%) ===
    '600089': AssetConfig(
        symbol='600089',
        name='特变电工',
        category=AssetCategory.MANUFACTURING,
        weight=0.05,
        amount=15.0,
        stop_loss=-0.10,
        investment_logic='电网+多晶硅双龙头，康波周期受益'
    ),
    '600875': AssetConfig(
        symbol='600875',
        name='东方电气',
        category=AssetCategory.MANUFACTURING,
        weight=0.04,
        amount=12.0,
        stop_loss=-0.10,
        investment_logic='核电+风电设备龙头，清洁能源'
    ),
    '000425': AssetConfig(
        symbol='000425',
        name='徐工机械',
        category=AssetCategory.MANUFACTURING,
        weight=0.04,
        amount=12.0,
        stop_loss=-0.10,
        investment_logic='工程机械龙头，一带一路+出海'
    ),
    '600406': AssetConfig(
        symbol='600406',
        name='国电南瑞',
        category=AssetCategory.MANUFACTURING,
        weight=0.04,
        amount=12.0,
        stop_loss=-0.10,
        investment_logic='电网自动化龙头，新型电力系统'
    ),
    '600989': AssetConfig(
        symbol='600989',
        name='宝丰能源',
        category=AssetCategory.MANUFACTURING,
        weight=0.03,
        amount=9.0,
        stop_loss=-0.12,
        investment_logic='煤化工+光伏一体化，成本优势'
    ),

    # === 防御/红利 + 商品避险 + 现金 (63万, 21%) ===
    '601088': AssetConfig(
        symbol='601088',
        name='中国神华',
        category=AssetCategory.DEFENSIVE,
        weight=0.05,
        amount=15.0,
        stop_loss=-0.08,
        investment_logic='煤炭龙头，高分红+现金流稳定'
    ),
    '518880': AssetConfig(
        symbol='518880',
        name='黄金ETF（华安）',
        category=AssetCategory.COMMODITY,
        weight=0.05,
        amount=15.0,
        stop_loss=-0.05,
        investment_logic='抗通胀+危机对冲，降低组合波动'
    ),
    'CASH': AssetConfig(
        symbol='CASH',
        name='现金缓冲',
        category=AssetCategory.CASH,
        weight=0.11,
        amount=33.0,
        stop_loss=0.0,
        investment_logic='再平衡/暴跌抄底/突发事件机动'
    ),
}

# 资金配置
CAPITAL_CONFIG = {
    'total_capital': 3000000,      # 总资金300万
    'stock_capital': 2000000,      # 股票现货200万
    'futures_capital': 1000000,    # 期货期权100万
}

# 三级风控配置
RISK_CONTROL_CONFIG = {
    'level1_individual_stop': {
        'etf': -0.08,      # ETF跌破8%减半仓
        'tech': -0.12,     # 科技股跌破12%清仓
        'manufacturing': -0.10,  # 制造业跌破10%清仓
        'defensive': -0.08,     # 防御股跌破8%减半仓
        'gold': -0.05      # 黄金跌破5%清仓
    },
    'level2_portfolio_drawdown': {
        'warning': -0.05,      # -5%预警
        'reduce_to_70': -0.08, # -8%仓位降至70%
        'reduce_to_50': -0.10, # -10%仓位降至50%
        'full_stop': -0.15     # -15%全部止损
    },
    'level3_circuit_breaker': {
        'stop_buying': -0.03,  # 单日-3%停止买入
        'force_reduce': -0.05  # 单日-5%强制减仓30%
    }
}

# API密钥配置（需要实际部署时填写真实密钥）
API_CONFIG = {
    'wind': {
        'username': '',  # Wind账号
        'password': '',  # Wind密码
        'api_key': '',   # Wind API密钥
    },
    'silicon_flow': {
        'api_key': '',   # 硅基流动API密钥
        'base_url': 'https://api.siliconflow.cn/v1',
        'model_flash': 'deepseek-v4-flash',    # Flash档（主力）
        'model_pro': 'deepseek-v4-pro',        # Pro档（复杂任务）
    },
    'zhipu_ai': {
        'api_key': '',   # 智谱AI API密钥
        'base_url': 'https://open.bigmodel.cn/api/paas/v4',
        'model': 'glm-4-flashx',  # 免费备用模型
    },
    'aliyun': {
        'access_key_id': '',
        'access_key_secret': '',
        'region': 'cn-shanghai',
    }
}

# 性能目标
PERFORMANCE_TARGETS = {
    'annual_return': 0.08,      # 年化收益率≥8%
    'max_drawdown': 0.15,       # 最大回撤≤15%
    'sharpe_ratio': 0.5,        # 夏普比率≥0.5
    'win_rate_monthly': 0.6,    # 月胜率≥60%
    'win_loss_ratio': 1.5,      # 盈亏比≥1.5:1
    'trade_frequency_monthly': (10, 20)  # 月交易频率10-20次
}

# 自动化任务调度配置
AUTOMATION_SCHEDULE = {
    'pre_market_data': {
        'time': '06:30',
        'frequency': 'daily',
        'description': '盘前数据采集'
    },
    'morning_report': {
        'time': '06:40',
        'frequency': 'daily',
        'description': 'AI驱动每日晨报生成'
    },
    'strategy_calculation': {
        'time': '08:30',
        'frequency': 'daily',
        'description': '盘前策略计算与操作建议'
    },
    'intraday_monitor': {
        'time_range': '09:00-15:00',
        'frequency': 'trading_day',
        'description': '盘中实时风控监控'
    },
    'post_market_archive': {
        'time': '15:30',
        'frequency': 'daily',
        'description': '盘后数据持久化与归档'
    },
    'weekly_rebalance': {
        'time': '15:30',
        'frequency': 'weekly_friday',
        'description': '周度组合再平衡检查'
    },
    'monthly_evaluation': {
        'time': 'end_of_month',
        'frequency': 'monthly',
        'description': '月度组合绩效评估报告'
    }
}

# 系统配置
SYSTEM_CONFIG = {
    'timezone': 'Asia/Shanghai',
    'trading_hours': {
        'morning': '09:30-11:30',
        'afternoon': '13:00-15:00'
    },
    'data_backup_enabled': True,
    'alert_system_enabled': True,
    'monitoring_enabled': True
}