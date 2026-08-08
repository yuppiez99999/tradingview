"""T4.3 整合 managers.py — 组合优化/大宗/ETF 统一管理外观.

对冲基金 L3 Alpha + L7 归因层三合一日级面板的核心管理器:
  - PortfolioManager: 组合优化 (复用 BlackLittermanOptimizer, 不重复造轮子)
  - CommodityManager: 大宗商品监控 (新建, 支持铜/金/原油/铁矿石等)
  - ETFFlowManager: ETF 资金流监控 (复用 ETFRealTimeTracker)
  - AttributionManagersFacade: 统一外观入口

设计原则:
  - Facade 模式: 不修改现有 black_litterman_optimizer.py / etf_flow_monitor.py
  - 复用优先: BL 优化器和 ETF 监控器已存在, 仅包装暴露统一接口
  - 大宗商品新建: 参考 test_copper_*.py 脚本扩展到铜/金/原油
  - HC-1 Feature Flag 透传: USE_ATTRIBUTION_MANAGERS 默认 False
  - HC-5 ConfigManager 4 级优先级: v8.3_institutional/config/attribution_managers.yaml

用法:
    from utils.attribution.managers import AttributionManagersFacade
    facade = AttributionManagersFacade()
    # 组合优化
    bl_result = facade.optimize_portfolio(assets, weights, cov_matrix, views)
    # 大宗商品监控
    commodity_summary = facade.get_commodity_summary()
    # ETF 资金流
    etf_signals = facade.get_etf_signals()
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# ============================================================
# 默认配置常量
# ============================================================
DEFAULT_RISK_AVERSION = 2.5
DEFAULT_TAU = 0.05
DEFAULT_CONFIDENCE = 0.5
DEFAULT_COMMODITY_LOOKBACK_DAYS = 30

# 大宗商品监控阈值 (日涨跌幅 %)
COMMODITY_VOLATILITY_THRESHOLD = 3.0  # 日波动 > 3% 触发预警
COMMODITY_TREND_THRESHOLD = 5.0  # 累计涨跌 > 5% 触发趋势信号

# 支持的大宗商品列表 (参考 scripts/test_copper_*.py 和 v8.3 futures_scan.py)
SUPPORTED_COMMODITIES = [
    {"code": "CU", "name": "铜", "exchange": "SHFE", "unit": "吨"},
    {"code": "AU", "name": "黄金", "exchange": "SHFE", "unit": "克"},
    {"code": "AG", "name": "白银", "exchange": "SHFE", "unit": "千克"},
    {"code": "SC", "name": "原油", "exchange": "INE", "unit": "桶"},
    {"code": "I", "name": "铁矿石", "exchange": "DCE", "unit": "吨"},
    {"code": "RB", "name": "螺纹钢", "exchange": "SHFE", "unit": "吨"},
    {"code": "M", "name": "豆粕", "exchange": "DCE", "unit": "吨"},
    {"code": "Y", "name": "豆油", "exchange": "DCE", "unit": "吨"},
]


# ============================================================
# 异常体系
# ============================================================
class ManagersError(Exception):
    """managers 模块基础异常."""


class PortfolioOptimizationError(ManagersError):
    """组合优化异常."""


class CommodityMonitorError(ManagersError):
    """大宗商品监控异常."""


class ETFFlowError(ManagersError):
    """ETF 资金流异常."""


# ============================================================
# 数据类
# ============================================================
@dataclass
class CommoditySnapshot:
    """大宗商品快照.

    Attributes:
        code: 合约代码 (如 CU/AU/SC)
        name: 商品名称
        exchange: 交易所
        price: 当前价格
        change_pct: 日涨跌幅 (小数, 如 0.02 = 2%)
        cumulative_return: lookback 累计涨跌幅
        volatility: lookback 日均波动率
        signal: 信号 (OVERBOUGHT/OVERSOLD/NEUTRAL/TREND_UP/TREND_DOWN)
        timestamp: 数据时间戳 (ISO 格式)
    """

    code: str
    name: str
    exchange: str
    price: float = 0.0
    change_pct: float = 0.0
    cumulative_return: float = 0.0
    volatility: float = 0.0
    signal: str = "NEUTRAL"
    timestamp: str = ""


@dataclass
class ManagersReport:
    """managers 统一报告.

    Attributes:
        portfolio_optimization: 组合优化结果 (BLResult 字典或 None)
        commodity_summary: 大宗商品监控汇总
        etf_flow_summary: ETF 资金流汇总
        errors: 错误列表 (非阻断性错误)
        timestamp: 报告时间戳
    """

    portfolio_optimization: dict[str, Any] | None = None
    commodity_summary: dict[str, Any] = field(default_factory=dict)
    etf_flow_summary: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    timestamp: str = ""


# ============================================================
# 1. PortfolioManager — 组合优化管理器
# ============================================================
class PortfolioManager:
    """组合优化管理器 (Facade 包装 BlackLittermanOptimizer).

    复用现有 black_litterman_optimizer.py 的 BlackLittermanOptimizer 类,
    提供统一的组合优化入口, 不重复造轮子.

    用法:
        pm = PortfolioManager(risk_aversion=2.5)
        result = pm.optimize(
            assets=["510050", "510300", "588080"],
            market_weights=[0.3, 0.4, 0.3],
            cov_matrix=cov_matrix_numpy,
            views=[View(type="absolute", assets=["510050"],
                       weights=[1.0], expected_return=0.05)],
        )
    """

    def __init__(
        self,
        risk_aversion: float = DEFAULT_RISK_AVERSION,
        tau: float = DEFAULT_TAU,
        default_confidence: float = DEFAULT_CONFIDENCE,
        use_idzorek_omega: bool = True,
    ) -> None:
        self.risk_aversion = float(risk_aversion)
        self.tau = float(tau)
        self.default_confidence = float(default_confidence)
        self.use_idzorek_omega = bool(use_idzorek_omega)
        self._optimizer: Any | None = None

    def _get_optimizer(self) -> Any:
        """懒加载 BlackLittermanOptimizer (避免 import 时硬依赖)."""
        if self._optimizer is None:
            try:
                from utils.black_litterman_optimizer import BlackLittermanOptimizer
            except ImportError as e:
                raise PortfolioOptimizationError(f"无法导入 BlackLittermanOptimizer: {e}") from e
            self._optimizer = BlackLittermanOptimizer(
                risk_aversion=self.risk_aversion,
                tau=self.tau,
                default_confidence=self.default_confidence,
                use_idzorek_omega=self.use_idzorek_omega,
            )
        return self._optimizer

    def optimize(
        self,
        assets: list[str],
        market_weights: list[float] | np.ndarray,
        cov_matrix: np.ndarray | Any,
        views: list[Any] | None = None,
        risk_free_rate: float = 0.03,
        target_return: float | None = None,
        max_weight: float | None = None,
        min_weight: float = 0.0,
    ) -> Any:
        """执行 Black-Litterman 组合优化.

        Args:
            assets: 标的代码列表
            market_weights: 市场权重 (市值加权)
            cov_matrix: 协方差矩阵 (n*n numpy array 或 pandas DataFrame)
            views: 投资者主观观点列表 (View 对象)
            risk_free_rate: 无风险利率 (默认 0.03)
            target_return: 目标收益 (可选, None 表示不约束)
            max_weight: 单标的最大权重 (可选)
            min_weight: 单标的 最小权重 (默认 0)

        Returns:
            BLResult 对象 (含 posterior_returns/optimal_weights/sharpe_ratio 等)

        Raises:
            PortfolioOptimizationError: 优化失败
        """
        try:
            optimizer = self._get_optimizer()
            result = optimizer.optimize(
                assets=assets,
                market_weights=market_weights,
                cov_matrix=cov_matrix,
                views=views,
                risk_free_rate=risk_free_rate,
                target_return=target_return,
                max_weight=max_weight,
                min_weight=min_weight,
            )
            logger.info(
                f"[PortfolioManager] BL 优化完成: "
                f"n_assets={len(assets)}, "
                f"sharpe={result.sharpe_ratio:.3f}, "
                f"effective_n={result.effective_n:.2f}"
            )
            return result
        except Exception as e:  # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化
            raise PortfolioOptimizationError(f"Black-Litterman 优化失败: {e}") from e

    def save_result(self, result: Any, path: str | Any) -> Any:
        """保存优化结果到文件.

        Args:
            result: BLResult 对象
            path: 文件路径

        Returns:
            保存的文件路径 (Path 对象)
        """
        optimizer = self._get_optimizer()
        return optimizer.save_result(result, path)


# ============================================================
# 2. CommodityManager — 大宗商品监控管理器
# ============================================================
class CommodityManager:
    """大宗商品监控管理器 (新建模块).

    监控铜/金/原油/铁矿石等大宗商品的价格和波动,
    生成趋势信号和波动预警, 为组合优化和宏观分析提供输入.

    设计参考:
      - scripts/test_copper_actual.py (铜数据获取)
      - v8.3_institutional/src/derivatives/futures_scan.py (期货扫描)
      - v8.3_institutional/src/macro/kondratiev.py (康波周期)

    用法:
        cm = CommodityManager()
        snapshot = cm.get_snapshot("CU")
        summary = cm.get_summary()
    """

    def __init__(
        self,
        lookback_days: int = DEFAULT_COMMODITY_LOOKBACK_DAYS,
        volatility_threshold: float = COMMODITY_VOLATILITY_THRESHOLD,
        trend_threshold: float = COMMODITY_TREND_THRESHOLD,
    ) -> None:
        self.lookback_days = int(lookback_days)
        self.volatility_threshold = float(volatility_threshold)
        self.trend_threshold = float(trend_threshold)
        self._supported_codes: dict[str, dict[str, str]] = {c["code"]: c for c in SUPPORTED_COMMODITIES}

    def list_supported(self) -> list[dict[str, str]]:
        """列出支持的大宗商品.

        Returns:
            [{"code": "CU", "name": "铜", "exchange": "SHFE", "unit": "吨"}, ...]
        """
        return list(self._supported_codes.values())

    def is_supported(self, code: str) -> bool:
        """检查商品代码是否支持."""
        return code.upper() in self._supported_codes

    def get_snapshot(
        self,
        code: str,
        price: float = 0.0,
        change_pct: float = 0.0,
        historical_prices: list[float] | np.ndarray | None = None,
        timestamp: str = "",
    ) -> CommoditySnapshot:
        """生成大宗商品快照 (基于输入数据, 不主动拉取).

        注: 实盘数据获取由上层调用者负责 (避免网络依赖),
        本方法仅做信号生成和阈值判断.

        Args:
            code: 商品代码 (如 CU/AU/SC)
            price: 当前价格
            change_pct: 日涨跌幅 (小数, 如 0.02 = 2%)
            historical_prices: 历史价格序列 (用于计算波动率和累计收益)
            timestamp: 数据时间戳 (ISO 格式)

        Returns:
            CommoditySnapshot 对象

        Raises:
            CommodityMonitorError: 商品代码不支持
        """
        code = code.upper()
        if not self.is_supported(code):
            raise CommodityMonitorError(f"不支持的商品代码: {code}, 支持: {list(self._supported_codes.keys())}")

        info = self._supported_codes[code]

        # 计算累计收益和波动率
        cumulative_return = change_pct
        volatility = abs(change_pct) * 100  # 简化: 日波动 = |涨跌幅|

        if historical_prices is not None and len(historical_prices) >= 2:
            prices_arr = np.asarray(historical_prices, dtype=float)
            returns = np.diff(prices_arr) / prices_arr[:-1]
            cumulative_return = float((prices_arr[-1] / prices_arr[0]) - 1.0)
            volatility = float(np.std(returns) * 100) if len(returns) > 0 else 0.0

        # 信号判断
        signal = self._detect_signal(change_pct, cumulative_return, volatility)

        return CommoditySnapshot(
            code=code,
            name=info["name"],
            exchange=info["exchange"],
            price=float(price),
            change_pct=float(change_pct),
            cumulative_return=cumulative_return,
            volatility=volatility,
            signal=signal,
            timestamp=timestamp,
        )

    def _detect_signal(
        self,
        change_pct: float,
        cumulative_return: float,
        volatility: float,
    ) -> str:
        """根据涨跌幅/累计收益/波动率生成信号.

        信号类型:
          - OVERBOUGHT: 日涨幅 > 趋势阈值 (超买)
          - OVERSOLD: 日跌幅 > 趋势阈值 (超卖)
          - TREND_UP: 累计收益 > 趋势阈值 (上涨趋势)
          - TREND_DOWN: 累计收益 < -趋势阈值 (下跌趋势)
          - HIGH_VOLATILITY: 波动率 > 波动阈值 (高波动)
          - NEUTRAL: 无信号
        """
        change_pct_pct = change_pct * 100 if abs(change_pct) < 1 else change_pct
        if change_pct_pct > self.trend_threshold:
            return "OVERBOUGHT"
        if change_pct_pct < -self.trend_threshold:
            return "OVERSOLD"
        if cumulative_return > self.trend_threshold / 100:
            return "TREND_UP"
        if cumulative_return < -self.trend_threshold / 100:
            return "TREND_DOWN"
        if volatility > self.volatility_threshold:
            return "HIGH_VOLATILITY"
        return "NEUTRAL"

    def get_summary(
        self,
        snapshots: list[CommoditySnapshot] | None = None,
    ) -> dict[str, Any]:
        """生成大宗商品监控汇总.

        Args:
            snapshots: 已生成的快照列表 (None 表示无数据)

        Returns:
            {
                "total": 8,
                "signals": {"OVERBOUGHT": 0, "TREND_UP": 2, ...},
                "high_volatility": ["CU", "SC"],
                "trending_up": ["AU"],
                "trending_down": ["I"],
                "snapshots": [...],
            }
        """
        if snapshots is None:
            snapshots = []

        signals_count: dict[str, int] = {}
        high_volatility: list[str] = []
        trending_up: list[str] = []
        trending_down: list[str] = []

        for snap in snapshots:
            signals_count[snap.signal] = signals_count.get(snap.signal, 0) + 1
            if snap.signal == "HIGH_VOLATILITY" or snap.volatility > self.volatility_threshold:
                high_volatility.append(snap.code)
            if snap.signal == "TREND_UP":
                trending_up.append(snap.code)
            elif snap.signal == "TREND_DOWN":
                trending_down.append(snap.code)

        return {
            "total": len(snapshots),
            "signals": signals_count,
            "high_volatility": high_volatility,
            "trending_up": trending_up,
            "trending_down": trending_down,
            "snapshots": [
                {
                    "code": s.code,
                    "name": s.name,
                    "price": s.price,
                    "change_pct": s.change_pct,
                    "cumulative_return": s.cumulative_return,
                    "volatility": s.volatility,
                    "signal": s.signal,
                    "timestamp": s.timestamp,
                }
                for s in snapshots
            ],
        }


# ============================================================
# 3. ETFFlowManager — ETF 资金流管理器
# ============================================================
class ETFFlowManager:
    """ETF 资金流管理器 (Facade 包装 ETFRealTimeTracker).

    复用现有 etf_flow_monitor.py 的 ETFRealTimeTracker 类,
    提供统一的 ETF 资金流监控入口.

    用法:
        em = ETFFlowManager()
        flows = em.get_all_flows()
        signals = em.detect_signals(flow_data)
        summary = em.get_signal_summary(flow_data)
    """

    def __init__(self) -> None:
        self._tracker: Any | None = None

    def _get_tracker(self) -> Any:
        """懒加载 ETFRealTimeTracker (避免 import 时硬依赖)."""
        if self._tracker is None:
            try:
                from utils.etf_flow_monitor import ETFRealTimeTracker
            except ImportError as e:
                raise ETFFlowError(f"无法导入 ETFRealTimeTracker: {e}") from e
            self._tracker = ETFRealTimeTracker()
        return self._tracker

    def get_all_flows(self) -> dict[str, dict]:
        """获取所有 ETF 资金流数据.

        Returns:
            {etf_code: {flow_data...}, ...}

        Raises:
            ETFFlowError: 数据获取失败
        """
        try:
            tracker = self._get_tracker()
            return tracker.get_all_etf_fund_flows()  # type: ignore[misc]
        except Exception as e:  # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化
            raise ETFFlowError(f"ETF 资金流获取失败: {e}") from e

    def get_flow(self, etf_code: str) -> dict | None:
        """获取单只 ETF 资金流数据.

        Args:
            etf_code: ETF 代码 (如 "510050")

        Returns:
            资金流数据字典, 失败返回 None
        """
        try:
            tracker = self._get_tracker()
            return tracker.get_etf_fund_flow(etf_code)  # type: ignore[misc]
        except Exception as e:  # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化
            logger.warning(f"[ETFFlowManager] 获取 {etf_code} 资金流失败: {e}")
            return None

    def detect_signals(self, flow_data: dict) -> list[dict]:
        """检测 ETF 资金流信号.

        Args:
            flow_data: 资金流数据字典

        Returns:
            信号列表 [{"type": "HIGH", "strength": 50.5, ...}, ...]
        """
        try:
            tracker = self._get_tracker()
            return tracker.detect_signals(flow_data)  # type: ignore[misc]
        except Exception as e:  # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化
            logger.warning(f"[ETFFlowManager] 信号检测失败: {e}")
            return []

    def get_signal_summary(self, flow_data: dict) -> dict:
        """获取 ETF 资金流信号汇总.

        Args:
            flow_data: 资金流数据字典

        Returns:
            {"signal_count": 3, "high_signals": 1, "medium_signals": 2, ...}
        """
        try:
            tracker = self._get_tracker()
            return tracker.get_signal_summary(flow_data)  # type: ignore[misc]
        except Exception as e:  # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化
            logger.warning(f"[ETFFlowManager] 汇总失败: {e}")
            return {"error": str(e)}

    def get_summary(self) -> dict[str, Any]:
        """获取全市场 ETF 资金流汇总 (便捷方法).

        Returns:
            {"total_etfs": 13, "total_inflow": 100.5,
             "high_signals": [...], "summary_by_etf": {...}}
        """
        try:
            flows = self.get_all_flows()
            total_etfs = len(flows)
            total_inflow = 0.0
            high_signals: list[str] = []
            summary_by_etf: dict[str, Any] = {}

            for code, flow_data in flows.items():
                if flow_data is None:
                    continue
                signals = self.detect_signals(flow_data)
                signal_summary = self.get_signal_summary(flow_data)
                summary_by_etf[code] = signal_summary

                for sig in signals:
                    if isinstance(sig, dict) and sig.get("type") == "HIGH":
                        high_signals.append(code)
                    if isinstance(sig, dict):
                        total_inflow += float(sig.get("strength", 0))

            return {
                "total_etfs": total_etfs,
                "total_inflow": total_inflow,
                "high_signals": high_signals,
                "summary_by_etf": summary_by_etf,
            }
        except ETFFlowError as e:
            return {"error": str(e)}


# ============================================================
# 4. AttributionManagersFacade — 统一外观入口
# ============================================================
class AttributionManagersFacade:
    """managers 统一外观入口.

    聚合 PortfolioManager + CommodityManager + ETFFlowManager,
    提供单点入口, 支持一键生成完整 managers 报告.

    Feature Flag 透传 (HC-1):
        USE_ATTRIBUTION_MANAGERS=False (默认) 时降级为兼容模式,
        仅返回空报告, 不触发实际计算.

    用法:
        facade = AttributionManagersFacade()
        # 单独调用
        bl_result = facade.portfolio.optimize(...)
        commodity_summary = facade.commodity.get_summary()
        etf_summary = facade.etf_flow.get_summary()
        # 统一报告
        report = facade.generate_report(...)
    """

    def __init__(
        self,
        risk_aversion: float = DEFAULT_RISK_AVERSION,
        commodity_lookback_days: int = DEFAULT_COMMODITY_LOOKBACK_DAYS,
        enable_portfolio: bool = True,
        enable_commodity: bool = True,
        enable_etf_flow: bool = True,
    ) -> None:
        self._portfolio: PortfolioManager | None = None
        self._commodity: CommodityManager | None = None
        self._etf_flow: ETFFlowManager | None = None

        if enable_portfolio:
            self._portfolio = PortfolioManager(risk_aversion=risk_aversion)
        if enable_commodity:
            self._commodity = CommodityManager(lookback_days=commodity_lookback_days)
        if enable_etf_flow:
            self._etf_flow = ETFFlowManager()

    @property
    def portfolio(self) -> PortfolioManager:
        """组合优化管理器."""
        if self._portfolio is None:
            raise ManagersError("PortfolioManager 未启用")
        return self._portfolio

    @property
    def commodity(self) -> CommodityManager:
        """大宗商品管理器."""
        if self._commodity is None:
            raise ManagersError("CommodityManager 未启用")
        return self._commodity

    @property
    def etf_flow(self) -> ETFFlowManager:
        """ETF 资金流管理器."""
        if self._etf_flow is None:
            raise ManagersError("ETFFlowManager 未启用")
        return self._etf_flow

    def optimize_portfolio(
        self,
        assets: list[str],
        market_weights: list[float] | np.ndarray,
        cov_matrix: np.ndarray | Any,
        views: list[Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        """执行组合优化 (委托给 PortfolioManager)."""
        return self.portfolio.optimize(
            assets=assets,
            market_weights=market_weights,
            cov_matrix=cov_matrix,
            views=views,
            **kwargs,
        )

    def get_commodity_snapshot(
        self,
        code: str,
        price: float = 0.0,
        change_pct: float = 0.0,
        historical_prices: list[float] | np.ndarray | None = None,
        timestamp: str = "",
    ) -> CommoditySnapshot:
        """获取大宗商品快照 (委托给 CommodityManager)."""
        return self.commodity.get_snapshot(
            code=code,
            price=price,
            change_pct=change_pct,
            historical_prices=historical_prices,
            timestamp=timestamp,
        )

    def get_commodity_summary(
        self,
        snapshots: list[CommoditySnapshot] | None = None,
    ) -> dict[str, Any]:
        """获取大宗商品汇总 (委托给 CommodityManager)."""
        return self.commodity.get_summary(snapshots)

    def get_etf_signals(self) -> dict[str, Any]:
        """获取 ETF 资金流信号汇总 (委托给 ETFFlowManager)."""
        return self.etf_flow.get_summary()

    def generate_report(
        self,
        portfolio_config: dict[str, Any] | None = None,
        commodity_snapshots: list[CommoditySnapshot] | None = None,
        timestamp: str = "",
    ) -> ManagersReport:
        """生成 managers 统一报告.

        Args:
            portfolio_config: 组合优化配置 (assets/market_weights/cov_matrix/views)
            commodity_snapshots: 大宗商品快照列表
            timestamp: 报告时间戳

        Returns:
            ManagersReport 对象
        """
        report = ManagersReport(timestamp=timestamp)
        errors: list[str] = []

        # 1. 组合优化
        if self._portfolio is not None and portfolio_config is not None:
            try:
                result = self.portfolio.optimize(**portfolio_config)
                report.portfolio_optimization = {
                    "assets": list(getattr(result, "assets", [])),
                    "optimal_weights": (
                        getattr(result, "optimal_weights", np.array([])).tolist()
                        if hasattr(getattr(result, "optimal_weights", None), "tolist")
                        else list(getattr(result, "optimal_weights", []))
                    ),
                    "sharpe_ratio": float(getattr(result, "sharpe_ratio", 0.0)),
                    "expected_portfolio_return": float(getattr(result, "expected_portfolio_return", 0.0)),
                    "expected_portfolio_vol": float(getattr(result, "expected_portfolio_vol", 0.0)),
                    "effective_n": float(getattr(result, "effective_n", 0.0)),
                    "diversification_ratio": float(getattr(result, "diversification_ratio", 0.0)),
                }
            except Exception as e:  # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化
                errors.append(f"portfolio_optimization: {e}")

        # 2. 大宗商品
        if self._commodity is not None:
            try:
                report.commodity_summary = self.commodity.get_summary(commodity_snapshots)
            except Exception as e:  # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化
                errors.append(f"commodity_summary: {e}")

        # 3. ETF 资金流
        if self._etf_flow is not None:
            try:
                report.etf_flow_summary = self.etf_flow.get_summary()
            except Exception as e:  # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化
                errors.append(f"etf_flow_summary: {e}")

        report.errors = errors
        return report


# ============================================================
# Feature Flag 透传 (HC-1)
# ============================================================
def is_attribution_managers_enabled() -> bool:
    """检查 USE_ATTRIBUTION_MANAGERS Feature Flag 是否启用.

    HC-1 硬约束: Feature Flag 透传, 默认 False (关闭时降级为兼容模式).

    Returns:
        True 启用, False 关闭 (默认)
    """
    try:
        from utils.feature_flags import is_enabled

        return bool(is_enabled("USE_ATTRIBUTION_MANAGERS"))
    except ImportError:
        return False
    except Exception:  # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化
        return False


# ============================================================
# 便捷函数
# ============================================================
def create_default_facade() -> AttributionManagersFacade:
    """创建默认配置的 AttributionManagersFacade.

    Returns:
        AttributionManagersFacade 实例 (全部三个管理器启用)
    """
    return AttributionManagersFacade(
        risk_aversion=DEFAULT_RISK_AVERSION,
        commodity_lookback_days=DEFAULT_COMMODITY_LOOKBACK_DAYS,
        enable_portfolio=True,
        enable_commodity=True,
        enable_etf_flow=True,
    )


def create_commodity_only_facade() -> AttributionManagersFacade:
    """创建仅启用大宗商品监控的 Facade (轻量级).

    Returns:
        AttributionManagersFacade 实例 (仅 CommodityManager 启用)
    """
    return AttributionManagersFacade(
        enable_portfolio=False,
        enable_commodity=True,
        enable_etf_flow=False,
    )


# ============================================================
# 模块导出
# ============================================================
__all__ = [
    "COMMODITY_TREND_THRESHOLD",
    "COMMODITY_VOLATILITY_THRESHOLD",
    "DEFAULT_COMMODITY_LOOKBACK_DAYS",
    "DEFAULT_CONFIDENCE",
    # 常量
    "DEFAULT_RISK_AVERSION",
    "DEFAULT_TAU",
    "SUPPORTED_COMMODITIES",
    "AttributionManagersFacade",
    "CommodityManager",
    "CommodityMonitorError",
    # 数据类
    "CommoditySnapshot",
    "ETFFlowError",
    "ETFFlowManager",
    # 异常
    "ManagersError",
    "ManagersReport",
    # 管理器
    "PortfolioManager",
    "PortfolioOptimizationError",
    "create_commodity_only_facade",
    "create_default_facade",
    # 便捷函数
    "is_attribution_managers_enabled",
]
