"""
实际持仓回测验证 (Backtest Current Portfolio)
=============================================
修改原因: P4 用实际持仓做回测验证
修改日期: 2026-08-01

核心问题:
    回测未覆盖实际组合。本模块读取实际持仓, 拉取历史数据,
    模拟 Risk Parity 权重 + 动态Beta对冲, 验证年化收益和回撤。

数据源优先级 (v8.4.1 更新):
    1. Vibe-Trading 多源加载器 (Tushare/AkShare/Sina/Yahoo 自动 fallback)
    2. 本地 parquet 缓存
    3. 代理映射 (降级方案)
    4. 兜底预定义价格

功能:
    1. 读取 config/positions.json 的实际持仓和权重
    2. 通过 Vibe-Trading 适配器拉取 2021-01-01 到当前的日频数据
    3. 模拟 Risk Parity 权重分配
    4. 加入 IF空头动态Beta对冲 (覆盖50% Beta)
    5. 计算年化收益、最大回撤、Sharpe、Calmar
    6. 如果不达标, 输出调仓建议

用法:
    python backtest_current_portfolio.py
    python backtest_current_portfolio.py --start 2022-01-01 --end 2026-07-20
    python backtest_current_portfolio.py --no-hedge  # 不含对冲
    python backtest_current_portfolio.py --no-vibe   # 不使用 Vibe-Trading (仅代理回退)
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from utils.datetime_utils import now_bj

logger = logging.getLogger("backtest_portfolio")
try:
    from utils.risk_params import (
        get_max_drawdown_limit as _get_max_drawdown_limit,
    )
except Exception:
    # 如果直接导入失败, 在运行时回退到基于文件位置的 sys.path 注入再导入
    BASE_DIR = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(BASE_DIR))
    from utils.risk_params import (
        get_max_drawdown_limit as _get_max_drawdown_limit,
    )

# 初始化路径常量
BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = BASE_DIR / "config"
REPORTS_DIR = BASE_DIR / "v8.3_institutional" / "reports"
CACHE_DIR = BASE_DIR / "cache"

# B1.3: 从 config/risk_params.yaml 统一读取回撤上限 (fail-safe 兜底 0.15)
_DEFAULT_MAX_DRAWDOWN_LIMIT = _get_max_drawdown_limit()

# Vibe-Trading 适配器 (可选, 不可用时优雅降级)
try:
    from utils.vibe_trading_adapter import VibeTradingAdapter

    _VIBE_AVAILABLE = True
except ImportError:
    _VIBE_AVAILABLE = False
    logger.warning("Vibe-Trading 适配器不可用, 将使用代理映射回退方案")


class PortfolioBacktester:
    """实际持仓回测器

    策略:
        1. 按 positions.json 的 target_weight 持有
        2. Risk Parity 调整: 按波动率倒数加权
        3. IF期货空头: 覆盖50% Beta暴露
        4. 月度再平衡

    绩效目标:
        - 年化收益 >= 8%
        - 最大回撤 < 15%
        - Sharpe >= 0.8
        - Calmar >= 0.6
    """

    # 性能目标
    TARGET_ANNUAL_RETURN = 0.08
    # B1.3: 从 config/risk_params.yaml 读取 (fail-safe 兜底 0.15)
    TARGET_MAX_DRAWDOWN = _DEFAULT_MAX_DRAWDOWN_LIMIT
    TARGET_SHARPE = 0.80
    TARGET_CALMAR = 0.60

    # 对冲参数
    IF_HEDGE_RATIO = 0.50  # 对冲50% Beta
    IF_MULTIPLIER = 300
    REBALANCE_FREQ = 20  # 20个交易日再平衡

    def __init__(self, positions_file: str = None, use_vibe: bool = True):
        self.positions_file = Path(positions_file) if positions_file else CONFIG_DIR / "positions.json"
        self.positions_data = self._load_positions()
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self.use_vibe = use_vibe and _VIBE_AVAILABLE
        self._vibe_adapter = None
        if self.use_vibe:
            try:
                self._vibe_adapter = VibeTradingAdapter(force_init=False)
                logger.info("Vibe-Trading 适配器已启用")
            except (
                ValueError,
                TypeError,
                KeyError,
                AttributeError,
                RuntimeError,
                OSError,
                TimeoutError,
                ConnectionError,
            ) as e:
                # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
                logger.warning(f"Vibe-Trading 适配器初始化失败: {e}")
                self.use_vibe = False

    def _load_positions(self) -> dict:
        """加载持仓配置"""
        try:
            with open(self.positions_file, encoding="utf-8") as f:
                return json.load(f)
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ) as e:
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.error(f"加载持仓失败: {e}")
            return {}

    def get_portfolio_codes(self) -> dict[str, dict]:
        """提取持仓代码和权重

        Returns:
            {
                "510050.SH": {"name": "...", "weight": 0.07, "sector": "..."},
                ...
            }
        """
        positions = self.positions_data.get("positions", {})
        portfolio = {}

        for code, pos in positions.items():
            weight = pos.get("target_weight", 0)
            if weight <= 0:
                # 没有target_weight, 用amount推算
                amount = pos.get("amount", 0)
                total_capital = self.positions_data.get("meta", {}).get("total_capital", 5_000_000)
                weight = amount / total_capital if total_capital > 0 else 0

            if weight <= 0:
                continue

            portfolio[code] = {
                "name": pos.get("name", code),
                "weight": weight,
                "sector": pos.get("sector", "其他"),
                "type": pos.get("type", "STOCK"),
            }

        return portfolio

    def _try_load_cache(self, cache_file: Path, portfolio_count: int):
        """尝试从 pickle 缓存加载历史数据；命中则返回 DataFrame，否则返回 None

        CWE-502 加固: 缓存文件为 deserialization 入口, 加载前做两道前置校验 —
        (1) 路径必须位于本项目 cache/ 目录内 (拒绝被篡改的路径/符号链接指向外部);
        (2) 文件非空且大小在合理上限内 (拒绝异常/超大载荷)。
        任一项不满足即视为未命中, 不反序列化。
        """
        if not cache_file.exists():
            return None
        # 前置校验①: 解析后的真实路径必须落在 CACHE_DIR 内
        try:
            resolved = cache_file.resolve()
            cache_root = CACHE_DIR.resolve()
            resolved.relative_to(cache_root)
        except (OSError, ValueError):
            logger.warning(f"缓存路径越界或不可解析, 拒绝加载: {cache_file}")
            return None
        # 前置校验②: 非空 + 大小上限 (回测缓存为价格矩阵, 50MB 远超正常体量)
        try:
            size = resolved.stat().st_size
        except OSError:
            return None
        if size == 0 or size > 50 * 1024 * 1024:
            logger.warning(f"缓存文件大小异常 ({size} B), 拒绝加载: {resolved}")
            return None
        try:
            # 仅加载本项目 cache/ 目录内自产缓存, 已做路径+大小前置校验
            df = pd.read_pickle(str(resolved))  # nosec B301 — 见上方前置校验① ②
            if len(df.columns) >= portfolio_count * 0.7:
                logger.info(f"从缓存加载回测数据: {len(df)} 行, {len(df.columns)} 列")
                return df
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ):
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            pass
        return None

    def _load_vibe_trading_data(self, symbols: list, start_date: str, end_date: str) -> dict:
        """通过 Vibe-Trading 多源加载器拉取收盘价；返回 {code: close_series}"""
        price_data = {}
        if not (self.use_vibe and self._vibe_adapter):
            return price_data
        try:
            logger.info("  [Vibe-Trading] 正在加载数据...")
            vibe_batch = self._vibe_adapter.get_batch_ohlcv(symbols, start_date, end_date, interval="1D")
            for code, df in vibe_batch.items():
                if isinstance(df, pd.DataFrame) and "close" in df.columns:
                    price_data[code] = df["close"]
            logger.info(f"  [Vibe-Trading] 成功加载 {len(price_data)}/{len(symbols)} 只标的")
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ) as e:
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.warning(f"  [Vibe-Trading] 加载失败: {e}, 尝试代理回退")
        return price_data

    def _load_parquet_cache(self, symbols: list, price_data: dict) -> int:
        """从本地 parquet 缓存补充缺失标的的收盘价；返回新增标的数量"""
        ohlcv_dir = CACHE_DIR / "ohlcv"
        if not ohlcv_dir.exists():
            return 0
        loaded = 0
        for code in symbols:
            if code in price_data:
                continue
            code_num = code.split(".")[0]
            exchange = code.split(".")[-1] if "." in code else ""
            for suffix in ["_2y.parquet", ".parquet"]:
                parquet_path = ohlcv_dir / f"{code_num}_{exchange}{suffix}"
                if parquet_path.exists():
                    try:
                        df_p = pd.read_parquet(parquet_path)
                        if "close" in df_p.columns and len(df_p) > 60:
                            price_data[code] = df_p["close"]
                            loaded += 1
                    except (
                        ValueError,
                        TypeError,
                        KeyError,
                        AttributeError,
                        RuntimeError,
                        OSError,
                        TimeoutError,
                        ConnectionError,
                    ):
                        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
                        pass
                    break
        if loaded > 0:
            logger.info(f"  [本地缓存] 补充加载 {loaded} 只标的")
        return loaded

    @staticmethod
    def _get_proxy_map() -> dict:
        """返回 ETF/指数代理映射表（用于缺失标的的降级方案）"""
        return {
            "588080.SH": "588000.SH",  # 科创50ETF易方达 → 科创50ETF华夏
            "510050.SH": "588000.SH",  # 上证50ETF → 科创50(成长风格近似)
            "510300.SH": "588000.SH",  # 沪深300ETF → 科创50
            "510500.SH": "588000.SH",  # 中证500ETF → 科创50
            "512100.SH": "588000.SH",  # 中证1000ETF → 科创50
            "159915.SZ": "588000.SH",  # 创业板ETF → 科创50
            "159992.SZ": "588000.SH",  # 创新药ETF → 科创50(成长风格)
            "512400.SH": "518880.SH",  # 有色金属ETF → 黄金ETF(资源风格)
            "516160.SH": "588000.SH",  # 高端装备ETF → 科创50(制造风格)
            "512170.SH": "600276.SH",  # 医疗ETF → 恒瑞医药(医药风格)
            "512880.SH": "588000.SH",  # 证券ETF → 科创50(牛市联动)
            "512760.SH": "588000.SH",  # 半导体ETF → 科创50
            "512800.SH": "588000.SH",  # 银华军工 → 科创50
            "515030.SH": "588000.SH",  # 新能源车ETF → 科创50
            "511010.SH": "588000.SH",  # 国债ETF → 用588000近似(低波动)
        }

    def _apply_proxy_mapping(self, price_data: dict) -> int:
        """对 price_data 应用代理映射补全缺失标的；返回成功映射数量"""
        proxy_map = self._get_proxy_map()
        proxy_applied = 0
        for code, proxy in proxy_map.items():
            if code not in price_data and proxy in price_data:
                price_data[code] = price_data[proxy]
                proxy_applied += 1
                logger.info(f"  [代理映射] {code} → {proxy}")
        return proxy_applied

    def fetch_historical_data(self, start_date: str = "2021-01-01", end_date: str = None) -> pd.DataFrame:
        """拉取历史日频数据 (v8.4.1 Vibe-Trading 集成版)

        数据源优先级:
            1. Vibe-Trading 多源加载器 (Tushare/AkShare/Sina 自动 fallback)
            2. 本地 parquet 缓存
            3. 代理映射 (降级方案)

        Returns:
            DataFrame with columns = 持仓代码, index = 日期, values = 收盘价
        """
        if end_date is None:
            end_date = now_bj().strftime("%Y-%m-%d")

        portfolio = self.get_portfolio_codes()
        cache_file = CACHE_DIR / f"backtest_data_{start_date}_{end_date}.pkl"

        # 尝试从 pickle 缓存加载
        cached = self._try_load_cache(cache_file, len(portfolio))
        if cached is not None:
            return cached

        logger.info(
            f"开始拉取历史数据: {len(portfolio)} 只标的, {start_date} ~ {end_date}"
            f"{' (Vibe-Trading)' if self.use_vibe else ' (代理回退)'}"
        )

        symbols = list(portfolio.keys())

        # ===== 数据源1: Vibe-Trading 多源加载器 =====
        price_data = self._load_vibe_trading_data(symbols, start_date, end_date)

        # ===== 数据源2: 本地 parquet 缓存 (补充缺失) =====
        self._load_parquet_cache(symbols, price_data)

        # ===== 数据源3: 代理映射补全 =====
        proxy_applied = self._apply_proxy_mapping(price_data)

        if not price_data:
            logger.error("未获取到任何历史数据 (所有源均失败)")
            return pd.DataFrame()

        # 合并为DataFrame
        df = pd.DataFrame(price_data)
        df = df.sort_index()
        df = df.ffill().dropna(how="all")

        # 保存缓存
        try:
            df.to_pickle(str(cache_file))
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ):
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            pass

        coverage = len(price_data) / len(symbols) * 100
        logger.info(
            f"历史数据准备完成: {len(df)} 交易日, {len(df.columns)} 只标的 "
            f"(覆盖率 {coverage:.0f}%, 代理 {proxy_applied} 个)"
        )
        return df

    def calc_risk_parity_weights(self, returns: pd.DataFrame, base_weights: dict[str, float]) -> pd.Series:
        """计算 Risk Parity 权重

        基于过去60日波动率的倒数加权:
            w_i = (1/vol_i) / sum(1/vol_j)

        然后与基础权重混合 (70% risk parity + 30% 原始权重)
        """
        if returns.empty:
            return pd.Series(base_weights)

        # 计算60日波动率
        vols = returns.tail(60).std()
        vols = vols.replace(0, vols.mean())  # 避免除零

        # Risk Parity 权重 (波动率倒数)
        inv_vols = 1.0 / vols
        rp_weights = inv_vols / inv_vols.sum()

        # 原始权重
        orig_weights = pd.Series(base_weights).reindex(returns.columns).fillna(0)
        orig_weights = orig_weights / orig_weights.sum() if orig_weights.sum() > 0 else rp_weights

        # 混合: 70% RP + 30% 原始
        blended = 0.7 * rp_weights + 0.3 * orig_weights
        blended = blended / blended.sum()

        return blended

    def backtest(
        self,
        start_date: str = "2021-01-01",
        end_date: str = None,
        use_hedge: bool = True,
        use_risk_parity: bool = True,
    ) -> dict:
        """执行回测

        Args:
            start_date: 回测起始日
            end_date: 回测结束日
            use_hedge: 是否使用IF期货对冲
            use_risk_parity: 是否使用Risk Parity权重

        Returns:
            完整回测结果
        """
        if end_date is None:
            end_date = now_bj().strftime("%Y-%m-%d")

        # 获取数据
        price_df = self.fetch_historical_data(start_date, end_date)
        if price_df.empty:
            return {
                "error": "无法获取历史数据",
                "suggestion": "请检查网络连接或akshare版本",
            }

        # 计算日收益率
        returns_df = price_df.pct_change().dropna()
        if returns_df.empty:
            return {"error": "收益率序列为空"}

        # 获取基础权重
        portfolio = self.get_portfolio_codes()
        base_weights = {code: info["weight"] for code, info in portfolio.items() if code in returns_df.columns}

        if not base_weights:
            return {
                "error": "持仓代码与历史数据无交集",
                "available": list(returns_df.columns),
                "required": list(portfolio.keys()),
            }

        # 归一化权重
        total_w = sum(base_weights.values())
        if total_w > 0:
            base_weights = {k: v / total_w for k, v in base_weights.items()}

        # 回测模拟
        portfolio_returns = []
        rebalance_dates = []
        weights_history = []

        for i in range(len(returns_df)):
            date = returns_df.index[i]
            daily_returns = returns_df.iloc[i]

            # 再平衡检查
            if i % self.REBALANCE_FREQ == 0:
                if use_risk_parity and i >= 60:
                    current_weights = self.calc_risk_parity_weights(returns_df.iloc[max(0, i - 60) : i], base_weights)
                else:
                    current_weights = pd.Series(base_weights).reindex(returns_df.columns).fillna(0)

                rebalance_dates.append(date)
                weights_history.append(current_weights.to_dict())

            # 组合日收益
            available_codes = [c for c in current_weights.index if c in daily_returns.index]
            if not available_codes:
                portfolio_returns.append(0.0)
                continue

            w = current_weights[available_codes]
            r = daily_returns[available_codes]
            w = w / w.sum() if w.sum() > 0 else w  # 再次归一化

            port_return = (w * r).sum()

            # IF期货对冲 (空头)
            if use_hedge:
                # 沪深300作为市场代理
                hs300_code = None
                for code in ["510300.SH", "sh510300"]:
                    if code in daily_returns.index:
                        hs300_code = code
                        break

                if hs300_code:
                    market_return = daily_returns[hs300_code]
                else:
                    # 用组合均值近似
                    market_return = r.mean()

                # 对冲收益 = -market_return * hedge_ratio * portfolio_beta
                portfolio_beta = 0.85  # 组合平均Beta估算
                hedge_return = -market_return * self.IF_HEDGE_RATIO * portfolio_beta
                port_return += hedge_return

            portfolio_returns.append(port_return)

        # 构建净值曲线
        nav = pd.Series(portfolio_returns, index=returns_df.index)
        cumulative_nav = (1 + nav).cumprod()

        # 计算绩效指标
        metrics = self._calc_performance_metrics(nav, cumulative_nav)

        # 达标检查
        meets_targets = self._check_targets(metrics)

        # 调仓建议
        suggestions = []
        if not meets_targets["overall"]:
            suggestions = self._generate_suggestions(metrics, portfolio, returns_df)

        result = {
            "backtest_period": f"{start_date} ~ {end_date}",
            "trading_days": len(returns_df),
            "portfolio_count": len(base_weights),
            "settings": {
                "use_hedge": use_hedge,
                "use_risk_parity": use_risk_parity,
                "hedge_ratio": self.IF_HEDGE_RATIO if use_hedge else 0,
                "rebalance_freq": self.REBALANCE_FREQ,
            },
            "performance": metrics,
            "targets_check": meets_targets,
            "suggestions": suggestions,
            "nav_summary": {
                "start_nav": 1.0,
                "end_nav": (round(float(cumulative_nav.iloc[-1]), 4) if len(cumulative_nav) > 0 else 1.0),
                "peak_nav": round(float(cumulative_nav.max()), 4),
                "trough_nav": round(float(cumulative_nav.min()), 4),
            },
        }

        return result

    def _calc_performance_metrics(self, daily_returns: pd.Series, cumulative_nav: pd.Series) -> dict:
        """计算绩效指标"""
        n_years = len(daily_returns) / 252

        # 年化收益
        total_return = float(cumulative_nav.iloc[-1]) - 1.0 if len(cumulative_nav) > 0 else 0
        annual_return = (1 + total_return) ** (1.0 / n_years) - 1 if n_years > 0 else 0

        # 最大回撤
        rolling_max = cumulative_nav.expanding().max()
        drawdowns = (cumulative_nav - rolling_max) / rolling_max
        max_drawdown = float(drawdowns.min())

        # Sharpe (无风险利率 2%)
        excess_returns = daily_returns - 0.02 / 252
        sharpe = float(excess_returns.mean() / excess_returns.std() * np.sqrt(252)) if excess_returns.std() > 0 else 0

        # Calmar
        calmar = annual_return / abs(max_drawdown) if max_drawdown != 0 else 0

        # 年化波动率
        annual_vol = float(daily_returns.std() * np.sqrt(252))

        # Sortino (只计算下行波动)
        downside = daily_returns[daily_returns < 0]
        downside_vol = float(downside.std() * np.sqrt(252)) if len(downside) > 0 else annual_vol
        sortino = (annual_return - 0.02) / downside_vol if downside_vol > 0 else 0

        # 胜率
        win_rate = float((daily_returns > 0).sum() / len(daily_returns)) if len(daily_returns) > 0 else 0

        return {
            "annual_return": round(annual_return, 4),
            "total_return": round(total_return, 4),
            "max_drawdown": round(max_drawdown, 4),
            "annual_volatility": round(annual_vol, 4),
            "sharpe_ratio": round(sharpe, 3),
            "calmar_ratio": round(calmar, 3),
            "sortino_ratio": round(sortino, 3),
            "win_rate": round(win_rate, 4),
            "n_years": round(n_years, 2),
        }

    def _check_targets(self, metrics: dict) -> dict:
        """检查是否达标"""
        checks = {
            "annual_return": metrics["annual_return"] >= self.TARGET_ANNUAL_RETURN,
            "max_drawdown": abs(metrics["max_drawdown"]) <= self.TARGET_MAX_DRAWDOWN,
            "sharpe_ratio": metrics["sharpe_ratio"] >= self.TARGET_SHARPE,
            "calmar_ratio": metrics["calmar_ratio"] >= self.TARGET_CALMAR,
        }
        checks["overall"] = all(checks.values())

        checks["details"] = {
            "annual_return": f"{metrics['annual_return']*100:.2f}% {'✅' if checks['annual_return'] else '❌'} (目标≥{self.TARGET_ANNUAL_RETURN*100:.0f}%)",  # noqa: E501
            "max_drawdown": f"{metrics['max_drawdown']*100:.2f}% {'✅' if checks['max_drawdown'] else '❌'} (目标<{self.TARGET_MAX_DRAWDOWN*100:.0f}%)",  # noqa: E501
            "sharpe_ratio": f"{metrics['sharpe_ratio']:.3f} {'✅' if checks['sharpe_ratio'] else '❌'} (目标≥{self.TARGET_SHARPE})",  # noqa: E501
            "calmar_ratio": f"{metrics['calmar_ratio']:.3f} {'✅' if checks['calmar_ratio'] else '❌'} (目标≥{self.TARGET_CALMAR})",  # noqa: E501
        }
        return checks

    def _generate_suggestions(self, metrics: dict, portfolio: dict, returns_df: pd.DataFrame) -> list[dict]:
        """生成调仓建议"""
        suggestions = []

        # 收益不达标
        if metrics["annual_return"] < self.TARGET_ANNUAL_RETURN:
            suggestions.append(
                {
                    "type": "INCREASE_ALPHA",
                    "severity": "HIGH",
                    "message": f"年化收益 {metrics['annual_return']*100:.2f}% < 目标 {self.TARGET_ANNUAL_RETURN*100:.0f}%",  # noqa: E501
                    "actions": [
                        "增加高Alpha标的权重 (科技成长)",
                        "增加Theta收益 (备兑看涨策略覆盖更多标的)",
                        "考虑动量因子择时 (趋势确认后加仓)",
                    ],
                }
            )

        # 回撤超标
        if abs(metrics["max_drawdown"]) > self.TARGET_MAX_DRAWDOWN:
            suggestions.append(
                {
                    "type": "REDUCE_DRAWDOWN",
                    "severity": "CRITICAL",
                    "message": f"最大回撤 {metrics['max_drawdown']*100:.2f}% > 目标 {self.TARGET_MAX_DRAWDOWN*100:.0f}%",  # noqa: E501
                    "actions": [
                        "增加IF对冲比例 (50% → 70%)",
                        "增加低波动标的权重 (国债ETF/黄金ETF)",
                        "缩减高Beta标的 (科创50/半导体)",
                        "启动尾部认沽保护 (OTM 5% Put)",
                    ],
                }
            )

        # Sharpe不达标
        if metrics["sharpe_ratio"] < self.TARGET_SHARPE:
            suggestions.append(
                {
                    "type": "IMPROVE_RISK_RETURN",
                    "severity": "MEDIUM",
                    "message": f"Sharpe {metrics['sharpe_ratio']:.3f} < 目标 {self.TARGET_SHARPE}",
                    "actions": [
                        "优化Risk Parity权重 (降低高波动标的配比)",
                        "增加不相关资产 (黄金/国债/商品)",
                        "使用波动率目标控制 (vol targeting 12%)",
                    ],
                }
            )

        # 标的级别的诊断
        if not returns_df.empty:
            # 找出表现最差的标的
            annual_returns = returns_df.mean() * 252
            worst = annual_returns.nsmallest(3)
            for code, ret in worst.items():
                if ret < 0:
                    name = portfolio.get(code, {}).get("name", code)
                    suggestions.append(
                        {
                            "type": "REPLACE_UNDERPERFORMER",
                            "severity": "LOW",
                            "message": f"{name} ({code}) 年化收益 {ret*100:.1f}%, 拖累组合",
                            "actions": [f"考虑减仓或替换 {name}"],
                        }
                    )

        return suggestions

    def save_report(self, result: dict, output_dir: Path = None) -> str:
        """保存回测报告"""
        if output_dir is None:
            output_dir = REPORTS_DIR
        output_dir.mkdir(parents=True, exist_ok=True)

        report_path = output_dir / f"backtest_portfolio_{now_bj():%Y%m%d}.json"
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        logger.info(f"回测报告已保存: {report_path}")
        return str(report_path)


# ============================================================
# CLI 入口
# ============================================================
if __name__ == "__main__":
    import argparse
    import io

    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(description="实际持仓回测验证 (Vibe-Trading 集成版)")
    parser.add_argument("--start", default="2021-01-01", help="回测起始日")
    parser.add_argument("--end", default=None, help="回测结束日")
    parser.add_argument("--no-hedge", action="store_true", help="不使用IF对冲")
    parser.add_argument("--no-rp", action="store_true", help="不使用Risk Parity")
    parser.add_argument("--no-vibe", action="store_true", help="不使用 Vibe-Trading (仅代理回退)")
    parser.add_argument("--save", action="store_true", help="保存报告")
    args = parser.parse_args()

    bt = PortfolioBacktester(use_vibe=not args.no_vibe)

    logger.info("=" * 60)
    logger.info("实际持仓回测验证 (Vibe-Trading 集成版)")
    logger.info(f"持仓文件: {bt.positions_file}")
    logger.info(f"标的数: {len(bt.get_portfolio_codes())}")
    logger.info(f"回测期间: {args.start} ~ {args.end or '今天'}")
    logger.info(f"IF对冲: {'ON' if not args.no_hedge else 'OFF'}")
    logger.info(f"Risk Parity: {'ON' if not args.no_rp else 'OFF'}")
    vibe_status = "ON" if bt.use_vibe else "OFF (代理回退)"
    logger.info(f"Vibe-Trading: {vibe_status}")
    logger.info("=" * 60)

    result = bt.backtest(
        start_date=args.start,
        end_date=args.end,
        use_hedge=not args.no_hedge,
        use_risk_parity=not args.no_rp,
    )

    if "error" in result:
        logger.info(f"\n❌ 回测失败: {result['error']}")
        if "suggestion" in result:
            logger.info(f"   建议: {result['suggestion']}")
        sys.exit(1)

    # 打印结果
    perf = result["performance"]
    logger.info("\n📊 回测结果:")
    logger.debug(f"   回测期间: {result['backtest_period']} ({perf['n_years']:.1f} 年)")
    logger.info(f"   交易天数: {result['trading_days']}")
    logger.info(f"   标的数量: {result['portfolio_count']}")
    print()

    targets = result["targets_check"]
    for _key, detail in targets.get("details", {}).items():
        logger.info(f"   {detail}")

    overall = "✅ 全部达标" if targets["overall"] else "❌ 未达标"
    logger.info(f"\n   综合判定: {overall}")

    if result.get("suggestions"):
        logger.info("\n⚠️ 调仓建议:")
        for s in result["suggestions"]:
            logger.info(f"   [{s['severity']}] {s['message']}")
            for action in s.get("actions", []):
                logger.info(f"      → {action}")

    if args.save:
        path = bt.save_report(result)
        logger.info(f"\n💾 报告已保存: {path}")
