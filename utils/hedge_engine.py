# -*- coding: utf-8 -*-
"""
对冲引擎 v5.9 — 多指数Beta加权 + 组合自触发 + 成本意识优化

v5.9 核心改进（基于2021-2026回测发现）:
1. 多指数Beta加权对冲分配 — IC/IM/IF按Beta比例分配，替代纯IF
2. 组合自身波动率触发 — 不再依赖CSI300市场状态判断
3. 成本效益阈值 — 仅在对冲收益预期 > 成本*1.5时激活
4. 极端行情尾部保护模式 — 默认模式，仅在vol>28%或DD>12%时触发

数据源: iFinD MCP → Wind MCP → Sina/AKShare (免费回退)
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

logger = logging.getLogger('hedge_engine')

# G11: 统一 CVaR 双代码路径口径 — 无历史数据分支复用主风险流程的蒙特卡洛 CVaR
try:
    from utils.wt_risk_control import PortfolioRiskAnalyzer as _WTPortfolioRiskAnalyzer
except Exception:  # noqa: BLE001
    _WTPortfolioRiskAnalyzer = None  # 降级: 保留原 var*2.0 近似

# ── 指数成分股权重(简化版) ──
INDEX_WEIGHTS_CSI300 = {
    "300750": 0.042, "600519": 0.055, "000858": 0.038, "601318": 0.032,
    "600036": 0.028, "000333": 0.025, "002415": 0.022, "300059": 0.020,
    "600276": 0.018, "601166": 0.016, "600900": 0.015, "000651": 0.014,
    "002475": 0.013, "601899": 0.013, "603259": 0.012,
}
INDEX_WEIGHTS_CSI500 = {
    "688981": 0.008, "688041": 0.007, "002371": 0.006, "300308": 0.005,
    "000792": 0.004, "600219": 0.004, "002422": 0.004, "000425": 0.003,
    "600019": 0.003, "601088": 0.005,
}

# ── 股指期货合约规格 ──
INDEX_FUTURES_SPECS = {
    "IF": {
        "name": "沪深300股指期货", "underlying": "CSI300",
        "multiplier": 300, "margin_pct": 0.12, "tick_size": 0.2,
        "contracts_per_month": 4, "dominant_contract_months": [3, 6, 9, 12],
        "sina_code": "nf_IF0",
    },
    "IC": {
        "name": "中证500股指期货", "underlying": "CSI500",
        "multiplier": 200, "margin_pct": 0.14, "tick_size": 0.2,
        "contracts_per_month": 4, "dominant_contract_months": [3, 6, 9, 12],
        "sina_code": "nf_IC0",
    },
    "IM": {
        "name": "中证1000股指期货", "underlying": "CSI1000",
        "multiplier": 200, "margin_pct": 0.15, "tick_size": 0.2,
        "contracts_per_month": 4, "dominant_contract_months": [3, 6, 9, 12],
        "sina_code": "nf_IM0",
    },
    "IH": {
        "name": "上证50股指期货", "underlying": "SSE50",
        "multiplier": 300, "margin_pct": 0.12, "tick_size": 0.2,
        "contracts_per_month": 4, "dominant_contract_months": [3, 6, 9, 12],
        "sina_code": "nf_IH0",
    },
}

# ── 期权合约规格 ──
ETF_OPTIONS_SPECS = {
    "510300": {
        "name": "沪深300ETF期权", "underlying": "510300.SH",
        "multiplier": 10000, "strike_step": 0.1, "exchange": "SSE",
    },
    "510050": {
        "name": "上证50ETF期权", "underlying": "510050.SH",
        "multiplier": 10000, "strike_step": 0.05, "exchange": "SSE",
    },
    "000300": {
        "name": "沪深300指数期权", "underlying": "000300.SH",
        "multiplier": 100, "strike_step": 50, "exchange": "CFFEX",
    },
}


class HedgeType(Enum):
    NONE = "none"
    FUTURES_SHORT = "futures_short"
    PUT_PROTECTIVE = "put_protective"
    PUT_SPREAD = "put_spread"
    COLLAR = "collar"
    DYNAMIC_DELTA = "dynamic_delta"


class HedgeSignalStrength(Enum):
    NO_HEDGE = 0
    LIGHT = 1       # 25%
    MODERATE = 2    # 50%
    STRONG = 3      # 75%
    FULL = 4        # 100%


@dataclass
class PortfolioRisk:
    """组合风险评估"""
    total_value: float = 0.0
    stock_exposure: float = 0.0
    cash: float = 0.0
    beta_csi300: float = 0.0
    beta_csi500: float = 0.0
    beta_csi1000: float = 0.0
    beta_sse50: float = 0.0
    volatility_30d: float = 0.0
    var_95_daily: float = 0.0
    cvar_95_daily: float = 0.0
    max_drawdown_current: float = 0.0
    correlation_matrix: Dict[str, float] = field(default_factory=dict)
    concentration_risk: float = 0.0
    # v5.10 P0-6/P0-7: 集中度和相关性增强
    sector_weights: Dict[str, float] = field(default_factory=dict)
    max_sector_weight: float = 0.0
    sector_concentration_warning: str = ""
    avg_pairwise_correlation: float = 0.0
    correlation_warning: str = ""
    mrc_warnings: List[str] = field(default_factory=list)
    max_single_mrc: float = 0.0


@dataclass  
class HedgeRecommendation:
    """对冲建议"""
    hedge_type: HedgeType = HedgeType.NONE
    strength: HedgeSignalStrength = HedgeSignalStrength.NO_HEDGE
    urgency_score: float = 0.0

    futures_instruments: List[str] = field(default_factory=list)
    futures_contracts: Dict[str, int] = field(default_factory=dict)
    futures_notional: Dict[str, float] = field(default_factory=dict)
    futures_margin: Dict[str, float] = field(default_factory=dict)

    options_instruments: List[str] = field(default_factory=list)
    options_strategy: str = ""
    options_contracts: List[Dict] = field(default_factory=list)
    options_cost: float = 0.0
    options_max_loss: float = 0.0

    hedge_ratio: float = 0.0
    effective_hedge_pct: float = 0.0
    expected_beta_after: float = 0.0
    expected_drawdown_reduce: float = 0.0

    reasoning: str = ""
    risk_signals: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""
    stress_tests: Dict[str, Dict[str, Any]] = field(default_factory=dict)  # v5.10 P0-8
    sector_warnings: List[str] = field(default_factory=list)  # v5.10 P0-6
    correlation_warning: str = ""  # v5.10 P0-7
    mrc_warnings: List[str] = field(default_factory=list)  # v5.10 P0-6


# ── 默认期货价格回退表 ──
DEFAULT_FUTURES_PRICES = {
    "IF": 3950.0, "IC": 6200.0, "IM": 6800.0, "IH": 2700.0,
}
FALLBACK_PRICES_UPDATED = "2026-06-29"

DEFAULT_INDEX_PRICES = {
    "CSI300": 3950.0, "CSI500": 6200.0, "CSI1000": 6800.0, "SSE50": 2700.0,
}

VOLATILITY_TARGET_ANNUAL = 0.18

# ── 对冲成本参数 ──
HEDGE_ROLL_COST_ANNUAL = 0.025    # 年化展期成本(基差+交易费)
HEDGE_MARGIN_OPP_COST = 0.020     # 保证金机会成本(按无风险利率)


# ── v5.9 新增：多指数Beta分配权重 ──
# 指数优先级的判定基于组合在各指数的暴露度
INDEX_ALLOCATION_ORDER = ["IC", "IM", "IF"]  # 中证500优先(匹配中小盘成长), 中证1000次之

# ── v5.9 新增：组合自触发阈值 ──
PORTFOLIO_TAIL_HEDGE_TRIGGERS = {
    "vol_trigger": 0.28,       # 年化波动率>28%触发
    "dd_trigger": 0.12,        # 60日最大回撤>12%触发
    "min_hedge_ratio": 0.25,   # 触发后最小对冲比率
    "max_hedge_ratio": 0.50,   # 触发后最大对冲比率
}

# ── v5.9 新增：成本效益阈值 ──
COST_BENEFIT_THRESHOLD = 1.5   # 预期对冲收益必须 > 对冲成本 * 1.5 才激活


# ============================================================
# 第零层: iFinD MCP 期货价格 (P0 — 最高优先级)
# ============================================================

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
        _skill_dir = os.path.join(os.path.expanduser("~"), ".trae", "skills", "ifind-finance-data")
        _call_path = os.path.join(_skill_dir, "call.py")
        
        # 安全检查：验证文件路径不在受保护目录外
        _real_skill_dir = os.path.realpath(_skill_dir)
        _real_call_path = os.path.realpath(_call_path)
        if not _real_call_path.startswith(_real_skill_dir + os.sep):
            logger.warning(f"[hedge] 拒绝加载外部路径模块: {_call_path}")
            IFIND_CLIENT = None
        else:
            _spec = importlib.util.spec_from_file_location("ifind_call_hedge", _call_path)
            _mod = importlib.util.module_from_spec(_spec)
            _spec.loader.exec_module(_mod)
            IFIND_CLIENT = _mod
            logger.info("[hedge] iFinD MCP 连接器加载成功")
    except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
        IFIND_CLIENT = None
        logger.warning(f"[hedge] iFinD MCP 连接器不可用: {e}")


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


def fetch_futures_prices_from_ifind() -> Dict[str, float]:
    """P0: iFinD MCP → 股指期货价格"""
    results = {}
    if not IFIND_AVAILABLE or IFIND_CLIENT is None:
        return results
    queries = {
        "IF": "沪深300股指期货最新成交价",
        "IC": "中证500股指期货最新成交价",
        "IM": "中证1000股指期货最新成交价",
        "IH": "上证50股指期货最新成交价",
    }
    for name, question in queries.items():
        try:
            result = _exec_ifind("stock", "get_stock_summary", {"query": question})
            data = result.get("data") or {}
            if isinstance(data, dict):
                data = data.get("result", data)
            content = data.get("content", []) if isinstance(data, dict) else []
            text = content[0].get("text", "") if content else ""
            import re
            m = re.search(r"\|[^|]*\|\s*([\d,.]+)", text)
            if m:
                price = float(m.group(1).replace(",", ""))
                if price > 0:
                    results[name] = price
                    logger.info("[hedge][ifind] %s=%.2f", name, price)
        except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
            logger.debug("[hedge][ifind] %s err: %s", name, e)
    return results


# ============================================================
# 期货价格获取（多源回退）
# ============================================================

def fetch_futures_prices_from_wind() -> Dict[str, float]:
    """P1: Wind MCP → 股指期货价格 (analytics_data NL查询 + index_data 回退)"""
    results = {}
    try:
        from quant_modules.wind_mcp import _wind_mcp_call
        # 指数→期货映射
        index_map = {
            "IF": "000300.SH",  # 沪深300
            "IC": "000905.SH",  # 中证500
            "IM": "000852.SH",  # 中证1000
            "IH": "000016.SH",  # 上证50
        }
        for name, windcode in index_map.items():
            try:
                data = _wind_mcp_call('index_data', 'get_index_price_indicators', {
                    "windcode": windcode,
                    "indexes": "最新成交价"
                }, timeout=10)
                if data:
                    rows = data.get('rows', [])
                    if rows and rows[0]:
                        price = float(rows[0][0]) if data.get('columns') else 0
                        if price > 0:
                            # 期货约等于指数+基差(简化为指数价)
                            results[name] = price
            except Exception as e:  # noqa: BLE001  # 显式记录, 不静默
                logger.warning("[wind] 单条期货行情解析失败 (%s): %s", name, e)
    except ImportError:
        logger.debug("[wind] quant_modules.wind_mcp 导入失败")
    except Exception as e:  # noqa: BLE001  # 显式记录, 不静默
        logger.warning("[wind] 期货价格获取失败: %s", e)
    return results


def fetch_futures_prices_from_sina() -> Dict[str, float]:
    import urllib.request
    import re
    sina_codes = {"IF": "nf_IF0", "IC": "nf_IC0", "IM": "nf_IM0", "IH": "nf_IH0"}
    results = {}
    for name, code in sina_codes.items():
        try:
            url = f"https://hq.sinajs.cn/list={code}"
            req = urllib.request.Request(url, headers={"Referer": "https://finance.sina.com.cn"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                text = resp.read().decode("gbk", errors="ignore")
            match = re.search(r'="([^"]+)"', text)
            if match:
                parts = match.group(1).split(",")
                if len(parts) >= 20:
                    price = float(parts[3]) if parts[3] and parts[3] != "0.000" else 0.0
                    if price > 0:
                        results[name] = price
        except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
            logger.debug(f"[sina] {name} 失败: {e}")
    return results


def fetch_futures_prices_from_akshare() -> Dict[str, float]:
    results = {}
    try:
        import akshare as ak
        for name in ["IF", "IC", "IM", "IH"]:
            try:
                df = ak.futures_main_sina(symbol=f"{name}0")
                if df is not None and not df.empty:
                    price = float(df.iloc[-1]['close']) if 'close' in df.columns else float(df.iloc[-1].iloc[-2])
                    if price > 0:
                        results[name] = price
            except Exception:  # noqa: BLE001  # fail-safe, 待后续精确化
                pass
    except ImportError:
        logger.debug("[akshare] 未安装")
    except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
        logger.debug(f"[akshare] 批量获取失败: {e}")
    return results


def fetch_futures_prices_from_efinance() -> Dict[str, float]:
    results = {}
    try:
        import efinance as ef
        efinance_codes = {"IF": "IF0", "IC": "IC0", "IM": "IM0", "IH": "IH0"}
        for name, code in efinance_codes.items():
            try:
                quote = ef.futures.get_realtime_quotes(code)
                if quote is not None:
                    price = float(quote.price) if hasattr(quote, 'price') and quote.price else 0
                    if not price and isinstance(quote, dict):
                        price = float(quote.get('price', 0) or quote.get('最新价', 0))
                    if price > 0:
                        results[name] = price
            except Exception:  # noqa: BLE001  # fail-safe, 待后续精确化
                pass
    except ImportError:
        logger.debug("[efinance] 未安装")
    except Exception:  # noqa: BLE001  # fail-safe, 待后续精确化
        pass
    return results


def get_live_futures_prices(force_refresh: bool = False) -> Dict[str, float]:
    # v5.10: iFinD MCP (P0) → Wind MCP (P1) → AKShare (P2) → Sina (P3) → efinance (P4) → 默认回退 (P5)
    prices = {}
    source_used = "none"

    # P0: iFinD MCP
    prices.update(fetch_futures_prices_from_ifind())
    if len(prices) >= 4:
        source_used = "ifind_mcp"

    # P1: Wind MCP
    missing = [k for k in ["IF", "IC", "IM", "IH"] if k not in prices]
    if missing:
        prices.update(fetch_futures_prices_from_wind())
        if source_used == "none" and len(prices) >= 3:
            source_used = "wind_mcp"

    # P2: AKShare
    missing = [k for k in ["IF", "IC", "IM", "IH"] if k not in prices]
    if missing:
        ak_prices = fetch_futures_prices_from_akshare()
        for k in missing:
            if k in ak_prices:
                prices[k] = ak_prices[k]
        if source_used == "none" and len(prices) >= 3:
            source_used = "akshare"

    # P3: Sina
    missing = [k for k in ["IF", "IC", "IM", "IH"] if k not in prices]
    if missing:
        sina_prices = fetch_futures_prices_from_sina()
        for k in missing:
            if k in sina_prices:
                prices[k] = sina_prices[k]
        if source_used == "none" and len(prices) >= 3:
            source_used = "sina"

    # P4: efinance
    missing = [k for k in ["IF", "IC", "IM", "IH"] if k not in prices]
    if missing:
        ef_prices = fetch_futures_prices_from_efinance()
        for k in missing:
            if k in ef_prices:
                prices[k] = ef_prices[k]
        if source_used == "none" and len(prices) >= 2:
            source_used = "efinance"

    fallback_used = []
    for name in ["IF", "IC", "IM", "IH"]:
        if name not in prices or prices[name] <= 0:
            prices[name] = DEFAULT_FUTURES_PRICES.get(name, 4000)
            fallback_used.append(name)

    if fallback_used:
        logger.warning(f"[!] 期货品种使用回退价格: {', '.join(fallback_used)}")

    if source_used != "none":
        logger.info(f"[OK] 期货价格 ({source_used}), 完整度: {4 - len(fallback_used)}/4")

    return prices


# ============================================================
# HedgeEngine v5.9
# ============================================================

class HedgeEngine:
    """对冲引擎核心类 v5.9

    核心改进:
    1. 多指数Beta加权对冲分配 — IC/IM优先, IF辅助
    2. 组合自触发尾部对冲 — 不依赖外部市场状态
    3. 成本效益过滤 — 对冲期望收益必须 > 1.5倍成本
    """

    def __init__(self, portfolio_value: float = 1_000_000):
        self.portfolio_value = portfolio_value
        self._price_cache: Dict[str, float] = {}
        self._beta_cache: Dict[str, float] = {}

    # ── 风险评估 ──

    def assess_portfolio_risk(
        self,
        positions: Dict[str, Dict[str, Any]],
        prices: Dict[str, float],
        historical_returns: Dict[str, List[float]] = None,
        cash: float = 0.0,
    ) -> PortfolioRisk:
        """评估组合风险 v5.10 — 协方差矩阵VaR修复 (P0-5)

        v5.10 改进:
        - 组合VaR = sqrt(w^T * Σ * w) * z * total_value
        - 当historical_returns可用时, 从历史数据推算协方差矩阵
        - 无历史数据时回退到独立假设并标注风险低估警告
        """
        risk = PortfolioRisk()

        total_stock = 0.0
        stock_weights = {}
        codes_in_portfolio = []

        for code, pos in positions.items():
            shares = pos.get('shares', 0)
            # 后缀兼容: 尝试纯数字码 → .SH/.SZ 后缀码
            price = prices.get(code, 0)
            if price <= 0:
                for suffix in ['.SH', '.SZ']:
                    price = prices.get(code + suffix, 0)
                    if price > 0:
                        break
            # 无行情时用兜底价格
            if price <= 0:
                price = self._estimate_default_price(code)
            market_value = shares * price
            total_stock += market_value
            if market_value > 0:
                stock_weights[code] = market_value
                codes_in_portfolio.append(code)

        risk.stock_exposure = total_stock
        risk.cash = cash
        risk.total_value = total_stock + cash

        if total_stock <= 0:
            return risk

        total_weight = sum(stock_weights.values())
        if total_weight > 0:
            for code in stock_weights:
                stock_weights[code] /= total_weight

        risk.beta_csi300 = self._compute_weighted_beta(stock_weights, "CSI300")
        risk.beta_csi500 = self._compute_weighted_beta(stock_weights, "CSI500")
        risk.beta_csi1000 = self._compute_weighted_beta(stock_weights, "CSI1000")
        risk.beta_sse50 = self._compute_weighted_beta(stock_weights, "SSE50")

        # ── v5.10 VaR协方差矩阵修复 (P0-5) ──
        z_95 = 1.645  # 95%置信度z-score

        if historical_returns and len(codes_in_portfolio) > 0:
            # 有历史数据: 使用协方差矩阵法计算组合VaR
            risk.volatility_30d = self._compute_portfolio_vol_cov(
                stock_weights, historical_returns, codes_in_portfolio
            )
            risk.var_95_daily = risk.total_value * risk.volatility_30d * z_95
            # ES_95%: 历史模拟法
            risk.cvar_95_daily = self._compute_expected_shortfall(
                stock_weights, historical_returns, codes_in_portfolio, risk.total_value, 0.95
            )
        else:
            # 无历史数据: 回退到独立假设但标注风险低估
            market_vol = 0.20
            risk.volatility_30d = (
                risk.beta_csi300 * market_vol / math.sqrt(12)
                if risk.beta_csi300 > 0
                else 0.02
            )
            risk.var_95_daily = risk.total_value * risk.volatility_30d * z_95
            # G11 统一口径: 无历史数据时不再用 var*2.0 粗暴近似,
            # 复用主风险流程的蒙特卡洛 CVaR (肥尾), 与 wt_risk_control 路径一致.
            if _WTPortfolioRiskAnalyzer is not None:
                try:
                    risk.cvar_95_daily = _WTPortfolioRiskAnalyzer._cvar_monte_carlo(
                        risk.total_value, risk.volatility_30d, 0.95, 50000, 1, 42,
                        dist="student_t", dof=5,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[VaR] 蒙特卡洛 CVaR 失败, 降级 var*2.0: %s", exc)
                    risk.cvar_95_daily = risk.var_95_daily * 2.0
            else:
                risk.cvar_95_daily = risk.var_95_daily * 2.0
            logger.warning(
                "[VaR] 无历史收益率数据, 使用蒙特卡洛CVaR近似 (可能仍低估真实风险), "
                "建议提供historical_returns参数以获得准确值"
            )

        # ── v5.10 P0-6/P0-7: 集中度+相关性增强监控 ──
        # HHI 集中度
        hhi = sum(w * w for w in stock_weights.values() if w > 0)
        risk.concentration_risk = hhi

        # P0-6: 板块集中度 (排除固收/国债ETF)
        sector_values: Dict[str, float] = {}
        stock_only_weight = 0.0
        for code, w in stock_weights.items():
            pure = code.split('.')[0] if '.' in code else code
            sector = self.SECTOR_MAP.get(pure, "其他")
            if sector in self.FIXED_INCOME_TYPES:
                continue  # 国债ETF不计入股票集中度
            sector_values[sector] = sector_values.get(sector, 0) + w
            stock_only_weight += w
        # 归一化到纯股票权重
        if stock_only_weight > 0:
            for sector in sector_values:
                sector_values[sector] /= stock_only_weight
        risk.sector_weights = sector_values
        risk.max_sector_weight = max(sector_values.values()) if sector_values else 0

        if risk.max_sector_weight > self.SECTOR_LIMIT:
            top_sector = max(sector_values, key=sector_values.get) if sector_values else ""
            risk.sector_concentration_warning = (
                f"{top_sector}板块权重{risk.max_sector_weight*100:.0f}% > {self.SECTOR_LIMIT*100:.0f}%上限 (纯股票口径)"
            )

        # P0-6: MRC (Marginal Risk Contribution) — 基于协方差矩阵
        if historical_returns:
            mrc_map = self._compute_mrc(
                stock_weights, historical_returns, codes_in_portfolio, risk.volatility_30d
            )
            for code, mrc in mrc_map.items():
                if mrc > self.MRC_LIMIT:
                    risk.mrc_warnings.append(
                        f"{code} MRC={mrc*100:.1f}% > {self.MRC_LIMIT*100:.0f}%上限"
                    )
            risk.max_single_mrc = max(mrc_map.values()) if mrc_map else 0

        # P0-7: 组合内平均相关性监控
        if historical_returns:
            corr_matrix = self.compute_correlation_matrix(
                historical_returns, codes_in_portfolio, lookback_days=60
            )
            if corr_matrix:
                corr_values = []
                for ci, inner in corr_matrix.items():
                    for cj, corr in inner.items():
                        if ci < cj:
                            corr_values.append(corr)
                if corr_values:
                    risk.avg_pairwise_correlation = sum(corr_values) / len(corr_values)
                    if risk.avg_pairwise_correlation > self.CORRELATION_WARN:
                        risk.correlation_warning = (
                            f"组合平均相关性{risk.avg_pairwise_correlation:.2f} > {self.CORRELATION_WARN:.2f}, "
                            f"呈现共振风险(20d均值)"
                        )

        return risk

    # v5.10: 类级别Beta常量 — 供压力测试和组合Beta计算复用
    DEFAULT_BETAS = {
        "300308": (1.35, 1.50, 1.60, 1.20),
        "688041": (1.40, 1.55, 1.70, 1.25),
        "002371": (1.25, 1.40, 1.50, 1.15),
        "688981": (1.30, 1.45, 1.55, 1.20),
        "300750": (1.20, 1.35, 1.45, 1.10),
        "000425": (1.05, 1.15, 1.25, 0.95),
        "601088": (0.85, 0.80, 0.75, 0.90),
        "600219": (0.90, 0.95, 1.05, 0.85),
        "600019": (0.95, 1.00, 1.10, 0.90),
        "518880": (0.40, 0.35, 0.30, 0.45),
        "000792": (0.80, 0.90, 1.00, 0.75),
        "600276": (0.75, 0.70, 0.65, 0.80),
        "603259": (0.90, 0.95, 1.00, 0.85),
        "002422": (0.70, 0.65, 0.60, 0.75),
    }

    # v5.10 P0-6: 板块映射 — 集中度监控
    SECTOR_MAP = {
        "300308": "高端制造", "688041": "高端制造", "002371": "高端制造",
        "688981": "高端制造", "300750": "高端制造", "000425": "高端制造",
        "601088": "顺周期", "600219": "顺周期", "600019": "顺周期",
        "518880": "黄金ETF", "000792": "资源",
        "600276": "防御", "603259": "防御", "002422": "防御",
        "600900": "防御",   # 长江电力
        "511010": "国债ETF",  # 固收敞口
    }

    # 固收/国债类不计入股票板块集中度
    FIXED_INCOME_TYPES = {"国债ETF"}

    SECTOR_LIMIT = 0.35  # 单一板块上限35% (P0-6)
    MRC_LIMIT = 0.25     # 单标的边际风险贡献上限25%
    CORRELATION_WARN = 0.70  # 平均相关性 > 0.7 触发预警 (P0-7)

    def _compute_weighted_beta(self, weights: Dict[str, float], index: str) -> float:
        idx_map = {"CSI300": 0, "CSI500": 1, "CSI1000": 2, "SSE50": 3}
        idx = idx_map.get(index, 0)

        total_beta = 0.0
        total_w = 0.0
        for code, w in weights.items():
            pure_code = code.split('.')[0] if '.' in code else code
            if pure_code in self.DEFAULT_BETAS:
                total_beta += w * self.DEFAULT_BETAS[pure_code][idx]
            else:
                total_beta += w * 1.0
            total_w += w

        return total_beta / total_w if total_w > 0 else 0

    # ── v5.10 P0-8: 历史极端压力测试 ──

    HISTORICAL_STRESS_SCENARIOS = {
        "2015股灾 (沪深300 -45%)": {
            "csi300": -0.45, "csi500": -0.50, "csi1000": -0.50, "sse50": -0.40,
            "gold": 0.02, "sector": "全面崩盘, 流动性枯竭, 千股停牌",
        },
        "2016熔断 (沪深300 -25%)": {
            "csi300": -0.25, "csi500": -0.30, "csi1000": -0.28, "sse50": -0.22,
            "gold": 0.01, "sector": "指数熔断, 恐慌抛售, 两日触发2次熔断",
        },
        "2018贸易战 (沪深300 -32%)": {
            "csi300": -0.32, "csi500": -0.35, "csi1000": -0.38, "sse50": -0.28,
            "gold": 0.04, "sector": "中美贸易摩擦升级, 科技股重挫, 人民币贬值",
        },
        "2020疫情闪崩 (沪深300 -16%)": {
            "csi300": -0.16, "csi500": -0.15, "csi1000": -0.14, "sse50": -0.14,
            "gold": 0.06, "sector": "新冠疫情全球爆发, 节后首日3000股跌停",
        },
        "2024国庆后暴跌 (沪深300 -20%)": {
            "csi300": -0.20, "csi500": -0.22, "csi1000": -0.25, "sse50": -0.18,
            "gold": 0.01, "sector": "政策宽松预期逆转, 前期过热回调",
        },
        "极端尾部事件 (1% VaR, -40%)": {
            "csi300": -0.40, "csi500": -0.45, "csi1000": -0.50, "sse50": -0.35,
            "gold": 0.08, "sector": "复合危机: 流动性枯竭+信用违约+汇率贬值叠加",
        },
    }

    def run_historical_stress_tests(
        self,
        positions: Dict[str, Dict[str, Any]],
        prices: Dict[str, float],
    ) -> Dict[str, Dict[str, Any]]:
        """v5.10 P0-8修复: 6个历史极端情景压力测试

        返回每个情景下的:
        - estimated_loss: 预估组合损失金额
        - drawdown_pct: 预估回撤百分比
        - breaches_limit: 是否突破15%最大回撤目标
        - surviving_value: 压力后组合剩余价值
        - sector_detail: 各板块受损明细
        """
        from dataclasses import dataclass

        # 计算各标的对每个指数的加权beta暴露
        stock_codes = list(positions.keys())
        total_mv = sum(
            positions[c]['shares'] * prices.get(c, self._estimate_default_price(c))
            for c in stock_codes
        )
        if total_mv <= 0:
            return {}

        results = {}

        for scenario_name, shocks in self.HISTORICAL_STRESS_SCENARIOS.items():
            estimated_loss = 0.0

            for code in stock_codes:
                pos = positions[code]
                shares = pos['shares']
                if shares <= 0:
                    continue

                current_price = prices.get(code, self._estimate_default_price(code))
                current_mv = shares * current_price

                # 判断标的对4个指数的Beta暴露
                pure = code.split('.')[0] if '.' in code else code
                betas = self.DEFAULT_BETAS.get(pure, [1.0, 1.0, 1.0, 1.0])

                # 加权指数冲击 (历史情景为多日累计，不用日跌停板限制)
                weighted_shock = (
                    betas[0] * shocks["csi300"] +
                    betas[1] * shocks["csi500"] +
                    betas[2] * shocks["csi1000"] +
                    betas[3] * shocks["sse50"]
                ) / 4.0

                # 黄金ETF特殊处理: 危机中黄金通常上涨
                if code in ("518880", "518880.SH") or "黄金" in code:
                    weighted_shock = -shocks.get("gold", 0.02)

                loss = current_mv * weighted_shock
                estimated_loss += loss

            # 计算压力后组合
            surviving_value = total_mv + estimated_loss
            drawdown_pct = abs(estimated_loss) / total_mv if total_mv > 0 else 0

            results[scenario_name] = {
                "estimated_loss": round(abs(estimated_loss), 0),
                "loss_pct": round(drawdown_pct * 100, 1),
                "drawdown_pct": round(drawdown_pct * 100, 1),
                "breaches_limit": drawdown_pct > 0.15,
                "surviving_value": round(surviving_value, 0),
                "total_value_before": round(total_mv, 0),
                "sector_impact": shocks["sector"],
            }

        return results

    def _compute_portfolio_vol(self, weights, returns) -> float:
        """旧版单资产波动率估算 — 保留向后兼容"""
        return 0.015

    def _estimate_default_price(self, code: str) -> float:
        """兜底价格 — 用于无实时行情时"""
        fallback_map = {
            "300308": 105.0, "300308.SZ": 105.0, "688041": 62.0, "688041.SH": 62.0,
            "002371": 320.0, "002371.SZ": 320.0, "688981": 55.0, "688981.SH": 55.0,
            "300750": 230.0, "300750.SZ": 230.0, "000425": 8.5, "000425.SZ": 8.5,
            "601088": 38.0, "601088.SH": 38.0, "600219": 5.0, "600219.SH": 5.0,
            "600019": 7.5, "600019.SH": 7.5, "518880": 5.2, "518880.SH": 5.2,
            "000792": 28.0, "000792.SZ": 28.0, "600900": 22.0, "600900.SH": 22.0,
            "600276": 48.0, "600276.SH": 48.0, "603259": 65.0, "603259.SH": 65.0,
            "002422": 32.0, "002422.SZ": 32.0, "511010": 108.5, "511010.SH": 108.5,
        }
        return fallback_map.get(code, 50.0)

    def _compute_portfolio_vol_cov(
        self,
        weights: Dict[str, float],
        historical_returns: Dict[str, List[float]],
        codes: List[str],
    ) -> float:
        """v5.10 协方差矩阵组合波动率 (P0-5修复核心)

        σ_p = sqrt(w^T * Σ * w)
        其中 Σ 是从历史日收益率推算的协方差矩阵

        仅使用codes_in_portfolio中包含的标的。
        """
        available_codes = [c for c in codes if c in historical_returns]
        if not available_codes:
            return 0.015

        # 获取每个标的的历史日收益率序列
        code_returns = {}
        min_len = float('inf')
        for code in available_codes:
            rets = historical_returns[code]
            if len(rets) < 30:
                continue  # 少于30天收益率的标的跳过
            code_returns[code] = rets[-252:]  # 最多取最近252天
            min_len = min(min_len, len(rets[-252:]))

        if len(code_returns) < 2:
            # 只有1个或更少标的有足够数据, 回退到加权标准差
            if code_returns:
                code = list(code_returns.keys())[0]
                w = weights.get(code, 0)
                rets = code_returns[code][-min_len:]
                avg_ret = sum(rets) / len(rets)
                vol = math.sqrt(sum((r - avg_ret) ** 2 for r in rets) / (len(rets) - 1))
                return vol * w
            return 0.015

        # 构建权重向量 (按codes_in_portfolio顺序)
        n = len(available_codes)
        w = [weights.get(c, 0.0) for c in available_codes]

        # 构建协方差矩阵
        cov = [[0.0] * n for _ in range(n)]
        for i, ci in enumerate(available_codes):
            rets_i = code_returns[ci][-min_len:]
            mean_i = sum(rets_i) / len(rets_i)
            for j, cj in enumerate(available_codes):
                rets_j = code_returns[cj][-min_len:]
                mean_j = sum(rets_j) / len(rets_j)
                # 样本协方差
                cov_ij = sum(
                    (rets_i[k] - mean_i) * (rets_j[k] - mean_j)
                    for k in range(min_len)
                ) / (min_len - 1)
                cov[i][j] = cov_ij

        # w^T * Σ * w
        port_var = 0.0
        for i in range(n):
            for j in range(n):
                port_var += w[i] * cov[i][j] * w[j]

        port_vol = math.sqrt(max(port_var, 0))
        return port_vol

    def _compute_expected_shortfall(
        self,
        weights: Dict[str, float],
        historical_returns: Dict[str, List[float]],
        codes: List[str],
        total_value: float,
        confidence: float = 0.95,
    ) -> float:
        """v5.10 历史模拟法计算 Expected Shortfall (P0-5修复)

        ES = 超过VaR的尾部损失平均值
        不再使用 VaR * 1.3 的粗暴近似。
        """
        available_codes = [c for c in codes if c in historical_returns]
        if not available_codes:
            return total_value * 0.015 * 1.645 * 2.0  # 保守回退

        min_len = min(len(historical_returns[c][-252:]) for c in available_codes)

        # 计算历史组合日收益率
        port_daily_returns = []
        for t in range(min_len):
            port_r = 0.0
            for c in available_codes:
                w = weights.get(c, 0.0)
                rets = historical_returns[c][-min_len:]
                port_r += w * rets[t]
            port_daily_returns.append(port_r)

        # 升序排列
        sorted_returns = sorted(port_daily_returns)

        # VaR阈值索引
        cutoff_idx = int(min_len * (1 - confidence))
        if cutoff_idx >= min_len:
            cutoff_idx = min_len - 1

        # ES = 超过VaR的尾部平均
        tail = sorted_returns[:cutoff_idx + 1]
        if not tail:
            return total_value * abs(sorted_returns[0]) * 2.0

        es_return = abs(sum(tail) / len(tail))
        return es_return * total_value

    def _compute_mrc(
        self,
        weights: Dict[str, float],
        historical_returns: Dict[str, List[float]],
        codes: List[str],
        portfolio_vol: float,
    ) -> Dict[str, float]:
        """v5.10 P0-6: 计算每个标的的边际风险贡献

        MRC_i = w_i * (Σw)_i / σ_p
        真正的风险平价要求各标的 MRC 相等。
        单标的 MRC > 25% 表示该标的承担了不成比例的风险。
        """
        available_codes = [c for c in codes if c in historical_returns]
        if len(available_codes) < 2 or portfolio_vol <= 0:
            return {}

        n = len(available_codes)
        min_len = min(
            len(historical_returns[c][-252:]) for c in available_codes
        )

        # 构建协方差矩阵 (简化: 只用可用标的)
        cov = [[0.0] * n for _ in range(n)]
        for i in range(n):
            rets_i = historical_returns[available_codes[i]][-min_len:]
            mean_i = sum(rets_i) / len(rets_i)
            var_i = sum((r - mean_i) ** 2 for r in rets_i) / (len(rets_i) - 1)
            cov[i][i] = var_i
            for j in range(i + 1, n):
                rets_j = historical_returns[available_codes[j]][-min_len:]
                mean_j = sum(rets_j) / len(rets_j)
                cov_ij = sum(
                    (rets_i[k] - mean_i) * (rets_j[k] - mean_j)
                    for k in range(min_len)
                ) / (min_len - 1)
                cov[i][j] = cov_ij
                cov[j][i] = cov_ij

        # Σw: 协方差加权向量
        sigma_w = [0.0] * n
        for i in range(n):
            wi = weights.get(available_codes[i], 0.0)
            for j in range(n):
                wj = weights.get(available_codes[j], 0.0)
                sigma_w[i] += cov[i][j] * wj

        # MRC_i = w_i * sigma_w_i / σ_p
        mrc = {}
        for i in range(n):
            wi = weights.get(available_codes[i], 0.0)
            mrc_i = wi * sigma_w[i] / portfolio_vol if portfolio_vol > 0 else 0
            mrc[available_codes[i]] = mrc_i

        return mrc

    # ── v5.9 信号强度（组合自触发优先） ──

    def determine_hedge_signal_strength(
        self,
        risk: PortfolioRisk,
        market_signals: Dict[str, Any] = None,
        portfolio_volatility: float = None,
        portfolio_drawdown_60d: float = None,
    ) -> Tuple[HedgeSignalStrength, float]:
        """v5.9 五因子模型 — 组合自触发权重提升

        因子权重:
        1. 组合Beta因子 (25%, 从40%降低) — 降权,回测证明CSI300Beta不准确
        2. 组合自波动率因子 (25%, v5.9新增) — 组合自身波动率超过阈值
        3. 组合自回撤因子 (20%, v5.9新增) — 60日最大回撤触发
        4. 集中度因子 (15%, 从20%降低)
        5. VaR尾部风险 (10%)
        6. 外部市场信号 (5%, 从10%降低)
        """
        score = 0.0
        reasons = []

        # 1. Beta因子 (权重25%, v5.9从40%降低)
        beta = max(risk.beta_csi300, risk.beta_csi500, risk.beta_csi1000)
        if beta > 1.5:
            score += 0.25
            reasons.append(f"组合Beta={beta:.2f}(取最大)较高")
        elif beta > 1.2:
            score += 0.15
            reasons.append(f"组合Beta={beta:.2f}偏高")
        elif beta > 0.8:
            score += 0.08

        # 2. 组合自波动率因子 (权重25%, v5.9新增)
        if portfolio_volatility is not None and portfolio_volatility > 0:
            current_vol = portfolio_volatility
        else:
            current_vol = risk.volatility_30d * math.sqrt(252) if risk.volatility_30d > 0 else 0.18

        vol_trigger = PORTFOLIO_TAIL_HEDGE_TRIGGERS["vol_trigger"]
        if current_vol > vol_trigger * 1.2:
            score += 0.25
            reasons.append(f"组合波动率{current_vol*100:.1f}%严重超标(>{vol_trigger*120:.0f}%)")
        elif current_vol > vol_trigger:
            score += 0.18
            reasons.append(f"组合波动率{current_vol*100:.1f}%超标(>{vol_trigger*100:.0f}%)")
        elif current_vol > vol_trigger * 0.8:
            score += 0.08

        # 3. 组合自回撤因子 (权重20%, v5.9新增)
        dd_trigger = PORTFOLIO_TAIL_HEDGE_TRIGGERS["dd_trigger"]
        if portfolio_drawdown_60d is not None and portfolio_drawdown_60d > 0:
            if portfolio_drawdown_60d > dd_trigger * 1.5:
                score += 0.20
                reasons.append(f"组合60日回撤{portfolio_drawdown_60d*100:.1f}%严重(>{dd_trigger*150:.0f}%)")
            elif portfolio_drawdown_60d > dd_trigger:
                score += 0.14
                reasons.append(f"组合60日回撤{portfolio_drawdown_60d*100:.1f}%超标(>{dd_trigger*100:.0f}%)")
            elif portfolio_drawdown_60d > dd_trigger * 0.7:
                score += 0.06

        # 4. 集中度因子 (权重15%)
        if risk.concentration_risk > 0.25:
            score += 0.15
            reasons.append(f"集中度HHI={risk.concentration_risk:.3f}过高")
        elif risk.concentration_risk > 0.15:
            score += 0.08

        # 5. VaR因子 (权重10%)
        var_pct = risk.var_95_daily / risk.total_value if risk.total_value > 0 else 0
        if var_pct > 0.03:
            score += 0.10
            reasons.append(f"日VaR(95%)={var_pct*100:.1f}%")
        elif var_pct > 0.02:
            score += 0.05

        # 6. 外部市场信号 (权重5%, v5.9大幅降低)
        if market_signals:
            panic = market_signals.get('panic_index', 0)
            if panic > 0.75:
                score += 0.05
                reasons.append("外部恐慌指数极高")

        # 信号强度判定
        if score >= 0.65:
            strength = HedgeSignalStrength.STRONG
        elif score >= 0.50:
            strength = HedgeSignalStrength.MODERATE
        elif score >= 0.35:
            strength = HedgeSignalStrength.LIGHT
        else:
            strength = HedgeSignalStrength.NO_HEDGE

        return strength, score

    def compute_optimal_hedge_ratio(
        self,
        risk: PortfolioRisk,
        hedge_strength: HedgeSignalStrength,
        method: str = "min_variance",
        portfolio_volatility: float = None,
        portfolio_drawdown_60d: float = None,
    ) -> float:
        """v5.9 最优对冲比率 — 组合自触发为上限

        核心逻辑: 对冲比率 = min(Beta中性比率, 尾部保护比率)
        尾部保护仅在组合自身波动率>28%或回撤>12%时显著激活。
        """
        strength_ratio = {
            HedgeSignalStrength.NO_HEDGE: 0.0,
            HedgeSignalStrength.LIGHT: 0.25,
            HedgeSignalStrength.MODERATE: 0.50,
            HedgeSignalStrength.STRONG: 0.75,
            HedgeSignalStrength.FULL: 1.0,
        }
        base_ratio = strength_ratio[hedge_strength]

        # Beta中性比率（使用多指数最大Beta）
        # 修复: 去掉 0.5 强制下限 — 低Beta组合(如黄金/国债ETF, beta≈0.3)会被高估对冲需求.
        # 用真实最大Beta, 低Beta组合对冲比例自然降低(符合风险), 纯无Beta组合不生成Beta对冲.
        max_beta = max(risk.beta_csi300, risk.beta_csi500, risk.beta_csi1000, 0.0)
        beta_neutral_ratio = max_beta * base_ratio * 0.70  # 70%因子考虑基差
        beta_neutral_ratio = min(beta_neutral_ratio, 1.0)

        # ── v5.9 核心: 组合自触发尾部保护比率 ──
        if portfolio_volatility is None:
            portfolio_volatility = risk.volatility_30d * math.sqrt(252) if risk.volatility_30d > 0 else 0.18

        vol_trigger = PORTFOLIO_TAIL_HEDGE_TRIGGERS["vol_trigger"]
        dd_trigger = PORTFOLIO_TAIL_HEDGE_TRIGGERS["dd_trigger"]
        max_ratio = PORTFOLIO_TAIL_HEDGE_TRIGGERS["max_hedge_ratio"]
        min_ratio = PORTFOLIO_TAIL_HEDGE_TRIGGERS["min_hedge_ratio"]

        tail_ratio = 0.0

        # 波动率触发
        if portfolio_volatility > vol_trigger:
            excess = portfolio_volatility - vol_trigger
            tail_ratio = min_ratio + excess * 2.5  # 每超出1%vol增加2.5%对冲
            tail_ratio = min(tail_ratio, max_ratio)

        # 回撤触发（叠加）
        if portfolio_drawdown_60d is not None and portfolio_drawdown_60d > dd_trigger:
            dd_excess = portfolio_drawdown_60d - dd_trigger
            dd_tail = min_ratio + dd_excess * 3.0
            dd_tail = min(dd_tail, max_ratio)
            tail_ratio = max(tail_ratio, dd_tail)

        # 融合: 取Beta中性(上限)和尾部保护的最小值
        # 尾保模式: 仅在极端行情激活, 日常不打扰
        final_ratio = min(beta_neutral_ratio + tail_ratio * 0.5, max(beta_neutral_ratio, tail_ratio))

        # 成本效益过滤
        if hedge_strength == HedgeSignalStrength.NO_HEDGE:
            return 0.0

        # 对冲成本估算(年化)
        hedge_cost_annual = final_ratio * (HEDGE_ROLL_COST_ANNUAL + HEDGE_MARGIN_OPP_COST)
        # 预期收益(仅尾保部分做减法)
        expected_benefit = tail_ratio * 0.08  # 尾保预期降低8%*ratio的回撤

        if expected_benefit < hedge_cost_annual * COST_BENEFIT_THRESHOLD and tail_ratio < 0.10:
            logger.info(f"[成本效益] 对冲预期收益{expected_benefit*100:.1f}% < "
                       f"成本{hedge_cost_annual*100:.1f}% * {COST_BENEFIT_THRESHOLD}, 降为0")
            return 0.0

        return min(final_ratio, 1.0)

    # ── v5.9 多指数Beta加权期货对冲 ──

    def generate_futures_hedge(
        self,
        risk: PortfolioRisk,
        hedge_ratio: float,
        futures_prices: Dict[str, float] = None,
    ) -> Dict[str, Any]:
        """v5.9 多指数Beta加权对冲方案

        IC/IM/IF按组合Beta比例分配, 不再单一依赖IF。
        """
        if hedge_ratio <= 0:
            return {"contracts": {}, "total_notional": 0, "total_margin": 0,
                    "reason": "对冲比率=0, 无需求", "price_source": "N/A", "fallback_used": []}

        price_source = "user_provided"
        if not futures_prices:
            futures_prices = get_live_futures_prices()
            price_source = "auto"

        fallback_used = []
        for code in ["IF", "IC", "IM", "IH"]:
            if code in futures_prices and abs(futures_prices[code] - DEFAULT_FUTURES_PRICES.get(code, 0)) < 0.1:
                fallback_used.append(code)

        if fallback_used:
            logger.warning(f"[!] 回退价格品种: {', '.join(fallback_used)}")

        # ── v5.9 多指数Beta加权分配 ──
        beta_map = {
            "IC": risk.beta_csi500,
            "IM": risk.beta_csi1000,
            "IF": risk.beta_csi300,
        }

        # 计算每个指数的对冲分配权重
        total_beta = sum(max(b, 0) for b in beta_map.values())
        if total_beta <= 0:
            return {"contracts": {}, "total_notional": 0, "total_margin": 0,
                    "reason": "组合Beta<=0, 无需对冲", "price_source": price_source, "fallback_used": fallback_used}

        hedge_notional_total = risk.stock_exposure * hedge_ratio

        result = {}
        remaining = hedge_notional_total

        # 按Beta比例分配, 优先IC和IM
        allocation_order = ["IC", "IM", "IF"]
        for code in allocation_order:
            beta = max(beta_map[code], 0)
            if beta <= 0 or remaining <= 0:
                continue

            spec = INDEX_FUTURES_SPECS[code]
            price = futures_prices.get(code, 0)
            multiplier = spec["multiplier"]

            if price <= 0:
                continue

            # 该指数分配的名义价值 = 总对冲 * (该指数Beta/总Beta)
            alloc_ratio = beta / total_beta
            alloc_notional = hedge_notional_total * alloc_ratio

            contract_value = price * multiplier
            contracts = max(1, round(alloc_notional / contract_value))
            notional = contracts * contract_value
            margin = notional * spec["margin_pct"]

            result[code] = {
                "contracts": contracts,
                "price": price,
                "notional": notional,
                "margin": margin,
                "spec": spec,
                "direction": "SELL",
                "alloc_ratio": alloc_ratio,
            }

            remaining -= notional

        total_notional = sum(v["notional"] for v in result.values())
        total_margin = sum(v["margin"] for v in result.values())

        # 生成理由
        parts = []
        if risk.beta_csi500 > 1.2:
            parts.append(f"CSI500 Beta={risk.beta_csi500:.2f}")
        if risk.beta_csi1000 > 1.2:
            parts.append(f"CSI1000 Beta={risk.beta_csi1000:.2f}")
        if risk.beta_csi300 > 1.0:
            parts.append(f"CSI300 Beta={risk.beta_csi300:.2f}")

        for code, detail in result.items():
            spec = detail.get("spec", {})
            name = spec.get("name", code)
            n = detail["contracts"]
            parts.append(f"做空{n}手{name}")

        reason = "; ".join(parts) if parts else f"多指数对冲{hedge_ratio*100:.0f}%敞口"
        if fallback_used:
            reason += f" [!]{','.join(fallback_used)}为回退价格"

        return {
            "contracts": result,
            "total_notional": total_notional,
            "total_margin": total_margin,
            "target_hedge_notional": hedge_notional_total,
            "reason": reason,
            "price_source": price_source,
            "fallback_used": fallback_used,
        }

    # ── 期权对冲（保留原实现） ──

    def generate_options_hedge(
        self, risk: PortfolioRisk, hedge_ratio: float,
        options_data: Dict[str, Any] = None, strategy: str = "protective_put",
    ) -> Dict[str, Any]:
        if hedge_ratio <= 0:
            return {"contracts": [], "total_cost": 0, "reason": "对冲比率=0"}

        default_iv = 0.22
        underlying = "510300" if risk.beta_csi300 > risk.beta_csi500 else "510050"
        index_price = 3950 if underlying == "510300" else 2700
        hedge_notional = risk.stock_exposure * hedge_ratio

        if strategy == "protective_put":
            T = 1/12
            atm_put_premium_pct = 0.4 * default_iv * math.sqrt(T)
            atm_put_premium = index_price * atm_put_premium_pct
            multiplier = ETF_OPTIONS_SPECS[underlying]["multiplier"]
            one_contract_hedge = index_price * multiplier
            contracts = max(1, round(hedge_notional / one_contract_hedge))
            total_premium = contracts * atm_put_premium * multiplier

            return {
                "strategy": "protective_put", "underlying": underlying,
                "contracts": contracts, "strike_type": "ATM",
                "estimated_premium_pct": round(atm_put_premium_pct * 100, 2),
                "total_premium": round(total_premium, 0),
                "total_premium_pct": round(total_premium / risk.total_value * 100, 2) if risk.total_value > 0 else 0,
                "max_protection": round(hedge_notional, 0),
                "reason": f"保护性看跌: {contracts}手{underlying} ATM Put, 权利金{total_premium:,.0f}元",
                "risk": "最大损失=权利金",
            }

        elif strategy == "collar":
            T = 1/12
            atm_put_pct = 0.4 * default_iv * math.sqrt(T)
            otm_put_pct = atm_put_pct * 0.7
            otm_call_pct = atm_put_pct * 0.8
            net_cost_pct = otm_put_pct - otm_call_pct
            multiplier = ETF_OPTIONS_SPECS[underlying]["multiplier"]
            one_contract_hedge = index_price * multiplier
            contracts = max(1, round(hedge_notional / one_contract_hedge))
            net_cost = contracts * net_cost_pct * index_price * multiplier

            return {
                "strategy": "collar", "underlying": underlying,
                "contracts": contracts, "net_cost": round(net_cost, 0),
                "put_strike": f"{index_price*0.95:.0f} (OTM 95%)",
                "call_strike": f"{index_price*1.05:.0f} (OTM 105%)",
                "net_cost_pct": round(net_cost / risk.total_value * 100, 2) if risk.total_value > 0 else 0,
                "reason": f"领口: {contracts}手, 净成本{net_cost:,.0f}元",
                "risk": "上行收益封顶+5%",
            }

        elif strategy == "put_spread":
            T = 1/12
            atm_put_pct = 0.4 * default_iv * math.sqrt(T)
            sell_otm_put_pct = atm_put_pct * 0.45
            spread_cost_pct = atm_put_pct - sell_otm_put_pct
            multiplier = ETF_OPTIONS_SPECS[underlying]["multiplier"]
            one_contract_hedge = index_price * multiplier
            contracts = max(1, round(hedge_notional / one_contract_hedge))
            spread_cost = contracts * spread_cost_pct * index_price * multiplier

            return {
                "strategy": "put_spread", "underlying": underlying,
                "contracts": contracts, "spread_cost": round(spread_cost, 0),
                "buy_put_strike": f"{index_price:.0f} (ATM)",
                "sell_put_strike": f"{index_price*0.90:.0f} (OTM 90%)",
                "max_profit": round(hedge_notional * 0.10, 0),
                "reason": f"看跌价差: {contracts}手, 成本{spread_cost:,.0f}元",
                "risk": "保护10%跌幅",
            }

        return {"contracts": [], "total_cost": 0, "reason": "未知策略"}

    def generate_hedge_plan(
        self, risk: PortfolioRisk, market_signals: Dict[str, Any] = None,
        futures_prices: Dict[str, float] = None, prefer_options: bool = False,
        portfolio_volatility: float = None, portfolio_drawdown_60d: float = None,
        positions: Dict[str, Dict[str, Any]] = None,
        prices: Dict[str, float] = None,
    ) -> HedgeRecommendation:
        """v5.10 完整对冲方案 — 组合自触发增强 + P0-8压力测试"""
        recommendation = HedgeRecommendation()
        recommendation.timestamp = datetime.now().isoformat()
        recommendation.risk_signals = market_signals or {}

        # v5.10 P0-8: 历史极端压力测试 (始终运行)
        if positions and prices:
            recommendation.stress_tests = self.run_historical_stress_tests(positions, prices)

        # v5.10 P0-6: 板块集中度 + MRC 预警
        if risk.sector_concentration_warning:
            recommendation.sector_warnings.append(risk.sector_concentration_warning)
        recommendation.mrc_warnings = risk.mrc_warnings

        # v5.10 P0-7: 相关性预警
        recommendation.correlation_warning = risk.correlation_warning

        strength, score = self.determine_hedge_signal_strength(
            risk, market_signals, portfolio_volatility, portfolio_drawdown_60d
        )
        recommendation.strength = strength
        recommendation.urgency_score = score

        if strength == HedgeSignalStrength.NO_HEDGE:
            recommendation.hedge_type = HedgeType.NONE
            recommendation.reasoning = "组合风险可控(自波动率+回撤均在安全范围), 无需对冲"
            return recommendation

        recommendation.hedge_type = HedgeType.PUT_PROTECTIVE if prefer_options else HedgeType.FUTURES_SHORT
        hedge_ratio = self.compute_optimal_hedge_ratio(
            risk, strength, method="min_variance",
            portfolio_volatility=portfolio_volatility,
            portfolio_drawdown_60d=portfolio_drawdown_60d,
        )
        recommendation.hedge_ratio = hedge_ratio

        if hedge_ratio <= 0:
            recommendation.hedge_type = HedgeType.NONE
            recommendation.reasoning = "对冲经成本效益分析后判定不划算(预期收益<1.5倍成本)"
            return recommendation

        if not prefer_options:
            futures_result = self.generate_futures_hedge(risk, hedge_ratio, futures_prices)
            recommendation.futures_instruments = list(futures_result.get("contracts", {}).keys())

            contracts = {}
            notionals = {}
            margins = {}
            for code, detail in futures_result.get("contracts", {}).items():
                contracts[code] = detail["contracts"]
                notionals[code] = detail["notional"]
                margins[code] = detail["margin"]

            recommendation.futures_contracts = contracts
            recommendation.futures_notional = notionals
            recommendation.futures_margin = margins
            recommendation.effective_hedge_pct = (
                futures_result.get("total_notional", 0) / risk.stock_exposure
                if risk.stock_exposure > 0 else 0
            )
            hedge_reason = futures_result.get("reason", "")
        else:
            options_result = self.generate_options_hedge(
                risk, hedge_ratio, strategy="protective_put" if score < 0.5 else "put_spread"
            )
            recommendation.options_instruments = [options_result.get("underlying", "510300")]
            recommendation.options_strategy = options_result.get("strategy", "")
            recommendation.options_contracts = [{
                "underlying": options_result.get("underlying"),
                "contracts": options_result.get("contracts", 0),
                "strategy": options_result.get("strategy"),
                "cost": options_result.get("total_premium", options_result.get("spread_cost", 0)),
            }]
            recommendation.options_cost = options_result.get("total_premium", options_result.get("spread_cost", 0))
            recommendation.effective_hedge_pct = hedge_ratio
            hedge_reason = options_result.get("reason", "")

        max_beta = max(risk.beta_csi300, risk.beta_csi500, risk.beta_csi1000)
        recommendation.expected_beta_after = max_beta * (1 - hedge_ratio)
        recommendation.expected_drawdown_reduce = hedge_ratio * 0.4

        strength_names = {
            HedgeSignalStrength.LIGHT: "轻度",
            HedgeSignalStrength.MODERATE: "中度",
            HedgeSignalStrength.STRONG: "强力",
            HedgeSignalStrength.FULL: "完全",
        }

        vol_info = ""
        if portfolio_volatility and portfolio_volatility > 0:
            vol_info = f"组合波动率={portfolio_volatility*100:.1f}%, "
        dd_info = ""
        if portfolio_drawdown_60d and portfolio_drawdown_60d > 0:
            dd_info = f"60日回撤={portfolio_drawdown_60d*100:.1f}%, "

        recommendation.reasoning = (
            f"{strength_names.get(strength, '')}对冲 (评分={score:.2f})。"
            f"最大Beta={max_beta:.2f}, {vol_info}{dd_info}"
            f"对冲比率={hedge_ratio*100:.0f}%。"
            f"{hedge_reason}"
        )

        return recommendation

    def _generate_hedge_reason(self, risk, hedge_ratio, contracts) -> str:
        parts = []
        for code, detail in contracts.items():
            spec = detail.get("spec", {})
            name = spec.get("name", code)
            n = detail["contracts"]
            parts.append(f"做空{n}手{name}")
        return "; ".join(parts) if parts else f"对冲{hedge_ratio*100:.0f}%股票敞口"

    def format_report(self, recommendation: HedgeRecommendation) -> str:
        lines = []
        lines.append("=" * 70)
        lines.append("  Hedge Engine v5.9 — 对冲策略报告")
        lines.append("=" * 70)
        lines.append(f"  生成时间: {recommendation.timestamp[:19]}")
        lines.append(f"  对冲类型: {recommendation.hedge_type.value}")
        lines.append(f"  信号强度: {recommendation.strength.name} (紧急度={recommendation.urgency_score:.2f})")
        lines.append(f"  对冲比率: {recommendation.hedge_ratio*100:.0f}%")
        lines.append(f"  预期对冲后Beta: {recommendation.expected_beta_after:.2f}")
        lines.append(f"  预期回撤减少: {recommendation.expected_drawdown_reduce*100:.0f}%")
        lines.append("-" * 70)

        if recommendation.futures_contracts:
            lines.append("\n  期货对冲方案 (多指数Beta加权)")
            lines.append("  " + "-" * 50)
            total_margin = 0
            for code, n in recommendation.futures_contracts.items():
                notional = recommendation.futures_notional.get(code, 0)
                margin = recommendation.futures_margin.get(code, 0)
                total_margin += margin
                spec = INDEX_FUTURES_SPECS.get(code, {})
                lines.append(f"    {code} {spec.get('name', '')}: 做空 {n} 手")
                lines.append(f"      名义价值: {notional:,.0f} | 保证金: {margin:,.0f}")
            lines.append(f"\n    总保证金需求: {total_margin:,.0f}")

        if recommendation.options_contracts:
            lines.append("\n  期权对冲方案")
            lines.append("  " + "-" * 50)
            for opt in recommendation.options_contracts:
                lines.append(f"    标的: {opt.get('underlying', '')}")
                lines.append(f"    策略: {opt.get('strategy', '')}")
                lines.append(f"    合约数: {opt.get('contracts', 0)} 张")
                lines.append(f"    预估成本: {opt.get('cost', 0):,.0f}")

        lines.append("\n  对冲逻辑")
        lines.append(f"    {recommendation.reasoning}")

        # v5.10 P0-6: 集中度与板块预警
        if recommendation.sector_warnings or recommendation.mrc_warnings:
            lines.append("\n" + "-" * 70)
            lines.append("  集中度风险监控 (P0-6)")
            lines.append("  " + "-" * 50)
            if recommendation.sector_warnings:
                for w in recommendation.sector_warnings:
                    lines.append(f"  ⚠️  {w}")
            if recommendation.mrc_warnings:
                for w in recommendation.mrc_warnings:
                    lines.append(f"  ⚠️  {w}")
            if not recommendation.sector_warnings and not recommendation.mrc_warnings:
                lines.append("  ✅ 板块和单标的集中度均在安全范围")

        # v5.10 P0-7: 相关性预警
        if recommendation.correlation_warning:
            lines.append("\n" + "-" * 70)
            lines.append("  相关性风险监控 (P0-7)")
            lines.append("  " + "-" * 50)
            lines.append(f"  ⚠️  {recommendation.correlation_warning}")

        # v5.10 P0-8: 历史极端压力测试
        if recommendation.stress_tests:
            lines.append("\n" + "-" * 70)
            lines.append("  历史极端压力测试 (P0-8 修复)")
            lines.append("  " + "-" * 50)
            breach_count = 0
            for scenario, result in recommendation.stress_tests.items():
                breach = "❌ 突破15%回撤上限" if result["breaches_limit"] else "✅ 未触发"
                if result["breaches_limit"]:
                    breach_count += 1
                dd = result["drawdown_pct"]
                loss = result["estimated_loss"]
                lines.append(f"  {scenario}")
                lines.append(f"    预估回撤: {dd:.1f}% | 损失: {loss:,.0f} | {breach}")
                lines.append(f"    情景: {result['sector_impact']}")
            if breach_count > 0:
                lines.append(f"\n  ⚠️  {breach_count}/6 个历史情景突破15%回撤上限，强烈建议启用尾部保护")
            else:
                lines.append(f"\n  全部历史情景均未突破15%回撤上限，尾部保护可选")

        lines.append("\n" + "=" * 70)
        lines.append("  以上分析仅供参考，不构成投资建议。")
        lines.append("=" * 70)

        return "\n".join(lines)

    def get_hedge_signal_for_fusion(self, portfolio_code: str = "portfolio") -> Dict[str, Any]:
        return {
            "code": portfolio_code, "source": "hedge_engine_v59",
            "action": "HOLD", "score": 0.5, "confidence": 0.3,
            "reason": "对冲引擎v5.9已初始化，等待风险评估",
            "timestamp": datetime.now().isoformat(),
        }

    def compute_correlation_matrix(
        self,
        historical_returns: Dict[str, List[float]],
        codes: List[str],
        lookback_days: int = 60,
    ) -> Dict[str, Dict[str, float]]:
        """v5.10 计算组合内资产相关性矩阵 (P0-7修复)

        返回N×N的相关性矩阵，用于实盘风控链路监控。
        当滚动60日平均相关系数>0.7时触发集中度预警。

        Args:
            historical_returns: 各标的历史日收益率
            codes: 标的代码列表
            lookback_days: 回溯天数

        Returns:
            correlation_matrix: 相关性矩阵字典
        """
        available_codes = [c for c in codes if c in historical_returns]
        if len(available_codes) < 2:
            logger.warning("[相关性] 有效标的不足2个，无法计算相关性矩阵")
            return {}

        code_returns = {}
        min_len = float('inf')
        for code in available_codes:
            rets = historical_returns[code]
            if len(rets) < lookback_days:
                continue
            code_returns[code] = rets[-lookback_days:]
            min_len = min(min_len, len(rets[-lookback_days:]))

        if len(code_returns) < 2:
            logger.warning("[相关性] 足够历史数据的标的不足2个")
            return {}

        n = len(code_returns)
        code_list = list(code_returns.keys())

        cov_matrix = [[0.0] * n for _ in range(n)]
        vol_list = [0.0] * n

        for i, ci in enumerate(code_list):
            rets_i = code_returns[ci][-min_len:]
            mean_i = sum(rets_i) / len(rets_i)
            var_i = sum((r - mean_i) ** 2 for r in rets_i) / (len(rets_i) - 1)
            vol_list[i] = math.sqrt(var_i) if var_i > 0 else 0.0001

            for j, cj in enumerate(code_list):
                rets_j = code_returns[cj][-min_len:]
                mean_j = sum(rets_j) / len(rets_j)
                cov_ij = sum(
                    (rets_i[k] - mean_i) * (rets_j[k] - mean_j)
                    for k in range(min_len)
                ) / (min_len - 1)
                cov_matrix[i][j] = cov_ij

        corr_matrix = {}
        for i, ci in enumerate(code_list):
            corr_matrix[ci] = {}
            for j, cj in enumerate(code_list):
                denom = vol_list[i] * vol_list[j]
                if denom > 0:
                    corr_matrix[ci][cj] = round(cov_matrix[i][j] / denom, 4)
                else:
                    corr_matrix[ci][cj] = 0.0 if i == j else 0.0

        return corr_matrix

    def monitor_daily_correlation(
        self,
        positions: Dict[str, Dict[str, Any]],
        historical_returns: Dict[str, List[float]],
        alert_threshold: float = 0.7,
        lookback_days: int = 60,
    ) -> Dict[str, Any]:
        """v5.10 每日相关性监控 (P0-7修复核心)

        监控组合内资产间的相关性变化，当滚动60日平均相关系数>0.7时触发预警。
        同时监控"相关性变化率"，识别危机中所有资产趋向1的情况。

        Args:
            positions: 当前持仓
            historical_returns: 历史收益率数据
            alert_threshold: 预警阈值
            lookback_days: 回溯天数

        Returns:
            monitoring_result: 包含相关性矩阵、预警状态、风险评分
        """
        codes = list(positions.keys())
        corr_matrix = self.compute_correlation_matrix(historical_returns, codes, lookback_days)

        if not corr_matrix:
            return {
                "status": "NO_DATA",
                "correlation_matrix": {},
                "average_correlation": 0.0,
                "max_correlation": 0.0,
                "high_correlation_pairs": [],
                "alert": False,
                "alert_reason": "",
                "risk_score": 0.0,
            }

        avg_corr = 0.0
        max_corr = 0.0
        high_corr_pairs = []
        count = 0

        code_list = list(corr_matrix.keys())
        n = len(code_list)

        for i in range(n):
            for j in range(i + 1, n):
                ci = code_list[i]
                cj = code_list[j]
                corr = corr_matrix[ci][cj]
                avg_corr += corr
                count += 1
                if corr > max_corr:
                    max_corr = corr
                if corr > alert_threshold:
                    high_corr_pairs.append((ci, cj, round(corr, 4)))

        avg_corr = avg_corr / count if count > 0 else 0.0

        alert = avg_corr > alert_threshold or max_corr > 0.85
        alert_reason = ""

        if avg_corr > alert_threshold:
            alert_reason = f"组合平均相关系数={avg_corr:.4f}超过阈值{alert_threshold}"
            logger.warning(f"[相关性预警] {alert_reason}")
        if max_corr > 0.85:
            if alert_reason:
                alert_reason += "; "
            alert_reason += f"最高相关系数={max_corr:.4f}>0.85，存在共振风险"
            logger.warning(f"[相关性预警] {alert_reason}")

        risk_score = min(1.0, avg_corr * 1.2 + (max_corr - 0.5) * 0.8)

        return {
            "status": "OK",
            "correlation_matrix": corr_matrix,
            "average_correlation": round(avg_corr, 4),
            "max_correlation": round(max_corr, 4),
            "high_correlation_pairs": high_corr_pairs,
            "alert": alert,
            "alert_reason": alert_reason,
            "risk_score": round(risk_score, 4),
            "lookback_days": lookback_days,
            "timestamp": datetime.now().isoformat(),
        }

    def check_sector_concentration(
        self,
        positions: Dict[str, Dict[str, Any]],
        historical_returns: Dict[str, List[float]],
        lookback_days: int = 60,
    ) -> Dict[str, Any]:
        """v5.10 板块集中度风险检查 (P0-6/P0-7联动)

        检查同一板块内标的的相关性是否过高，识别"伪分散化"风险。
        """
        sectors = {}
        for code, pos in positions.items():
            sector = pos.get('category', 'unknown')
            if sector not in sectors:
                sectors[sector] = []
            sectors[sector].append(code)

        sector_risks = {}
        overall_risk = 0.0
        alert_sectors = []

        for sector, codes in sectors.items():
            if len(codes) < 2:
                sector_risks[sector] = {"risk": 0.0, "reason": "标的不足"}
                continue

            corr_result = self.monitor_daily_correlation(
                {c: positions[c] for c in codes},
                historical_returns,
                lookback_days=lookback_days,
            )

            sector_risks[sector] = {
                "codes": codes,
                "count": len(codes),
                "average_correlation": corr_result["average_correlation"],
                "risk": corr_result["risk_score"],
                "alert": corr_result["alert"],
            }

            if corr_result["alert"]:
                alert_sectors.append(sector)
                overall_risk += corr_result["risk_score"] * (len(codes) / len(positions))

        return {
            "sector_concentration": sector_risks,
            "alert_sectors": alert_sectors,
            "overall_concentration_risk": round(overall_risk, 4),
            "timestamp": datetime.now().isoformat(),
        }


# ── 便捷函数 ──

def get_hedge_engine(portfolio_value: float = None) -> HedgeEngine:
    return HedgeEngine(portfolio_value=portfolio_value or 1_000_000)


def calculate_portfolio_beta(
    positions: Dict[str, Dict[str, Any]], prices: Dict[str, float],
) -> Dict[str, float]:
    engine = HedgeEngine()
    risk = engine.assess_portfolio_risk(positions, prices)
    return {
        "beta_csi300": risk.beta_csi300, "beta_csi500": risk.beta_csi500,
        "beta_csi1000": risk.beta_csi1000, "beta_sse50": risk.beta_sse50,
        "concentration_hhi": risk.concentration_risk, "var_95_daily": risk.var_95_daily,
    }
