"""ETF期权数据获取接口 — 真实期权链优先 + 多源降级 (v2)

数据源优先级 (v2, 2026-09-11 起真实链优先):
    1. 真实期权链 — AKShare ``option_finance_board`` 上交所 ETF 期权当日行情
       (一次调用取回整条链, 单标的按月拉取后进程内/磁盘缓存)
    2. 本地缓存 — ``data/option_cache/chains/{underlying}_{YYYYMMDD}.json`` (当日链快照)
    3. 已实现波动率代理 — ``utils.alpha.iv_rank.IVRankProvider.fetch_current_level()``
       作为 BS 定价的 σ
    4. Black-Scholes 兜底 (永不抛异常, 保证调用方不崩)

修复背景 (《ETF期权组合诊脉书》2026-09-11 硬伤六「期权数据链未通」):
    旧版 ``_fetch_from_wind()`` 恒 ``return None`` (Wind MCP 无 ETF 期权链接口),
    ``sigma`` 缺省硬编码 ``0.20``, 导致:
      - 期权链从未真正接通, 组合的备兑/保护腿全部走理论定价;
      - 保护成本按 σ=20% 估算, 而沪深300 实际已实现波动率仅约 12.8%, 成本被系统性高估;
      - 「按 IV Rank 调整保护比例」的规则在无真实 IV 时无法执行。
    v2 修复: ① 接入 AKShare 真实期权链 (行权价/权利金/到期月全部真实);
    ② IV 由真实权利金经 Black-Scholes 反解 (implied vol);
    ③ σ 来源落 ``iv_source`` 字段 (explicit / chain_iv / chain_atm_iv / rv_proxy /
       assumed_default), 理论值不再伪装成真实值, 可审计;
    ④ 链快照与单点结果均落盘缓存 (旧版 ``_save_to_cache`` 定义了但从未被调用)。

诚实边界 (不伪造数据):
    - AKShare 覆盖 510050 / 510300 / 510500 / 588000 / 588080;
      **不含 159915 (创业板ETF期权)** → 未覆盖标的返回空链, 由调用方按降级链处理;
    - 交易所 T 型行情不含成交量/持仓量 (AKShare ``数量`` 列是合约条数, 非成交量) →
      ``volume`` 统一置 0 表示「未知」, 消费侧 ``volume=0`` 不触发流动性过滤;
    - 交易所不提供现成 IV, 由权利金反解; 反解失败时回退该到期日 ATM IV;
    - ``vega`` 未纳入 (保持既有 ``_bs_price`` 输出契约不变, 避免改动消费侧过滤语义)。

用法:
    from utils.option_data_fetcher import OptionDataFetcher

    fetcher = OptionDataFetcher()
    # 真实期权链 (推荐: 一次取链, 全网格查询)
    chain = fetcher.get_real_chain(
        "510300", option_type="put", dte_range=(90, 180), spot_price=4.579
    )
    # 单点定价 (签名向后兼容)
    data = fetcher.get_option_data("510300", 4.579, 4.30, 0.25, option_type="put")
    data["source"]     # akshare_chain / local_cache / bs_model
    data["iv_source"]  # explicit / chain_iv / chain_atm_iv / rv_proxy / assumed_default
"""

from __future__ import annotations

import importlib
import json
import logging
import math
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from utils.datetime_utils import now_bj

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CACHE_DIR = _PROJECT_ROOT / "data" / "option_cache"
_CHAIN_CACHE_DIR = _CACHE_DIR / "chains"

_DEFAULT_SIGMA = 0.20
_DEFAULT_R = 0.03
_CHAIN_TTL_SECONDS = 300
_DTE_MATCH_TOLERANCE_DAYS = 7

# AKShare `option_finance_board` 的 symbol 是**中文注册名** (非代码), 覆盖范围为上交所 ETF 期权。
# 159915 (创业板ETF期权) 等深市 ETF 期权不在覆盖内 → 由 docstring 的诚实边界说明, 不伪造。
_AKSHARE_SYMBOL_MAP: dict[str, str] = {
    "510050": "华夏上证50ETF期权",
    "510300": "华泰柏瑞沪深300ETF期权",
    "510500": "南方中证500ETF期权",
    "588000": "华夏科创50ETF期权",
    "588080": "易方达科创50ETF期权",
}

# 上交所期权合约交易代码: 510050C2612M02800 → 标的510050 / C认购(P认沽) / 2612到期 / M标识 / 行权价2.8000
_CONTRACT_RE = re.compile(
    r"^(?P<underlying>\d{6})(?P<cp>[CP])(?P<ym>\d{4})[A-Z]+(?P<strike>\d{5})$"
)

# akshare/requests 网络调用的失败异常集合。
# 说明: requests 的 RequestException 继承自 OSError, json 解析异常继承自 ValueError,
# 故该显式元组已覆盖第三方库在离线/超时/页面改版/字段缺失下的全部现实失败模式。
_FETCH_EXC_TYPES = (
    ImportError,
    ModuleNotFoundError,
    OSError,
    ValueError,
    TypeError,
    KeyError,
    IndexError,
    AttributeError,
    RuntimeError,
    ArithmeticError,
)


class OptionDataFetcher:
    """ETF期权数据获取器 — 真实期权链优先 + 多源降级"""

    def __init__(
        self,
        use_wind: bool = True,
        use_cache: bool = True,
        use_akshare: bool = True,
        chain_ttl_seconds: int = _CHAIN_TTL_SECONDS,
    ) -> None:
        self.use_wind = use_wind
        self.use_cache = use_cache
        # 环境变量护栏: 离线/CI 场景强制关闭真实链 (观测路径 fail-open, 不阻断主流程)
        self.use_akshare = bool(use_akshare) and os.environ.get("OPTION_CHAIN_OFFLINE", "") != "1"
        self.chain_ttl_seconds = max(0, int(chain_ttl_seconds))
        self._wind_available = False
        self._chain_memo: dict[str, dict[str, Any] | None] = {}
        self._chain_memo_at: dict[str, datetime] = {}
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _CHAIN_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        if use_wind:
            self._check_wind_availability()
        if not self.use_akshare:
            logger.info(
                "期权真实链已关闭 (use_akshare=False 或 OPTION_CHAIN_OFFLINE=1), 全程走降级链"
            )

    # ------------------------------------------------------------------
    # 兼容保留: Wind 可用性探测
    # ------------------------------------------------------------------
    def _check_wind_availability(self) -> None:
        try:
            from tools.wind_mcp_fetcher import _get_wind_api_key

            key = _get_wind_api_key()
            self._wind_available = key is not None
            if self._wind_available:
                logger.info("Wind MCP API Key 可用, 但 Wind MCP 无 ETF 期权链接口 (真实链走 AKShare)")
            else:
                logger.info("Wind MCP API Key 未配置, 真实链走 AKShare")
        except (
            ImportError,
            AttributeError,
            ModuleNotFoundError,
            OSError,
            RuntimeError,
        ) as e:
            logger.warning("Wind MCP 检查失败, 真实链走 AKShare: %s", e)
            self._wind_available = False

    # ------------------------------------------------------------------
    # 真实期权链
    # ------------------------------------------------------------------
    def get_chain_snapshot(self, underlying: str, force: bool = False) -> dict[str, Any] | None:
        """取单标的期权链快照 (进程内 TTL → 磁盘当日快照 → AKShare 实时拉取)。

        Returns:
            {"underlying", "source", "fetch_time", "n_contracts", "contracts": [...]}
            或 None (无真实数据)。
        """
        code = self._normalize_underlying(underlying)
        if not code:
            return None

        now = now_bj()
        memo_at = self._chain_memo_at.get(code)
        if (
            not force
            and memo_at is not None
            and (now - memo_at).total_seconds() <= self.chain_ttl_seconds
        ):
            return self._chain_memo.get(code)

        stale = self._chain_memo.get(code)

        if self.use_cache and not force:
            disk = self._load_chain_from_disk(code, now.date())
            if disk is not None:
                self._chain_memo[code] = disk
                self._chain_memo_at[code] = now
                return disk

        snapshot: dict[str, Any] | None = None
        if self.use_akshare:
            contracts = self._fetch_chain_akshare(code, now.date())
            if contracts:
                snapshot = {
                    "underlying": code,
                    "source": "akshare_chain",
                    "fetch_time": now.isoformat(timespec="seconds"),
                    "n_contracts": len(contracts),
                    "contracts": contracts,
                }
                self._chain_memo[code] = snapshot
                self._chain_memo_at[code] = now
                if self.use_cache:
                    self._save_chain_to_disk(code, snapshot, now.date())
                return snapshot

        if stale is not None and stale.get("source") != "chain_disk_cache":
            logger.warning(
                "期权链刷新失败, 回退进程内快照: %s (%s 条合约)", code, stale.get("n_contracts")
            )
            self._chain_memo_at[code] = now
            return stale

        # 负缓存: 在同一 TTL 内不重复打网络
        self._chain_memo[code] = None
        self._chain_memo_at[code] = now
        return None

    def get_real_chain(
        self,
        underlying: str,
        option_type: str | None = None,
        dte_range: tuple[int, int] | None = None,
        otm_range: tuple[float, float] | None = None,
        spot_price: float | None = None,
        min_volume: int = 0,
    ) -> list[dict[str, Any]]:
        """返回真实期权链合约 (含 IV/Greeks), 无真实数据时返回空列表。

        契约与 ``OptionChainFetcher.get_option_chain()`` 产出一致:
        {"strike","expiry","dte","premium","iv","delta","gamma","theta","vega",
         "volume","source"}

        Args:
            option_type: "put" / "call" / None(全部)
            dte_range: 剩余到期日区间 (自然日)
            otm_range: 虚值幅度区间 (相对标的价格的比例, 如 (0.08, 0.12))
            spot_price: 标的价格; 提供时才会补齐 IV/Greeks
            min_volume: 最小成交量过滤 (volume=0 表示未知, 不做过滤)
        """
        snapshot = self.get_chain_snapshot(underlying)
        if not snapshot:
            return []

        want = str(option_type or "").strip().lower()
        filtered: list[dict[str, Any]] = []
        for contract in cast("list[dict[str, Any]]", snapshot.get("contracts") or []):
            ctype = str(contract.get("option_type", "")).lower()
            if want and ctype != want:
                continue
            dte = int(contract.get("dte") or 0)
            if dte_range and not (dte_range[0] <= dte <= dte_range[1]):
                continue
            if otm_range and spot_price and spot_price > 0:
                strike = float(contract["strike"])
                otm_pct = (
                    (spot_price - strike) / spot_price
                    if ctype == "put"
                    else (strike - spot_price) / spot_price
                )
                if otm_pct < otm_range[0] - 1e-9 or otm_pct > otm_range[1] + 1e-9:
                    continue
            volume = float(contract.get("volume") or 0)
            if min_volume > 0 and volume > 0 and volume < min_volume:
                continue
            filtered.append(contract)

        if not filtered:
            return []

        if not spot_price or spot_price <= 0:
            # 无标的价格 → 只能回传权利金, IV/Greeks 置 0 并显式标记来源
            return [self._empty_greeks_row(contract) for contract in filtered]

        rows: list[dict[str, Any]] = []
        for contract in filtered:
            row = self._enrich_with_greeks(contract, float(spot_price), filtered)
            if row is not None:
                rows.append(row)
        return rows

    def _empty_greeks_row(self, contract: dict[str, Any]) -> dict[str, Any]:
        """无标的价格时的降级行 — IV/Greeks 置 0, source 显式标记无 Greeks。"""
        return {
            "strike": float(contract["strike"]),
            "expiry": str(contract.get("expiry", "")),
            "dte": int(contract.get("dte") or 0),
            "premium": float(contract["premium"]),
            "iv": 0.0,
            "delta": 0.0,
            "gamma": 0.0,
            "theta": 0.0,
            "vega": 0.0,
            "volume": float(contract.get("volume") or 0),
            "source": "akshare_chain_no_spot",
            "iv_source": "unavailable",
        }

    def _enrich_with_greeks(
        self,
        contract: dict[str, Any],
        spot: float,
        peers: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """用真实权利金反解 IV, 再以该 IV 计算 Greeks (与权利金自洽)。"""
        strike = float(contract["strike"])
        ctype = str(contract["option_type"]).lower()
        dte = max(int(contract.get("dte") or 0), 1)
        T = dte / 365.0
        premium = float(contract["premium"])

        iv = self._implied_vol(premium, spot, strike, T, _DEFAULT_R, ctype)
        iv_source = "chain_iv"
        if iv is None:
            iv = self._atm_iv_of_expiry(peers, str(contract.get("expiry", "")), spot, _DEFAULT_R, ctype)
            iv_source = "chain_atm_iv"
        if iv is None:
            iv = self._rv_sigma(str(contract.get("underlying") or ""))
            iv_source = "rv_proxy"
        if iv is None:
            logger.debug(
                "合约 IV 反解失败且无 ATM/RV 兜底, 跳过: %s", contract.get("contract")
            )
            return None

        priced = self._bs_price(spot, strike, T, _DEFAULT_R, iv, ctype)
        return {
            "strike": round(strike, 4),
            "expiry": str(contract.get("expiry", "")),
            "dte": dte,
            "premium": round(premium, 6),
            "iv": round(iv, 4),
            "delta": float(priced["delta"]),
            "gamma": float(priced["gamma"]),
            "theta": float(priced["theta"]),
            "vega": 0.0,
            "volume": float(contract.get("volume") or 0),
            "source": "akshare_chain",
            "iv_source": iv_source,
        }

    def _atm_iv_of_expiry(
        self,
        peers: list[dict[str, Any]],
        expiry: str,
        spot: float,
        r: float,
        option_type: str,
    ) -> float | None:
        """同到期日 ATM 合约的 IV — 作为薄价外合约反解失败时的兜底。"""
        candidates = [
            c
            for c in peers
            if str(c.get("expiry", "")) == expiry
            and str(c.get("option_type", "")).lower() == option_type
        ]
        candidates.sort(key=lambda c: abs(float(c["strike"]) - spot))
        for contract in candidates[:3]:
            dte = max(int(contract.get("dte") or 0), 1)
            iv = self._implied_vol(
                float(contract["premium"]), spot, float(contract["strike"]), dte / 365.0, r, option_type
            )
            if iv is not None:
                return iv
        return None

    # ------------------------------------------------------------------
    # 单点查询 (签名向后兼容)
    # ------------------------------------------------------------------
    def get_option_data(
        self,
        underlying: str,
        spot_price: float,
        strike: float,
        T: float,
        r: float = _DEFAULT_R,
        sigma: float | None = None,
        option_type: str = "put",
        trade_date: str | None = None,
    ) -> dict[str, Any]:
        """获取期权数据 — 真实链/缓存优先, BS 兜底。

        Returns:
            {"premium", "iv", "source", "delta", "gamma", "theta", "iv_source"}
            ``iv_source``: explicit(调用方显式给定) / chain_iv(真实链反解) /
            chain_atm_iv(真实链 ATM) / rv_proxy(已实现波动率) / assumed_default(理论兜底 0.20)
        """
        opt = "call" if str(option_type).strip().lower().startswith("c") else "put"

        hit = self._lookup_real_contract(underlying, spot_price, strike, T, r, opt)
        if hit is not None:
            return hit

        # 调用方显式给定 σ 时必须尊重 (不读缓存), 否则「显式 0.20」会被旧缓存静默覆盖
        use_point_cache = self.use_cache and sigma is None
        cache_key = self._point_cache_key(underlying, strike, T, opt, trade_date)
        if use_point_cache:
            cached = self._fetch_from_cache(cache_key)
            if cached:
                cached["source"] = "local_cache"
                return cached

        sigma_value, iv_source = self._resolve_sigma(
            underlying, spot_price, strike, T, r, opt, sigma
        )
        result = self._bs_price(spot_price, strike, T, r, sigma_value, opt)
        result["iv_source"] = iv_source
        if use_point_cache:
            self._save_to_cache(cache_key, result)
        return result

    def _lookup_real_contract(
        self,
        underlying: str,
        spot_price: float,
        strike: float,
        T: float,
        r: float,
        option_type: str,
    ) -> dict[str, Any] | None:
        """在真实链中查找同行权价、到期日最接近的合约 (容差 7 天)。"""
        if not self.use_akshare or spot_price <= 0 or strike <= 0 or T <= 0:
            return None
        snapshot = self.get_chain_snapshot(underlying)
        if not snapshot:
            return None

        contracts = cast("list[dict[str, Any]]", snapshot.get("contracts") or [])
        target_dte = T * 365.0
        best: dict[str, Any] | None = None
        best_gap: float | None = None
        for contract in contracts:
            if str(contract.get("option_type", "")).lower() != option_type:
                continue
            if abs(float(contract["strike"]) - float(strike)) > max(1e-6, strike * 1e-4):
                continue
            gap = abs(float(contract.get("dte") or 0) - target_dte)
            if best_gap is None or gap < best_gap:
                best, best_gap = contract, gap
        if best is None or best_gap is None or best_gap > _DTE_MATCH_TOLERANCE_DAYS:
            return None

        enriched = self._enrich_with_greeks(best, float(spot_price), contracts)
        if enriched is None:
            return None
        enriched["contract"] = best.get("contract")
        enriched["dte_gap_days"] = round(float(best_gap), 1)
        return enriched

    def _resolve_sigma(
        self,
        underlying: str,
        spot_price: float,
        strike: float,
        T: float,
        r: float,
        option_type: str,
        sigma: float | None,
    ) -> tuple[float, str]:
        """σ 解析: 显式 → 真实链 ATM IV → 已实现波动率代理 → 兜底 0.20。"""
        if sigma is not None and sigma > 0:
            return float(sigma), "explicit"
        chain_iv = self._chain_atm_iv(underlying, spot_price, T, r, option_type)
        if chain_iv:
            return chain_iv, "chain_atm_iv"
        rv = self._rv_sigma(underlying)
        if rv:
            return rv, "rv_proxy"
        return _DEFAULT_SIGMA, "assumed_default"

    def _chain_atm_iv(
        self,
        underlying: str,
        spot_price: float,
        T: float,
        r: float,
        option_type: str,
    ) -> float | None:
        """真实链中与请求期限最接近的到期日之 ATM 隐含波动率。"""
        if not self.use_akshare or spot_price <= 0 or T <= 0:
            return None
        snapshot = self.get_chain_snapshot(underlying)
        if not snapshot:
            return None
        contracts = cast("list[dict[str, Any]]", snapshot.get("contracts") or [])
        typed = [c for c in contracts if str(c.get("option_type", "")).lower() == option_type]
        if not typed:
            return None
        target_dte = T * 365.0
        expiries = {str(c.get("expiry", "")) for c in typed}
        best_expiry = min(
            expiries,
            key=lambda e: abs(
                float(next(c for c in typed if str(c.get("expiry", "")) == e).get("dte") or 0)
                - target_dte
            ),
        )
        return self._atm_iv_of_expiry(typed, best_expiry, spot_price, r, option_type)

    def _rv_sigma(self, underlying: str) -> float | None:
        """已实现波动率代理 (RV, 小数口径)。修复旧版 σ 恒 0.20 的核心入口。"""
        code = self._normalize_underlying(underlying) or str(underlying or "")
        if not code:
            return None
        try:
            module = importlib.import_module("utils.alpha.iv_rank")
            provider_cls = getattr(module, "IVRankProvider", None)
            if provider_cls is None:
                return None
            provider = provider_cls(config={"underlying": self._wind_code(code)})
            level = provider.fetch_current_level()
            if level is None:
                return None
            raw = float(level)
            if 0.03 <= raw <= 1.5:
                value = raw
            elif 3.0 <= raw <= 150.0:
                value = raw / 100.0
            else:
                logger.warning("RV 代理值越界, 忽略: %s=%s", code, raw)
                return None
            return round(value, 4)
        except _FETCH_EXC_TYPES as e:
            logger.debug("RV 代理不可用 (%s): %s", code, e)
            return None

    # ------------------------------------------------------------------
    # AKShare 链拉取与解析
    # ------------------------------------------------------------------
    def _fetch_chain_akshare(self, code: str, today: date) -> list[dict[str, Any]]:
        symbol = _AKSHARE_SYMBOL_MAP.get(code)
        if symbol is None:
            logger.info(
                "AKShare 未覆盖 %s 的 ETF 期权 (已覆盖: %s), 走降级链",
                code,
                ",".join(sorted(_AKSHARE_SYMBOL_MAP)),
            )
            return []
        try:
            akshare = importlib.import_module("akshare")
        except _FETCH_EXC_TYPES as e:
            logger.warning("AKShare 不可用, 期权链降级: %s", e)
            return []
        board = getattr(akshare, "option_finance_board", None)
        if board is None:
            logger.warning("AKShare 版本不含 option_finance_board, 期权链降级")
            return []

        merged: dict[str, dict[str, Any]] = {}
        for month in self._candidate_expiry_months(today):
            try:
                frame = board(symbol=symbol, end_month=month)
            except _FETCH_EXC_TYPES as e:
                logger.debug("期权链拉取失败 %s %s: %s", code, month, e)
                continue
            for contract in self._parse_board_frame(frame, today):
                contract["underlying"] = code
                merged[str(contract["contract"])] = contract

        contracts = sorted(merged.values(), key=lambda c: (c["expiry"], c["strike"]))
        if contracts:
            logger.info("期权链拉取成功: %s %s 条合约 (%s)", code, len(contracts), symbol)
        else:
            logger.warning("期权链为空: %s (%s) — AKShare 页面可能改版或非交易日", code, symbol)
        return contracts

    def _parse_board_frame(self, frame: Any, today: date) -> list[dict[str, Any]]:
        """解析 AKShare T 型行情长表 → 合约字典列表。"""
        if frame is None or not hasattr(frame, "empty") or frame.empty:
            return []
        records = frame.to_dict("records")
        out: list[dict[str, Any]] = []
        for row in records:
            contract = str(row.get("合约交易代码") or "").strip()
            matched = _CONTRACT_RE.match(contract)
            if not matched:
                continue
            try:
                expiry = self._expiry_date_from_ym(matched.group("ym"))
            except ValueError:
                continue
            dte = (expiry - today).days
            if dte < 0:
                continue
            premium, price_source = self._pick_premium(row)
            if premium is None:
                continue
            strike = self._resolve_strike(row, matched)
            if strike is None:
                continue
            out.append(
                {
                    "contract": contract,
                    "strike": strike,
                    "expiry": expiry.isoformat(),
                    "dte": dte,
                    "option_type": "call" if matched.group("cp") == "C" else "put",
                    "premium": premium,
                    "price_source": price_source,
                    # 交易所 T 型行情不含成交量 → 0 = 未知 (消费侧不对 0 做流动性过滤)
                    "volume": 0.0,
                    "source": "akshare_chain",
                }
            )
        return out

    @classmethod
    def _resolve_strike(cls, row: dict[str, Any], matched: re.Match[str]) -> float | None:
        """行权价解析: 优先行情表 `行权价` 列, 缺失/异常时由合约代码解析。

        合约代码口径为「价格 × 1000」零填充 5 位 (510300C2612M04000 → 4.000),
        注意不是 × 10000 — 旧实现按 ×10000 解析会把 4.000 读成 0.4,
        使 OTM 幅度过滤判定为 91% 虚值而把整条链滤空。
        """
        column = cls._to_float(row.get("行权价"))
        if column is not None and 0.001 <= column <= 100.0:
            return round(column, 4)
        try:
            return round(float(matched.group("strike")) / 1000.0, 4)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _pick_premium(row: dict[str, Any]) -> tuple[float | None, str]:
        """取权利金: 优先最新价, 无成交时回退前结算价。"""
        for key, tag in (("当前价", "last"), ("前结价", "prev_settle")):
            value = OptionDataFetcher._to_float(row.get(key))
            if value is not None and value > 0:
                return value, tag
        return None, "none"

    @staticmethod
    def _to_float(value: Any) -> float | None:
        if value is None:
            return None
        try:
            text = str(value).strip().replace(",", "")
            if text in ("", "-", "--", "None", "nan"):
                return None
            return float(text)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _candidate_expiry_months(today: date) -> list[str]:
        """上交所挂牌月份 = 当月 / 下月 / 当季 / 下季 (YYMM 编码)。"""

        def shift(year: int, month: int, step: int) -> tuple[int, int]:
            index = year * 12 + (month - 1) + step
            return index // 12, index % 12 + 1

        current = (today.year, today.month)
        next_month = shift(today.year, today.month, 1)
        quarter = next_month
        while quarter[1] not in (3, 6, 9, 12):
            quarter = shift(quarter[0], quarter[1], 1)
        next_quarter = shift(quarter[0], quarter[1], 3)

        months: list[str] = []
        for year, month in (current, next_month, quarter, next_quarter):
            tag = f"{year % 100:02d}{month:02d}"
            if tag not in months:
                months.append(tag)
        return months

    @staticmethod
    def _expiry_date_from_ym(ym: str) -> date:
        """由 YYMM 推算到期日 = 该月第四个星期三 (上交所 ETF 期权规则)。"""
        if len(ym) != 4 or not ym.isdigit():
            raise ValueError(f"非法到期月编码: {ym!r}")
        year = 2000 + int(ym[:2])
        month = int(ym[2:])
        if not 1 <= month <= 12:
            raise ValueError(f"非法到期月: {ym!r}")
        first_day = date(year, month, 1)
        offset = (2 - first_day.weekday()) % 7  # 周三 weekday()==2
        return first_day + timedelta(days=offset + 21)

    # ------------------------------------------------------------------
    # 链缓存
    # ------------------------------------------------------------------
    def _load_chain_from_disk(self, code: str, today: date) -> dict[str, Any] | None:
        cache_file = _CHAIN_CACHE_DIR / f"{code}_{today:%Y%m%d}.json"
        if not cache_file.exists():
            return None
        try:
            with open(cache_file, encoding="utf-8") as f:
                payload = json.load(f)
        except (json.JSONDecodeError, OSError):
            return None
        if not isinstance(payload, dict):
            return None
        contracts = payload.get("contracts")
        if not isinstance(contracts, list) or not contracts:
            return None
        payload["source"] = "chain_disk_cache"
        return cast("dict[str, Any]", payload)

    def _save_chain_to_disk(self, code: str, snapshot: dict[str, Any], today: date) -> None:
        cache_file = _CHAIN_CACHE_DIR / f"{code}_{today:%Y%m%d}.json"
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(snapshot, f, ensure_ascii=False)
        except OSError as e:
            logger.warning("期权链快照落盘失败: %s", e)

    # ------------------------------------------------------------------
    # 单点缓存 (旧版已定义但从未调用 → v2 接上)
    # ------------------------------------------------------------------
    @staticmethod
    def _point_cache_key(
        underlying: str, strike: float, T: float, option_type: str, trade_date: str | None
    ) -> str:
        """单点缓存键 — 按自然日切分, 避免跨日复用陈旧的 σ/权利金。"""
        day = str(trade_date) if trade_date else f"{now_bj():%Y%m%d}"
        return f"{underlying}_{strike}_{T:.4f}_{option_type}_{day}"

    def _fetch_from_cache(self, cache_key: str) -> dict[str, Any] | None:
        """从本地缓存获取"""
        cache_file = _CACHE_DIR / f"{cache_key}.json"
        if not cache_file.exists():
            return None
        try:
            with open(cache_file, encoding="utf-8") as f:
                return cast("dict[str, Any]", json.load(f))
        except (json.JSONDecodeError, OSError):
            return None

    def _save_to_cache(self, cache_key: str, data: dict[str, Any]) -> None:
        """保存到本地缓存"""
        cache_file = _CACHE_DIR / f"{cache_key}.json"
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        except OSError as e:
            logger.warning("缓存保存失败: %s", e)

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------
    @staticmethod
    def _normalize_underlying(underlying: str) -> str:
        code = str(underlying or "").strip().upper().split(".")[0]
        return code if len(code) == 6 and code.isdigit() else ""

    @staticmethod
    def _wind_code(code: str) -> str:
        return f"{code}.SH" if code[:1] in ("5", "6") else f"{code}.SZ"

    @staticmethod
    def _norm_cdf(x: float) -> float:
        return 0.5 * (1 + math.erf(x / math.sqrt(2)))

    @staticmethod
    def _norm_pdf(x: float) -> float:
        return math.exp(-0.5 * x**2) / math.sqrt(2 * math.pi)

    @classmethod
    def _implied_vol(
        cls,
        price: float,
        S: float,
        K: float,
        T: float,
        r: float,
        option_type: str,
    ) -> float | None:
        """由真实权利金反解隐含波动率 (二分法)。无解返回 None。"""
        if price is None or price <= 0 or S <= 0 or K <= 0 or T <= 0:
            return None
        intrinsic = max(0.0, K - S) if option_type == "put" else max(0.0, S - K)
        if price < intrinsic - 1e-9:
            # 权利金低于内在价值 → 陈旧/异常报价, 不可反解
            return None

        lo, hi = 1e-4, 5.0
        f_lo = cls._bs_price(S, K, T, r, lo, option_type)["premium"] - price
        f_hi = cls._bs_price(S, K, T, r, hi, option_type)["premium"] - price
        if f_lo * f_hi > 0:
            return None
        for _ in range(80):
            mid = 0.5 * (lo + hi)
            f_mid = cls._bs_price(S, K, T, r, mid, option_type)["premium"] - price
            if abs(f_mid) < 1e-9:
                return round(mid, 6)
            if f_lo * f_mid <= 0:
                hi = mid
            else:
                lo, f_lo = mid, f_mid
        return round(0.5 * (lo + hi), 6)

    @staticmethod
    def _bs_price(
        S: float, K: float, T: float, r: float, sigma: float, option_type: str
    ) -> dict[str, Any]:
        """Black-Scholes定价 + Greeks"""
        if T <= 0 or S <= 0 or K <= 0 or sigma <= 0:
            intrinsic = max(0, K - S) if option_type == "put" else max(0, S - K)
            return {
                "premium": intrinsic,
                "iv": sigma,
                "source": "bs_model",
                "delta": (
                    -1.0
                    if (option_type == "put" and intrinsic > 0)
                    else (1.0 if intrinsic > 0 else 0.0)
                ),
                "gamma": 0.0,
                "theta": 0.0,
            }

        sqrt_T = math.sqrt(T)
        d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * sqrt_T)
        d2 = d1 - sigma * sqrt_T
        n_d1 = OptionDataFetcher._norm_cdf(d1)

        if option_type == "put":
            premium = K * math.exp(-r * T) * OptionDataFetcher._norm_cdf(-d2) - S * OptionDataFetcher._norm_cdf(-d1)
            delta = -OptionDataFetcher._norm_cdf(-d1)
        else:
            premium = S * n_d1 - K * math.exp(-r * T) * OptionDataFetcher._norm_cdf(d2)
            delta = n_d1

        density = OptionDataFetcher._norm_pdf(d1)
        gamma = density / (S * sigma * sqrt_T)
        theta = -(S * density * sigma) / (2 * sqrt_T) - r * K * math.exp(-r * T) * (
            OptionDataFetcher._norm_cdf(d2) if option_type == "call" else OptionDataFetcher._norm_cdf(-d2)
        )

        return {
            "premium": round(premium, 6),
            "iv": round(sigma, 4),
            "source": "bs_model",
            "delta": round(delta, 4),
            "gamma": round(gamma, 6),
            "theta": round(theta, 6),
        }
