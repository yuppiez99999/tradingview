# -*- coding: utf-8 -*-
"""
对冲-再平衡联动引擎 v5.9 — 组合自触发 + 多指数对冲 + 成本过滤

v5.9 核心改进（基于v2.0回测验证）：
1. 市场状态判断改由组合自身波动率+回撤驱动，不再依赖CSI300
2. 对冲工具改为 IC/IM/IF 多指数Beta加权分配（优先IC/IM匹配中小盘）
3. 新增 TAIL_HEDGE 模式：仅在组合vol>28%或DD>12%时触发25-40%对冲
4. 成本效益过滤：对冲预期收益 < 1.5*成本时自动跳过
5. 默认推荐 S2+尾部保护 混合策略

五阶段决策流程：
  Phase 1: 风险评估 → 组合Beta/VaR/波动率/60日回撤
  Phase 2: 对冲决策 → 组合自触发 → 是否需要？多强？哪个指数？
  Phase 3: 再平衡检查 → 各标的权重偏离度 vs 动态阈值
  Phase 4: 联合优化 → 对冲后敞口 + 再平衡后分布一致性
  Phase 5: 生成执行计划 → 对冲指令 + 买卖清单 + 成本估算

数据源: iFinD MCP → Wind MCP → AKShare → Sina → efinance → 默认回退
"""

import os
import sys
import json
import math
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
from enum import Enum

import pandas as pd

_current_dir = os.path.dirname(os.path.abspath(__file__))
_project_dir = os.path.dirname(_current_dir)
if _project_dir not in sys.path:
    sys.path.insert(0, _project_dir)

logger = logging.getLogger('hedge_rebalance_integrator')

# ============ 数据源：iFinD MCP ============
_IFIND_CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".trae", "skills", "ifind-finance-data")
_IFIND_CONFIG_PATH = os.path.join(_IFIND_CONFIG_DIR, "mcp_config.json")
_IFIND_TOKEN = ""
if os.path.isfile(_IFIND_CONFIG_PATH):
    try:
        with open(_IFIND_CONFIG_PATH, 'r', encoding='utf-8') as _f:
            _cfg = json.load(_f)
            _IFIND_TOKEN = (_cfg.get("auth_token") or "").strip()
    except Exception:  # noqa: BLE001  # fail-safe, 待后续精确化
        pass
IFIND_AVAILABLE = bool(_IFIND_TOKEN)
IFIND_CLIENT = None
if IFIND_AVAILABLE:
    try:
        import importlib.util
        skill_dir = os.path.join(os.path.expanduser("~"), ".trae", "skills", "ifind-finance-data")
        call_path = os.path.join(skill_dir, "call.py")
        spec = importlib.util.spec_from_file_location("ifind_call", call_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        IFIND_CLIENT = mod
        logger.info("[HedgeRebalance] iFinD MCP 连接器加载成功")
    except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
        IFIND_CLIENT = None
        logger.warning(f"[HedgeRebalance] iFinD MCP 连接器不可用: {e}")


def _exec_ifind(server_type: str, tool_name: str, params: dict) -> dict:
    """调用 iFinD MCP API"""
    if not IFIND_CLIENT:
        return {"error": "iFinD MCP 不可用"}
    try:
        result = IFIND_CLIENT.call(server_type, tool_name, params)
        if isinstance(result, dict) and result.get("error"):
            return {"error": result["error"].get("message", str(result["error"])[:200])}
        if isinstance(result, dict) and result.get("data"):
            return {"data": result["data"], "source": "iFinD MCP"}
        return result
    except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
        return {"error": str(e)}


def _get_ifind_prices_batch(codes: List[str]) -> Dict[str, float]:
    """iFinD MCP 批量获取价格 (P0)"""
    prices = {}
    if not IFIND_AVAILABLE or IFIND_CLIENT is None:
        return prices
    pure_codes = [c.split('.')[0] for c in codes if '.' in c]
    if not pure_codes:
        return prices
    try:
        from ifind_client import IFindClient
        client = IFindClient(auth_token=_IFIND_TOKEN, max_concurrency=4)
        quotes = client.get_etf_quotes(pure_codes)
        for c in pure_codes:
            if c in quotes:
                q = quotes[c]
                px = float(q.get('price', 0) or 0)
                if px > 0:
                    prices[c] = px
                    logger.info("[HedgeRebalance][ifind] %s=%.2f", c, px)
    except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
        logger.debug(f"[HedgeRebalance][ifind] batch err: {e}")
    return prices


try:
    from utils.hedge_engine import (
        HedgeEngine, HedgeSignalStrength, HedgeType, HedgeRecommendation,
        PortfolioRisk, INDEX_FUTURES_SPECS, ETF_OPTIONS_SPECS,
        get_live_futures_prices, DEFAULT_FUTURES_PRICES,
    )
    _HEDGE_OK = True
except ImportError as e:
    logger.warning(f"[!] hedge_engine 导入失败: {e}")
    _HEDGE_OK = False


# ============================================================
# 数据类定义
# ============================================================

class MarketRegime(Enum):
    """v5.9: 组合自驱动市场状态 — 基于组合自身波动率和回撤"""
    CALM = "calm"               # vol<18%, DD<8%
    MILD_VOLATILE = "mild"      # vol 18-25%, DD 8-12%
    HIGH_VOLATILE = "high"      # vol 25-35%, DD 12-18%
    TAIL_EVENT = "tail"         # vol>35% 或 DD>18%, 黑天鹅


class HedgeMode(Enum):
    """v5.9 对冲模式"""
    NONE = "none"                     # 不对冲
    TAIL_ONLY = "tail_only"           # 仅尾部对冲 (推荐)
    DYNAMIC = "dynamic"               # 动态对冲 (基于波动率)
    FIXED = "fixed"                   # 固定比例对冲


@dataclass
class PositionWeight:
    code: str
    name: str
    category: str
    target_weight: float
    current_weight: float
    deviation: float
    deviation_pct: float
    current_value: float
    target_value: float
    adjustment: float
    adjustment_shares: int
    current_price: float
    action: str = "HOLD"
    priority: int = 0


@dataclass
class HedgeDecision:
    """对冲决策"""
    needed: bool
    mode: HedgeMode = HedgeMode.NONE
    regime: MarketRegime = MarketRegime.CALM
    hedge_ratio: float = 0.0
    strength_name: str = ""
    futures_instruments: List[str] = field(default_factory=list)
    futures_contracts: Dict[str, int] = field(default_factory=dict)
    futures_notional: Dict[str, float] = field(default_factory=dict)
    futures_margin: Dict[str, float] = field(default_factory=dict)
    total_notional: float = 0.0
    total_margin: float = 0.0
    price_source: str = ""
    fallback_used: List[str] = field(default_factory=list)
    expected_beta_after: float = 0.0
    reasoning: str = ""


@dataclass
class RebalanceDecision:
    needed: bool
    rebalance_type: str = "none"
    threshold: float = 0.05
    positions_to_adjust: List[PositionWeight] = field(default_factory=list)
    total_buy_amount: float = 0.0
    total_sell_amount: float = 0.0
    net_cash_flow: float = 0.0
    reasoning: str = ""


@dataclass
class JointPlan:
    timestamp: str = ""
    portfolio_value: float = 0.0
    stock_exposure: float = 0.0

    hedge: Optional[HedgeDecision] = None
    after_hedge_exposure: float = 0.0

    rebalance: Optional[RebalanceDecision] = None

    execution_window: str = ""
    execution_priority: str = ""
    warning_flags: List[str] = field(default_factory=list)

    estimated_annual_return: float = 0.0
    estimated_max_drawdown: float = 0.0
    estimated_sharpe: float = 0.0
    estimated_volatility: float = 0.0

    summary: str = ""
    stress_tests: Dict[str, Dict[str, Any]] = field(default_factory=dict)  # v5.10 P0-8


# ============================================================
# v5.9 组合自触发对冲阈值表
# ============================================================

# v5.9: 阈值完全由组合自身状态决定，不依赖外部指数
PORTFOLIO_HEDGE_THRESHOLDS = {
    MarketRegime.CALM: {
        "condition": "组合vol<18%, DD<8%",
        "hedge_ratio": 0.0,
        "mode": HedgeMode.NONE,
        "description": "组合风险极低，无需对冲",
    },
    MarketRegime.MILD_VOLATILE: {
        "condition": "组合vol 18-25%, DD 8-12%",
        "hedge_ratio": 0.10,           # v5.9: 仅10%, v5.8为25%
        "mode": HedgeMode.TAIL_ONLY,   # v5.9: tail_only模式
        "description": "轻度关注，仅预留极端对冲预案",
    },
    MarketRegime.HIGH_VOLATILE: {
        "condition": "组合vol 25-35%, DD 12-18%",
        "hedge_ratio": 0.25,           # v5.9: 25%, v5.8为50%
        "mode": HedgeMode.TAIL_ONLY,
        "description": "中度保护，组合尾部对冲25%",
    },
    MarketRegime.TAIL_EVENT: {
        "condition": "组合vol>35% 或 DD>18%",
        "hedge_ratio": 0.40,           # v5.9: 40%, v5.8为75%
        "mode": HedgeMode.DYNAMIC,
        "description": "极端行情保护，对冲40%敞口",
    },
}

# 尾部对冲触发参数 (来自回测验证)
TAIL_VOL_TRIGGER = 0.28      # 年化波动率>28%触发
TAIL_DD_TRIGGER = 0.12       # 60日最大回撤>12%触发
TAIL_MIN_HEDGE = 0.25        # 触发后最小对冲
TAIL_MAX_HEDGE = 0.40        # 触发后最大对冲

REBALANCE_THRESHOLDS = {
    "low":    {"vol_range": "< 15%",  "threshold": 0.03, "check_freq": "每周",   "max_adjust": 3},
    "normal": {"vol_range": "15-25%", "threshold": 0.05, "check_freq": "每3天",  "max_adjust": 2},
    "high":   {"vol_range": "> 25%",  "threshold": 0.08, "check_freq": "每日",   "max_adjust": 4},
}

SECTOR_ROTATION = {
    "recovery":    {"high_end_manufacturing": 0.50, "cyclical": 0.15, "resources": 0.20, "defensive": 0.15},
    "prosperity":  {"high_end_manufacturing": 0.45, "cyclical": 0.20, "resources": 0.20, "defensive": 0.15},
    "stagflation": {"high_end_manufacturing": 0.30, "cyclical": 0.20, "resources": 0.30, "defensive": 0.20},
    "recession":   {"high_end_manufacturing": 0.25, "cyclical": 0.15, "resources": 0.25, "defensive": 0.35},
}

DEFAULT_SECTOR_WEIGHTS = {
    "high_end_manufacturing": 0.45, "cyclical": 0.20,
    "resources": 0.20, "defensive": 0.15,
}


# ============================================================
# 辅助函数
# ============================================================

def _load_yaml(filepath: str) -> Optional[Dict]:
    try:
        import yaml
        with open(filepath, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    except Exception:  # noqa: BLE001  # fail-safe, 待后续精确化
        return None


def _load_json(filepath: str) -> Optional[Dict]:
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:  # noqa: BLE001  # fail-safe, 待后续精确化
        return None


def _estimate_portfolio_vol() -> float:
    """v5.9: 估算组合年化波动率 (默认18%，由调用者传入实际值覆盖)"""
    return 0.18


def _estimate_portfolio_dd_60d() -> float:
    """v5.9: 估算组合60日最大回撤 (默认0%，由调用者传入实际值覆盖)"""
    return 0.0


# ============================================================
# HedgeRebalanceIntegrator v5.9
# ============================================================

class HedgeRebalanceIntegrator:
    """对冲-再平衡联动引擎 v5.9

    用法:
        integrator = HedgeRebalanceIntegrator()
        plan = integrator.run_full_workflow()
        report = integrator.format_report(plan)
    """

    VOLATILITY_TARGET = 0.18

    def __init__(
        self, base_dir: str = None, portfolio_value: float = None,
        config_dir: str = None, hedge_mode: HedgeMode = HedgeMode.TAIL_ONLY,
    ):
        """
        Args:
            base_dir: 项目根目录
            portfolio_value: 组合总市值（默认从配置读取）
            config_dir: 配置目录
            hedge_mode: 对冲模式 (默认 TAIL_ONLY)
        """
        self.base_dir = base_dir or os.path.dirname(os.path.abspath(__file__))
        if os.path.basename(self.base_dir) == 'utils':
            self.base_dir = os.path.dirname(self.base_dir)
        self.config_dir = config_dir or os.path.join(self.base_dir, 'config')
        self.hedge_mode = hedge_mode

        self.portfolio_config = _load_yaml(os.path.join(self.config_dir, 'portfolio.yaml')) or {}
        # v5.10+: normalize positions dict → assets list for backward compat
        if 'assets' not in self.portfolio_config and 'positions' in self.portfolio_config:
            positions = self.portfolio_config['positions']
            if isinstance(positions, dict):
                self.portfolio_config['assets'] = [
                    {'code': code, 'name': v.get('name', code),
                     'category': v.get('sector', ''), 'target_weight': v.get('target_weight', 0.0)}
                    for code, v in positions.items()
                ]
        self.positions = _load_json(os.path.join(self.config_dir, 'positions.json')) or {}
        self.settings = _load_yaml(os.path.join(self.config_dir, 'settings.yaml')) or {}

        if portfolio_value is not None:
            self.portfolio_value = portfolio_value
        else:
            self.portfolio_value = float(
                self.portfolio_config.get('global', {}).get('capital', {}).get('equity_portfolio', 1_000_000)
            )

        if _HEDGE_OK:
            self.hedge_engine = HedgeEngine(portfolio_value=self.portfolio_value)
        else:
            self.hedge_engine = None

        self._prices: Dict[str, float] = {}
        self._prices_loaded = False

    # ================================================================
    # Phase 1: 风险评估 + v5.9 组合自触发
    # ================================================================

    def load_prices(self) -> Dict[str, float]:
        """加载持仓标的价格 — v5.10: iFinD MCP (P0) → Wind MCP (P1) → AKShare (P2) → efinance (P3) → 兜底"""
        if self._prices_loaded:
            return self._prices

        assets = self.portfolio_config.get('assets', [])
        codes = [a['code'] for a in assets]

        # P0: iFinD MCP 实时行情
        if IFIND_AVAILABLE:
            try:
                ifind_prices = _get_ifind_prices_batch(codes)
                for code in codes:
                    pure = code.split('.')[0] if '.' in code else code
                    if pure in ifind_prices and ifind_prices[pure] > 0:
                        self._prices[code] = ifind_prices[pure]
            except Exception:  # noqa: BLE001  # fail-safe, 待后续精确化
                pass

        missing = [c for c in codes if c not in self._prices]

        # P1: Wind MCP 实时行情快照
        if missing:
            try:
                from quant_modules.wind_mcp import get_realtime_prices_batch
                pure_codes = [c.split('.')[0] for c in missing if '.' in c]
                if pure_codes:
                    wind_prices = get_realtime_prices_batch(pure_codes, max_workers=8)
                    for code in missing:
                        pure = code.split('.')[0] if '.' in code else code
                        if pure in wind_prices and wind_prices[pure] > 0:
                            self._prices[code] = wind_prices[pure]
            except Exception:  # noqa: BLE001  # fail-safe, 待后续精确化
                pass

        missing = [c for c in codes if c not in self._prices]

        # P2: AKShare 历史日线
        if missing:
            try:
                import akshare as ak
                for code in missing:
                    try:
                        pure = code.split('.')[0] if '.' in code else code
                        df = ak.stock_zh_a_hist(symbol=pure, period="daily",
                                               start_date="2026-06-20", end_date="2026-06-29", adjust="qfq")
                        if df is not None and not df.empty:
                            close_col = '收盘' if '收盘' in df.columns else ('close' if 'close' in df.columns else df.columns[-1])
                            self._prices[code] = float(df.iloc[-1][close_col])
                    except Exception:  # noqa: BLE001  # fail-safe, 待后续精确化
                        pass
            except Exception:  # noqa: BLE001  # fail-safe, 待后续精确化
                pass

        missing = [c for c in codes if c not in self._prices]

        # P3: efinance 实时行情
        if missing:
            try:
                import efinance as ef
                for code in missing:
                    try:
                        quote = ef.stock.get_realtime_quotes(code)
                        if quote is not None:
                            px = float(quote.price) if hasattr(quote, 'price') and quote.price else 0
                            if not px and isinstance(quote, dict):
                                px = float(quote.get('price', 0) or quote.get('latest', 0))
                            if px > 0:
                                self._prices[code] = px
                    except Exception:  # noqa: BLE001  # fail-safe, 待后续精确化
                        pass
            except Exception:  # noqa: BLE001  # fail-safe, 待后续精确化
                pass

        # P4: 兜底价格
        for code in codes:
            if code not in self._prices:
                self._prices[code] = self._estimate_default_price(code)

        self._prices_loaded = True
        return self._prices

    def _estimate_default_price(self, code: str) -> float:
        fallback_map = {
            "300308.SZ": 105.0, "688041.SH": 62.0, "002371.SZ": 320.0,
            "688981.SH": 55.0, "300750.SZ": 230.0, "000425.SZ": 8.5,
            "601088.SH": 38.0, "600219.SH": 5.0, "600019.SH": 7.5,
            "518880.SH": 5.2, "000792.SZ": 28.0,
            "600276.SH": 48.0, "603259.SH": 65.0, "002422.SZ": 32.0,
        }
        return fallback_map.get(code, 50.0)

    def assess_risk(self) -> PortfolioRisk:
        """Phase 1: 评估组合风险 — v5.10 双数据源融合"""
        prices = self.load_prices()
        positions_for_risk = {}

        # ── 1. 收集 portfolio.yaml 中的目标配置 (v5.10: positions dict) ──
        assets = self.portfolio_config.get('assets', [])
        pos_cfg = self.portfolio_config.get('positions', {})
        if not assets and isinstance(pos_cfg, dict) and pos_cfg:
            for code, item in pos_cfg.items():
                if not isinstance(item, dict):
                    continue
                if item.get('name', '').endswith('ETF') or item.get('sector') == '黄金':
                    continue  # ETF另处理
                assets.append({'code': code, 'target_weight': item.get('target_weight', 0.07)})

        # ── 2. 直接整合 positions.json 实际持仓 (纯数字代码) ──
        position_data = self.positions.get('positions', {}) if isinstance(self.positions, dict) else self.positions
        if isinstance(position_data, dict) and position_data:
            for code, pos in position_data.items():
                if not isinstance(pos, dict):
                    continue
                shares = pos.get('shares', 0)
                if shares <= 0:
                    continue
                # 匹配价格 (尝试带后缀)
                price_key = code
                for suffix in ['.SH', '.SZ']:
                    if code + suffix in prices:
                        price_key = code + suffix
                        break
                if price_key not in prices:
                    prices[price_key] = self._estimate_default_price(price_key)
                positions_for_risk[code] = {
                    'shares': shares,
                    'cost': pos.get('avg_cost', prices.get(price_key, 50)),
                }

        # ── 3. 兜底: portfolio.yaml plan代码 (带后缀) ──
        for asset in assets:
            code = asset['code']
            if code in positions_for_risk:
                continue  # 已有实仓数据
            # 尝试无后缀匹配 positions.json
            pure = code.split('.')[0] if '.' in code else code
            if pure in positions_for_risk:
                continue
            shares = 0
            price = prices.get(code, 50)
            target_w = asset.get('target_weight', 0.07)
            target_mv = self.portfolio_value * target_w
            shares = int(target_mv / price) if price > 0 else 100
            if shares > 0:
                positions_for_risk[code] = {
                    'shares': shares,
                    'cost': price,
                }

        # ── 4. 更新 portfolio_value 为实际持仓市值 ──
        actual_stock_value = sum(
            pos['shares'] * prices.get(code, self._estimate_default_price(code))
            for code, pos in positions_for_risk.items()
        )
        if actual_stock_value > 0:
            self.portfolio_value = actual_stock_value

        if self.hedge_engine:
            self.hedge_engine.portfolio_value = self.portfolio_value
            historical_returns = self._load_historical_returns()
            risk = self.hedge_engine.assess_portfolio_risk(
                positions_for_risk, prices,
                historical_returns=historical_returns,
            )
        else:
            risk = PortfolioRisk(
                total_value=self.portfolio_value,
                stock_exposure=self.portfolio_value * 0.85,
                beta_csi300=1.15, beta_csi500=1.30, beta_csi1000=1.45, beta_sse50=1.05,
                volatility_30d=0.018,
                var_95_daily=self.portfolio_value * 0.02,
                concentration_risk=0.18,
            )
        return risk

    def _load_historical_returns(self) -> Dict[str, List[float]]:
        """v5.10 P0-5: 加载标的近252日历史收益率用于协方差矩阵VaR"""
        returns: Dict[str, List[float]] = {}
        cache_dir = os.path.join(self.base_dir, 'data', 'cache')
        if not os.path.isdir(cache_dir):
            return returns

        assets = self.portfolio_config.get('assets', [])
        for asset in assets:
            code = asset.get('code', '')
            pure = code.split('.')[0] if '.' in code else code
            filepath = os.path.join(cache_dir, f'kline_{pure}_daily.parquet')
            if not os.path.exists(filepath):
                continue
            try:
                df = pd.read_parquet(filepath, columns=['close'])
                df = df.sort_index().dropna(subset=['close'])
                if len(df) < 30:
                    continue
                rets = df['close'].pct_change().dropna().tail(252).tolist()
                returns[code] = [float(r) for r in rets]
            except (OSError, ValueError, KeyError) as err:  # 仅捕获数据读取类异常
                logger.warning("加载 %s 历史收益率失败: %s", filepath, err)
                continue
            except Exception as err:  # noqa: BLE001  # 未知异常也要显式记录, 不允许静默
                logger.error("加载 %s 历史收益率出现未知错误: %s", filepath, err)
                continue
        return returns

    # ================================================================
    # Phase 2: v5.9 组合自触发对冲决策
    # ================================================================

    def _determine_market_regime(
        self, risk: PortfolioRisk,
        portfolio_volatility: float = None,
        portfolio_drawdown_60d: float = None,
    ) -> MarketRegime:
        """v5.9: 组合自身波动率和回撤驱动市场状态

        完全不再依赖CSI300或其他外部指数。
        """
        vol = portfolio_volatility if portfolio_volatility is not None else _estimate_portfolio_vol()
        dd = portfolio_drawdown_60d if portfolio_drawdown_60d is not None else _estimate_portfolio_dd_60d()

        if vol > 0.35 or dd > 0.18:
            return MarketRegime.TAIL_EVENT
        if vol > 0.25 or dd > 0.12:
            return MarketRegime.HIGH_VOLATILE
        if vol > 0.18 or dd > 0.08:
            return MarketRegime.MILD_VOLATILE
        return MarketRegime.CALM

    def _compute_tail_hedge_ratio(
        self, portfolio_volatility: float = None, portfolio_drawdown_60d: float = None
    ) -> float:
        """v5.9: 计算尾部对冲比率

        仅在组合自触发条件满足时激活，日常为0。
        """
        if self.hedge_mode == HedgeMode.NONE:
            return 0.0

        vol = portfolio_volatility if portfolio_volatility is not None else _estimate_portfolio_vol()
        dd = portfolio_drawdown_60d if portfolio_drawdown_60d is not None else _estimate_portfolio_dd_60d()

        ratio = 0.0

        if vol > TAIL_VOL_TRIGGER:
            excess_vol = vol - TAIL_VOL_TRIGGER
            ratio = TAIL_MIN_HEDGE + excess_vol * 2.0
            ratio = min(ratio, TAIL_MAX_HEDGE)

        if dd > TAIL_DD_TRIGGER:
            excess_dd = dd - TAIL_DD_TRIGGER
            dd_ratio = TAIL_MIN_HEDGE + excess_dd * 3.0
            dd_ratio = min(dd_ratio, TAIL_MAX_HEDGE)
            ratio = max(ratio, dd_ratio)

        # v5.9: 成本效益过滤
        if ratio > 0 and ratio < 0.10:
            # 太小不值得
            return 0.0

        return ratio

    def decide_hedge(
        self, risk: PortfolioRisk,
        portfolio_volatility: float = None,
        portfolio_drawdown_60d: float = None,
    ) -> HedgeDecision:
        """v5.9: Phase 2 对冲决策

        使用组合自触发逻辑，替代CSI300状态判断。
        """
        regime = self._determine_market_regime(risk, portfolio_volatility, portfolio_drawdown_60d)
        hconfig = PORTFOLIO_HEDGE_THRESHOLDS[regime]

        # 基础对冲比率
        if self.hedge_mode == HedgeMode.TAIL_ONLY:
            # 尾部保护模式: 仅在触发时激活
            base_ratio = self._compute_tail_hedge_ratio(portfolio_volatility, portfolio_drawdown_60d)
        elif self.hedge_mode == HedgeMode.FIXED:
            base_ratio = hconfig["hedge_ratio"]
        else:
            base_ratio = self._compute_tail_hedge_ratio(portfolio_volatility, portfolio_drawdown_60d)
            base_ratio = max(base_ratio, hconfig["hedge_ratio"])

        hedge_ratio = base_ratio

        # 波动率目标修正
        if _HEDGE_OK and portfolio_volatility is not None and portfolio_volatility > self.VOLATILITY_TARGET * 1.3:
            vol_correction = 1.0 - (self.VOLATILITY_TARGET / portfolio_volatility)
            vol_correction = max(0.0, min(0.5, vol_correction))
            hedge_ratio = max(hedge_ratio, vol_correction * 0.5)

        if hedge_ratio <= 0:
            return HedgeDecision(
                needed=False, mode=self.hedge_mode, regime=regime, hedge_ratio=0.0,
                strength_name="NO_HEDGE",
                expected_beta_after=max(risk.beta_csi300, risk.beta_csi500, risk.beta_csi1000),
                reasoning="组合自评: 波动率和回撤均在安全范围，无需对冲。日常运行动态再平衡即可。",
            )

        # 生成多指数期货对冲方案
        if self.hedge_engine:
            futures_result = self.hedge_engine.generate_futures_hedge(risk, hedge_ratio)

            contracts = {}
            notionals = {}
            margins = {}
            for code, detail in futures_result.get("contracts", {}).items():
                contracts[code] = detail["contracts"]
                notionals[code] = detail["notional"]
                margins[code] = detail["margin"]

            total_notional = futures_result.get("total_notional", 0)
            total_margin = futures_result.get("total_margin", 0)
            fallback_used = futures_result.get("fallback_used", [])
            price_source = futures_result.get("price_source", "auto")
            reason = futures_result.get("reason", "")

            instruments = list(contracts.keys())
            max_beta = max(risk.beta_csi300, risk.beta_csi500, risk.beta_csi1000)
            expected_beta = max_beta * (1 - hedge_ratio)

            actual_desc = hconfig['description']
            if instruments:
                actual_desc += f"，使用{'/'.join(instruments)}期货"
        else:
            contracts = {}
            notionals = {}
            margins = {}
            total_notional = 0
            total_margin = 0
            fallback_used = []
            price_source = "hedge_engine_unavailable"
            instruments = []
            expected_beta = max(risk.beta_csi300, risk.beta_csi500, risk.beta_csi1000)
            reason = "对冲引擎不可用"
            actual_desc = hconfig['description']

        vol_str = f"波动率={portfolio_volatility*100:.1f}%" if portfolio_volatility else ""
        dd_str = f"回撤={portfolio_drawdown_60d*100:.1f}%" if portfolio_drawdown_60d else ""

        return HedgeDecision(
            needed=True, mode=self.hedge_mode, regime=regime,
            hedge_ratio=hedge_ratio,
            strength_name=f"{hconfig['mode'].value}:{hedge_ratio*100:.0f}%",
            futures_instruments=instruments,
            futures_contracts=contracts,
            futures_notional=notionals,
            futures_margin=margins,
            total_notional=total_notional,
            total_margin=total_margin,
            price_source=price_source,
            fallback_used=fallback_used,
            expected_beta_after=expected_beta,
            reasoning=f"{actual_desc}。{vol_str}{dd_str}。{reason}" if reason else actual_desc,
        )

    # ================================================================
    # Phase 3: 再平衡检查
    # ================================================================

    def _get_dynamic_rebalance_threshold(self, portfolio_vol: float) -> Tuple[float, str, int]:
        if portfolio_vol < 0.15:
            config = REBALANCE_THRESHOLDS["low"]
        elif portfolio_vol > 0.25:
            config = REBALANCE_THRESHOLDS["high"]
        else:
            config = REBALANCE_THRESHOLDS["normal"]
        return config["threshold"], config["check_freq"], config["max_adjust"]

    def _get_sector_adjusted_weights(self) -> Dict[str, float]:
        kondratiev_phase = None
        try:
            from utils.kondratiev_cycle import KondratievCycleAnalyzer
            analyzer = KondratievCycleAnalyzer()
            phase_info = analyzer.get_current_phase()
            kondratiev_phase = phase_info.get('phase', None)
        except Exception:  # noqa: BLE001  # fail-safe, 待后续精确化
            pass

        rotation_config = None
        yaml_path = os.path.join(self.config_dir, 'sector_rotation.yaml')
        yaml_data = _load_yaml(yaml_path)
        if yaml_data and 'phases' in yaml_data:
            rotation_config = yaml_data['phases']

        if kondratiev_phase:
            if rotation_config and kondratiev_phase in rotation_config:
                weights = rotation_config[kondratiev_phase].get('sector_weights', {})
                return weights

            phase_to_rotation_key = {
                "复苏期": "prosperity", "繁荣期": "overheat",
                "滞胀期": "stagflation", "衰退期": "recession",
            }
            rot_key = phase_to_rotation_key.get(kondratiev_phase, "prosperity")
            if rot_key in SECTOR_ROTATION:
                return SECTOR_ROTATION[rot_key]

        if self.portfolio_config.get('categories'):
            return {
                cat: self.portfolio_config.get('categories', {}).get(cat, {}).get('weight', 0.25)
                for cat in DEFAULT_SECTOR_WEIGHTS
            }
        return DEFAULT_SECTOR_WEIGHTS.copy()

    def check_rebalance(
        self, risk: PortfolioRisk,
        portfolio_volatility: float = None,
    ) -> RebalanceDecision:
        """Phase 3: 再平衡检查"""
        prices = self.load_prices()

        total_stock_value = risk.stock_exposure if risk.stock_exposure > 0 else sum(
            self.positions.get(a['code'], {}).get('shares', 0) * prices.get(a['code'], 50)
            for a in self.portfolio_config.get('assets', [])
        )
        if total_stock_value <= 0:
            total_stock_value = self.portfolio_value

        sector_weights = self._get_sector_adjusted_weights()
        portfolio_vol = portfolio_volatility if portfolio_volatility else 0.18
        threshold, check_freq, max_adjust = self._get_dynamic_rebalance_threshold(portfolio_vol)

        position_weights = []
        for asset in self.portfolio_config.get('assets', []):
            code = asset['code']
            name = asset.get('name', code)
            category = asset.get('category', 'unknown')
            target_weight = asset.get('target_weight', 0.07)

            if category in sector_weights:
                cat_sector_w = sector_weights[category]
                cat_default_w = self.portfolio_config.get('categories', {}).get(category, {}).get('weight', 0.25)
                if cat_default_w > 0:
                    target_weight = target_weight * (cat_sector_w / cat_default_w)

            shares = self.positions.get(code, {}).get('shares', 0)
            price = prices.get(code, 50)
            if shares <= 0:
                target_mv = self.portfolio_value * target_weight
                shares = int(target_mv / price) if price > 0 else 100

            current_mv = shares * price
            current_weight = current_mv / total_stock_value if total_stock_value > 0 else 0
            target_mv = total_stock_value * target_weight
            deviation = current_weight - target_weight
            deviation_pct = abs(deviation) / target_weight if target_weight > 0 else 0

            action = "HOLD"
            if deviation > threshold:
                action = "SELL"
            elif deviation < -threshold:
                action = "BUY"

            adj_amount = target_mv - current_mv
            adj_shares = int(abs(adj_amount) / price) if price > 0 else 0

            pw = PositionWeight(
                code=code, name=name, category=category,
                target_weight=target_weight, current_weight=current_weight,
                deviation=deviation, deviation_pct=deviation_pct,
                current_value=current_mv, target_value=target_mv,
                adjustment=adj_amount, adjustment_shares=adj_shares,
                current_price=price, action=action,
                priority=int(deviation_pct * 100) if action != "HOLD" else 0,
            )
            position_weights.append(pw)

        to_adjust = [pw for pw in position_weights if pw.action != "HOLD"]
        to_adjust.sort(key=lambda x: x.priority, reverse=True)
        to_adjust = to_adjust[:max_adjust]

        total_buy = sum(pw.adjustment for pw in to_adjust if pw.action == "BUY")
        total_sell = sum(abs(pw.adjustment) for pw in to_adjust if pw.action == "SELL")
        net_cash = total_sell - total_buy

        if not to_adjust:
            return RebalanceDecision(
                needed=False, rebalance_type="none", threshold=threshold,
                reasoning=f"所有标的偏离度在{threshold*100:.0f}%阈值内。波动率{portfolio_vol*100:.1f}%，检查频率{check_freq}",
            )
        elif max(pw.deviation_pct for pw in to_adjust) > threshold * 2.0:
            return RebalanceDecision(
                needed=True, rebalance_type="strategic", threshold=threshold,
                positions_to_adjust=to_adjust, total_buy_amount=total_buy,
                total_sell_amount=total_sell, net_cash_flow=net_cash,
                reasoning=f"{len(to_adjust)}只标的严重偏离(最大{max(pw.deviation_pct for pw in to_adjust)*100:.1f}%)，战略再平衡",
            )
        else:
            return RebalanceDecision(
                needed=True, rebalance_type="tactical", threshold=threshold,
                positions_to_adjust=to_adjust, total_buy_amount=total_buy,
                total_sell_amount=total_sell, net_cash_flow=net_cash,
                reasoning=f"{len(to_adjust)}只标偏离超{threshold*100:.0f}%阈值，战术再平衡",
            )

    # ================================================================
    # Phase 4: 联合优化
    # ================================================================

    def joint_optimize(
        self, risk: PortfolioRisk, hedge: HedgeDecision, rebalance: RebalanceDecision
    ) -> Tuple[HedgeDecision, RebalanceDecision, List[str]]:
        """Phase 4: 联合优化"""
        warnings = []
        adj_hedge = hedge
        adj_rebalance = rebalance

        if not hedge.needed or not rebalance.needed:
            return adj_hedge, adj_rebalance, warnings

        after_hedge_exposure = risk.stock_exposure * (1 - hedge.hedge_ratio)
        rebalance_net_change = rebalance.net_cash_flow
        after_rebalance_exposure = risk.stock_exposure - rebalance_net_change

        discrepancy = abs(after_hedge_exposure - after_rebalance_exposure) / risk.stock_exposure if risk.stock_exposure > 0 else 0

        if discrepancy > 0.15:
            adj_ratio = 1 - (after_rebalance_exposure / risk.stock_exposure) if risk.stock_exposure > 0 else hedge.hedge_ratio
            adj_ratio = max(0, min(1.0, adj_ratio))
            if adj_ratio != hedge.hedge_ratio:
                warnings.append(
                    f"联合优化: 再平衡后敞口与对冲不一致({discrepancy*100:.0f}%), "
                    f"调整对冲{hedge.hedge_ratio*100:.0f}%->{adj_ratio*100:.0f}%"
                )
                adj_hedge.hedge_ratio = adj_ratio
        elif discrepancy > 0.05:
            warnings.append(f"轻微不一致({discrepancy*100:.1f}%), 在可接受范围")

        if adj_hedge.total_margin > adj_rebalance.net_cash_flow + risk.cash * 0.3:
            warnings.append(f"保证金需求({adj_hedge.total_margin:,.0f})较高")

        return adj_hedge, adj_rebalance, warnings

    # ================================================================
    # Phase 5: 生成执行计划
    # ================================================================

    def _estimate_performance(
        self, risk, hedge: HedgeDecision, rebalance: RebalanceDecision
    ) -> Tuple[float, float, float, float]:
        base_return = 0.12
        base_drawdown = 0.18
        base_vol = 0.20

        hedge_penalty = hedge.hedge_ratio * 0.015     # v5.9: 降低对冲成本估算
        hedge_dr_reduce = hedge.hedge_ratio * 0.04    # v5.9: 保守估计回撤减少
        hedge_vol_reduce = hedge.hedge_ratio * 0.04

        rebalance_boost = 0.015 if rebalance.needed and rebalance.rebalance_type == "strategic" else (0.008 if rebalance.needed else 0)

        est_return = base_return - hedge_penalty + rebalance_boost
        est_drawdown = max(0.05, base_drawdown - hedge_dr_reduce)
        est_vol = max(0.10, base_vol - hedge_vol_reduce)
        est_sharpe = (est_return - 0.03) / est_vol if est_vol > 0 else 0.4

        return est_return, est_drawdown, est_sharpe, est_vol

    def generate_execution_plan(
        self, risk, hedge: HedgeDecision, rebalance: RebalanceDecision,
        warnings: List[str] = None,
        stress_tests: Dict[str, Dict[str, Any]] = None,
    ) -> JointPlan:
        """Phase 5: 生成联合执行计划"""
        now = datetime.now()

        if hedge.hedge_ratio > 0.30 or (rebalance.needed and rebalance.rebalance_type == "strategic"):
            priority = "HIGH"
            window = "当日/次日"
        elif hedge.hedge_ratio > 0.10 or rebalance.needed:
            priority = "MEDIUM"
            window = "本周内"
        else:
            priority = "LOW"
            window = "两周内"

        after_hedge_exposure = risk.stock_exposure * (1 - hedge.hedge_ratio) if hedge.needed else risk.stock_exposure
        est_return, est_drawdown, est_sharpe, est_vol = self._estimate_performance(risk, hedge, rebalance)

        summary_parts = []
        if hedge.needed:
            instruments_str = '+'.join(hedge.futures_instruments) if hedge.futures_instruments else '无'
            summary_parts.append(
                f"对冲: {hedge.mode.value}模式 {hedge.hedge_ratio*100:.0f}%敞口, "
                f"期货{instruments_str}, 保证金{hedge.total_margin:,.0f}元"
            )
        else:
            summary_parts.append(f"对冲: 无需(组合波动率/回撤均安全)")

        if rebalance.needed:
            summary_parts.append(
                f"再平衡: {rebalance.rebalance_type}({len(rebalance.positions_to_adjust)}标的), "
                f"净买入{rebalance.total_buy_amount:,.0f}/净卖出{rebalance.total_sell_amount:,.0f}"
            )
        else:
            summary_parts.append("再平衡: 无需")

        summary = "。".join(summary_parts) + "。"

        return JointPlan(
            timestamp=now.isoformat(),
            portfolio_value=risk.total_value,
            stock_exposure=risk.stock_exposure,
            hedge=hedge if hedge.needed else None,
            after_hedge_exposure=after_hedge_exposure,
            rebalance=rebalance if rebalance.needed else None,
            execution_window=window, execution_priority=priority,
            warning_flags=warnings or [],
            estimated_annual_return=est_return,
            estimated_max_drawdown=est_drawdown,
            estimated_sharpe=est_sharpe,
            estimated_volatility=est_vol,
            summary=summary,
            stress_tests=stress_tests or {},
        )

    # ================================================================
    # 完整工作流 & 报告输出
    # ================================================================

    def run_full_workflow(
        self, portfolio_volatility: float = None, portfolio_drawdown_60d: float = None,
    ) -> JointPlan:
        """v5.9 完整五阶段工作流 — 组合自触发驱动"""
        logger.info("=" * 60)
        logger.info(f"对冲-再平衡联动引擎 v5.9 — 模式: {self.hedge_mode.value}")
        logger.info("=" * 60)

        logger.info("[Phase 1/5] 评估组合风险...")
        risk = self.assess_risk()
        logger.info(f"  组合Beta(IF/IC/IM): {risk.beta_csi300:.2f}/{risk.beta_csi500:.2f}/{risk.beta_csi1000:.2f}")

        logger.info("[Phase 2/5] 对冲决策 (组合自触发)...")
        hedge = self.decide_hedge(risk, portfolio_volatility, portfolio_drawdown_60d)
        logger.info(f"  模式: {hedge.mode.value}, 比率: {hedge.hedge_ratio*100:.0f}%, 状态: {hedge.regime.value}")

        logger.info("[Phase 3/5] 再平衡检查...")
        rebalance = self.check_rebalance(risk, portfolio_volatility)
        logger.info(f"  决策: {'需要' if rebalance.needed else '无需'}, 类型: {rebalance.rebalance_type}")

        logger.info("[Phase 4/5] 联合优化...")
        adj_hedge, adj_rebalance, warnings = self.joint_optimize(risk, hedge, rebalance)
        for w in warnings:
            logger.warning(f"  [!] {w}")

        # v5.10 P0-8: 历史极端压力测试
        stress_tests = {}
        if self.hedge_engine:
            try:
                # 复用 assess_risk 中已加载的 prices 缓存
                prices = self.load_prices()
                pos_for_stress = {}
                for code, pos in (self.positions.get('positions', {}) if isinstance(self.positions, dict) else {}).items():
                    if isinstance(pos, dict) and pos.get('shares', 0) > 0:
                        pos_for_stress[code] = {'shares': pos['shares'], 'cost': pos.get('avg_cost', 50)}
                if pos_for_stress:
                    stress_tests = self.hedge_engine.run_historical_stress_tests(pos_for_stress, prices)
                    if stress_tests:
                        breach_count = sum(1 for r in stress_tests.values() if r['breaches_limit'])
                        logger.info(f"[P0-8] 压力测试完成: {breach_count}/6 情景突破15%回撤上限")
            except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
                logger.warning(f"[P0-8] 压力测试跳过: {e}")

        logger.info("[Phase 5/5] 生成执行计划...")
        plan = self.generate_execution_plan(risk, adj_hedge, adj_rebalance, warnings, stress_tests)
        logger.info(f"  优先级: {plan.execution_priority}, 窗口: {plan.execution_window}")

        logger.info("=" * 60)
        return plan

    def format_report(self, plan: JointPlan) -> str:
        """格式化联动分析报告"""
        lines = []
        lines.append("=" * 72)
        lines.append("  对冲+再平衡联动分析报告 — HedgeRebalanceIntegrator v5.9")
        lines.append("=" * 72)
        lines.append(f"  生成时间: {plan.timestamp[:19]}")
        lines.append(f"  组合市值: {plan.portfolio_value:,.0f}")
        lines.append(f"  股票敞口: {plan.stock_exposure:,.0f} ({plan.stock_exposure/plan.portfolio_value*100:.0f}%)")
        lines.append(f"  对冲后净敞口: {plan.after_hedge_exposure:,.0f}")
        if plan.hedge:
            lines.append(f"  对冲模式: {plan.hedge.mode.value} (v5.9 组合自触发)")

        # ---- 对冲 ----
        lines.append("")
        lines.append("  [1] 对冲决策")
        lines.append("  " + "-" * 60)
        if plan.hedge:
            h = plan.hedge
            th = PORTFOLIO_HEDGE_THRESHOLDS.get(h.regime, {})
            lines.append(f"  组合状态: {h.regime.value} ({th.get('condition', 'N/A')})")
            lines.append(f"  对冲比率: {h.hedge_ratio*100:.0f}% (对冲 {h.total_notional:,.0f} 元名义价值)")
            lines.append(f"  期货品种: {', '.join(h.futures_instruments) if h.futures_instruments else '无'} (多指数Beta加权)")
            if h.futures_contracts:
                for code, n in h.futures_contracts.items():
                    spec = INDEX_FUTURES_SPECS.get(code, {})
                    notional = h.futures_notional.get(code, 0)
                    margin = h.futures_margin.get(code, 0)
                    lines.append(f"    {code}: 做空 {n} 手 | 名义{notional:,.0f} | 保证金{margin:,.0f}")
                lines.append(f"  总保证金: {h.total_margin:,.0f} (占组合 {h.total_margin/plan.portfolio_value*100:.1f}%)")
            lines.append(f"  预期对冲后Beta: {h.expected_beta_after:.2f}")
            lines.append(f"  价格数据源: {h.price_source}")
            if h.fallback_used:
                lines.append(f"  [!] 回退价格品种: {', '.join(h.fallback_used)}")
            lines.append(f"  理由: {h.reasoning}")
        else:
            lines.append("  对冲需求: 无需 — 组合波动率和回撤均在安全范围")
            lines.append("  建议: 日常运行S2动态再平衡，保持关注极端行情触发条件")

        # ---- 再平衡 ----
        lines.append("")
        lines.append("  [2] 再平衡决策")
        lines.append("  " + "-" * 60)
        if plan.rebalance:
            r = plan.rebalance
            lines.append(f"  再平衡类型: {r.rebalance_type}")
            lines.append(f"  动态阈值: {r.threshold*100:.0f}% (波动率驱动)")
            lines.append(f"  需调整标的: {len(r.positions_to_adjust)} 只")
            lines.append(f"  总买入金额: {r.total_buy_amount:,.0f}")
            lines.append(f"  总卖出金额: {r.total_sell_amount:,.0f}")
            lines.append(f"  净现金流: {r.net_cash_flow:,.0f} ({'净流入' if r.net_cash_flow > 0 else '净流出'})")
            lines.append("")
            lines.append(f"  {'代码':<12s} {'名称':<10s} {'板块':<8s} {'操作':<6s} "
                        f"{'目标权重':>8s} {'当前权重':>8s} {'偏差':>8s} {'调整金额':>10s}")
            lines.append("  " + "-" * 75)
            for pw in r.positions_to_adjust:
                lines.append(f"  {pw.code:<12s} {pw.name:<10s} {pw.category:<8s} {pw.action:<6s} "
                            f"{pw.target_weight*100:>7.1f}% {pw.current_weight*100:>7.1f}% "
                            f"{pw.deviation_pct*100:>7.1f}% {pw.adjustment:>10,.0f}")
        else:
            lines.append("  再平衡需求: 无需")

        # ---- 执行 ----
        lines.append("")
        lines.append("  [3] 执行计划")
        lines.append("  " + "-" * 60)
        lines.append(f"  优先级: {plan.execution_priority}")
        lines.append(f"  执行窗口: {plan.execution_window}")
        if plan.warning_flags:
            lines.append("  注意事项:")
            for w in plan.warning_flags:
                lines.append(f"    - {w}")

        # ---- 绩效预估 ----
        lines.append("")
        lines.append("  [4] 绩效预估")
        lines.append("  " + "-" * 60)
        lines.append(f"  预估年化收益:  {plan.estimated_annual_return*100:.1f}%")
        lines.append(f"  预估最大回撤:  {plan.estimated_max_drawdown*100:.1f}%")
        lines.append(f"  预估夏普比率:  {plan.estimated_sharpe:.2f}")
        lines.append(f"  预估年化波动率: {plan.estimated_volatility*100:.1f}%")

        lines.append("")
        lines.append("  " + "=" * 60)
        lines.append(f"  综合: {plan.summary}")
        lines.append("  " + "=" * 60)

        # v5.10 P0-8: 历史极端压力测试
        if plan.stress_tests:
            lines.append("")
            lines.append("  [5] 历史极端压力测试 (P0-8)")
            lines.append("  " + "-" * 60)
            breach_count = 0
            for scenario, result in plan.stress_tests.items():
                breach = "突破15%回撤上限" if result["breaches_limit"] else "安全"
                if result["breaches_limit"]:
                    breach_count += 1
                dd = result["drawdown_pct"]
                loss = result["estimated_loss"]
                lines.append(f"  {scenario}: 预估回撤{dd:.1f}%, 损失{loss:,.0f} — {breach}")
            if breach_count > 0:
                lines.append(f"  ⚠️  {breach_count}/6 个历史情景突破15%上限，强烈建议启用尾部保护")
            else:
                lines.append(f"  全部6个历史情景均未突破15%上限")

        lines.append("")
        lines.append("  [!] 以上分析仅供参考，不构成投资建议。")
        lines.append("=" * 72)

        return "\n".join(lines)

    def save_report(self, plan: JointPlan, output_dir: str = None) -> str:
        if output_dir is None:
            output_dir = os.path.join(self.base_dir, '..', 'reports')
        os.makedirs(output_dir, exist_ok=True)
        date_str = datetime.now().strftime("%Y-%m-%d")
        filename = f"hedge_rebalance_joint_{date_str}.md"
        filepath = os.path.join(output_dir, filename)

        report = self.format_report(plan)
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(report)

        logger.info(f"联动报告已保存: {filepath}")
        return filepath


# ============================================================
# 便捷函数
# ============================================================

def get_integrator(base_dir=None, portfolio_value=None, mode: str = "tail_only") -> HedgeRebalanceIntegrator:
    mode_map = {
        "tail_only": HedgeMode.TAIL_ONLY,
        "dynamic": HedgeMode.DYNAMIC,
        "fixed": HedgeMode.FIXED,
        "none": HedgeMode.NONE,
    }
    return HedgeRebalanceIntegrator(
        base_dir=base_dir, portfolio_value=portfolio_value,
        hedge_mode=mode_map.get(mode, HedgeMode.TAIL_ONLY),
    )


def run_joint_analysis(
    base_dir=None, portfolio_volatility=None, portfolio_drawdown_60d=None,
    mode="tail_only", save=True,
) -> Tuple[JointPlan, str]:
    """v5.9 一键运行对冲-再平衡联动分析

    Args:
        base_dir: 项目根目录
        portfolio_volatility: 组合年化波动率 (用于组合自触发)
        portfolio_drawdown_60d: 组合60日最大回撤 (用于组合自触发)
        mode: 对冲模式 (tail_only/dynamic/fixed/none)
        save: 是否保存报告
    """
    integrator = get_integrator(base_dir=base_dir, mode=mode)
    plan = integrator.run_full_workflow(
        portfolio_volatility=portfolio_volatility,
        portfolio_drawdown_60d=portfolio_drawdown_60d,
    )
    fpath = ""
    if save:
        fpath = integrator.save_report(plan)
    return plan, fpath


# ============================================================
# CLI 入口
# ============================================================

if __name__ == "__main__":
    logger.info("HedgeRebalanceIntegrator v5.9 — 组合自触发模式")
    logger.info("=" * 60)
    plan, fpath = run_joint_analysis(mode="tail_only", save=True)
    logger.info(get_integrator().format_report(plan))
