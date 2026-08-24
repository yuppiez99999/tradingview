"""
股票池构建模块 — 沪深300 + 中证500 成分股获取

复用 AKShare 数据源：
- akshare.index_stock_cons_csindex("000300")  # 沪深300
- akshare.index_stock_cons_csindex("000905")  # 中证500
- akshare.stock_zh_a_spot_em()               # 全市场实时快照
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import pandas as pd

# 关闭代理 + SSL 兼容（akshare 内部用 requests，避免 SSL EOF）
os.environ.setdefault("NO_PROXY", "*")
os.environ.setdefault("no_proxy", "*")
os.environ.setdefault("HTTP_PROXY", "")
os.environ.setdefault("HTTPS_PROXY", "")
os.environ.setdefault("http_proxy", "")
os.environ.setdefault("https_proxy", "")

# 安全加固: TLS 证书验证保持系统默认启用, 不调用 disable_warnings, 不降低验证强度
# (若旧 OpenSSL 报证书错误, 应升级 certifi/openssl, 而非关闭验证)

logger = logging.getLogger(__name__)

# ============================================================
# 指数代码常量
# ============================================================
HS300_INDEX = "000300"  # 沪深300
ZZ500_INDEX = "000905"  # 中证500


def _get_akshare() -> Any:
    """安全获取 akshare 模块 (Any 收窄, 根除下游 5 处 stub-less attr ignore)."""
    try:
        import akshare as ak_impl

        return ak_impl
    except ImportError:
        logger.error("akshare 未安装，请运行: pip install akshare")
        return None


def get_hs300_constituents() -> pd.DataFrame:
    """获取沪深300成分股

    Returns:
        DataFrame: columns=[code, name, weight], index=0..N
    """
    ak = _get_akshare()
    if ak is None:
        return pd.DataFrame()

    for attempt in range(3):
        try:
            df = ak.index_stock_cons_csindex(symbol=HS300_INDEX)
            if df is None or df.empty:
                time.sleep(1)
                continue

            # 标准化列名
            df = df.rename(
                columns={
                    "成分券代码": "code",
                    "成分券名称": "name",
                    "权重": "weight",
                }
            )
            if "code" not in df.columns:
                # 兼容不同版本 akshare
                df = df.iloc[:, [0, 1]]
                df.columns = ["code", "name"]
            df["code"] = df["code"].astype(str).str.zfill(6)
            df["index"] = "HS300"
            logger.info(f"沪深300 成分股: {len(df)} 只")
            return df[["code", "name", "index"] + (["weight"] if "weight" in df.columns else [])]
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # noqa: BLE001
            logger.warning(f"获取沪深300成分股失败 (attempt {attempt + 1}/3): {e}")
            time.sleep(2)

    return pd.DataFrame()


def get_zz500_constituents() -> pd.DataFrame:
    """获取中证500成分股"""
    ak = _get_akshare()
    if ak is None:
        return pd.DataFrame()

    for attempt in range(3):
        try:
            df = ak.index_stock_cons_csindex(symbol=ZZ500_INDEX)
            if df is None or df.empty:
                time.sleep(1)
                continue

            df = df.rename(
                columns={
                    "成分券代码": "code",
                    "成分券名称": "name",
                    "权重": "weight",
                }
            )
            if "code" not in df.columns:
                df = df.iloc[:, [0, 1]]
                df.columns = ["code", "name"]
            df["code"] = df["code"].astype(str).str.zfill(6)
            df["index"] = "ZZ500"
            logger.info(f"中证500 成分股: {len(df)} 只")
            return df[["code", "name", "index"] + (["weight"] if "weight" in df.columns else [])]
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # noqa: BLE001
            logger.warning(f"获取中证500成分股失败 (attempt {attempt + 1}/3): {e}")
            time.sleep(2)

    return pd.DataFrame()


def get_universe(pool: str = "hs300_zz500") -> pd.DataFrame:
    """获取合并股票池

    Args:
        pool: 股票池类型
            - "hs300": 仅沪深300
            - "zz500": 仅中证500
            - "hs300_zz500": 沪深300+中证500合并（默认）
            - "tdx_all": 通达信全市场（约 5000+ 只）
            - "tdx_top800": 通达信全市场按流动性 Top 800

    Returns:
        DataFrame: columns=[code, name, index]
    """
    # 通达信数据源（TCP，稳定）
    if pool in ("tdx_all", "tdx_top800"):
        df = get_tdx_full_stock_list()
        if not df.empty:
            if pool == "tdx_top800":
                # 用快照按成交额排序
                spot = _get_tdx_full_snapshot()
                if not spot.empty:
                    spot = spot.rename(columns={"代码": "code"})
                    df = df.merge(spot[["code", "成交额"]], on="code", how="left")
                    df = df.sort_values("成交额", ascending=False).head(800)
                    df = df.drop(columns=["成交额"], errors="ignore")
                logger.info(f"通达信 Top 800 (按流动性): {len(df)} 只")
            return df

    # AKShare 沪深300/中证500
    frames: list[pd.DataFrame] = []
    if pool in ("hs300", "hs300_zz500"):
        df = get_hs300_constituents()
        if not df.empty:
            frames.append(df)
    if pool in ("zz500", "hs300_zz500"):
        df = get_zz500_constituents()
        if not df.empty:
            frames.append(df)

    if not frames:
        # 降级 1: 用通达信全市场股票列表
        logger.warning("csindex 不可用，降级为通达信全市场股票列表")
        df = get_tdx_full_stock_list()
        if not df.empty:
            # 取按流动性 Top 800
            return df.head(800) if len(df) > 800 else df
        # 降级 2: 内置蓝筹股白名单
        logger.warning("通达信也不可用，降级为内置蓝筹股白名单")
        return _get_builtin_pool()

    merged = pd.concat(frames, ignore_index=True)
    merged = merged.drop_duplicates(subset=["code"], keep="first").reset_index(drop=True)
    logger.info(f"合并股票池 [{pool}]: {len(merged)} 只 (去重后)")
    return merged


def _get_top_by_marketcap(n: int = 800) -> pd.DataFrame:
    """降级方案：用全市场快照按市值排序取 Top N

    Args:
        n: 取前 N 只

    Returns:
        DataFrame: columns=[code, name, index]
    """
    spot_df = get_full_market_snapshot()
    if spot_df.empty:
        logger.error("全市场快照为空，无法降级")
        return pd.DataFrame()

    # 标准化列名
    spot_df = spot_df.copy()
    if "代码" in spot_df.columns:
        spot_df = spot_df.rename(columns={"代码": "code", "名称": "name", "总市值": "market_cap"})
    spot_df["code"] = spot_df["code"].astype(str).str.zfill(6)

    # 按市值降序
    if "market_cap" not in spot_df.columns:
        # 用成交额代替（流动性代理）
        spot_df["market_cap"] = spot_df.get("成交额", 0)
    spot_df = spot_df.sort_values("market_cap", ascending=False).head(n)

    result = pd.DataFrame(
        {
            "code": spot_df["code"].values,
            "name": spot_df["name"].values if "name" in spot_df.columns else spot_df["code"].values,
            "index": "TOP800_BY_MCAP",
        }
    )
    logger.info(f"降级股票池: Top {len(result)} (按市值排序)")
    return result


def get_full_market_snapshot() -> pd.DataFrame:
    """获取全A股实时快照（用于风险过滤）

    优先级:
    1. AKShare stock_zh_a_spot_em（HTTPS，可能 SSL 失败）
    2. 通达信 pytdx（TCP，更稳定）
    3. 返回空（调用方降级处理）

    Returns:
        DataFrame: 全市场行情，columns 包含 代码/名称/最新价/涨跌幅/成交额/成交量/换手率 等
    """
    # 方案 1: AKShare
    ak = _get_akshare()
    if ak is not None:
        for attempt in range(2):
            try:
                df = ak.stock_zh_a_spot_em()
                if df is not None and not df.empty:
                    logger.info(f"[AKShare] 全市场实时快照: {len(df)} 只股票")
                    return df
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # noqa: BLE001
                logger.debug(f"[AKShare] 快照失败 (attempt {attempt + 1}/2): {e}")
                time.sleep(1)

    # 方案 2: 通达信（TCP 协议，无 SSL 问题）
    logger.info("AKShare 不可用，尝试通达信数据源...")
    try:
        return _get_tdx_full_snapshot()
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # noqa: BLE001
        logger.warning(f"[TDX] 全市场快照失败: {e}")
        return pd.DataFrame()


def _get_tdx_full_snapshot() -> pd.DataFrame:
    """通过通达信获取全市场快照（TCP 协议，稳定）"""
    from utils.tdx_data_source import get_tdx_source

    tdx = get_tdx_source()
    if not tdx or not tdx._connected:
        logger.warning("通达信未连接")
        return pd.DataFrame()

    api = tdx._api
    rows = []

    # 遍历上海(1)和深圳(0)两个市场
    for market in [1, 0]:
        # market_name 已移除 (F841)
        # pytdx 的 get_security_list 分页获取
        start = 0
        page_size = 1000
        max_pages = 20  # 最多 20000 只
        for _ in range(max_pages):
            try:
                stocks = api.get_security_list(market, start)
                if not stocks:
                    break
                for s in stocks:
                    code = str(s.get("code", "")).zfill(6)
                    name = s.get("name", "")
                    # 仅处理股票（过滤指数、基金、债券）
                    if not code or len(code) != 6:
                        continue
                    # 排除指数(880/000开头但带"指"字)、基金、债券
                    if "指数" in name or "ETF" in name.upper() or "债" in name:
                        continue
                    # 排除非主板/创业板/科创板
                    first = code[0]
                    if first not in ("0", "3", "6"):
                        continue

                    # 拉取实时行情
                    try:
                        quotes = api.get_security_quotes([(market, code)])
                        if quotes:
                            q = quotes[0]
                            rows.append(
                                {
                                    "代码": code,
                                    "名称": name,
                                    "最新价": float(q.get("price", 0) or 0),
                                    "涨跌幅": _calc_change_pct(q),
                                    "成交额": float(q.get("amount", 0) or 0),
                                    "成交量": float(q.get("vol", 0) or 0),
                                    "换手率": _calc_turnover(q),
                                    "总市值": float(q.get("liaohuan", 0) or 0),  # 流通市值
                                }
                            )
                    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
                        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
                        continue

                start += page_size
                # 如果本页不足 1000 条，说明到底了
                if len(stocks) < page_size:
                    break
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # noqa: BLE001
                logger.debug(f"TDX market={market} start={start} 失败: {e}")
                break

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    logger.info(f"[TDX] 全市场实时快照: {len(df)} 只股票")
    return df


def _calc_change_pct(q: dict) -> float:
    """计算涨跌幅"""
    try:
        price = float(q.get("price", 0) or 0)
        prev_close = float(q.get("last_close", 0) or 0)
        if prev_close > 0:
            return (price - prev_close) / prev_close * 100
    except (ValueError, TypeError):
        pass
    return 0.0


def _calc_turnover(q: dict) -> float:
    """计算换手率（近似）"""
    try:
        vol = float(q.get("vol", 0) or 0)
        liaohuan = float(q.get("liaohuan", 0) or 0)  # 流通股本
        if liaohuan > 0:
            return vol / liaohuan * 100
    except (ValueError, TypeError):
        pass
    return 0.0


# ============================================================
# 通达信获取全市场股票列表（仅代码+名称，用于股票池构建）
# ============================================================
def get_tdx_full_stock_list() -> pd.DataFrame:
    """通过通达信获取全市场股票列表（仅代码+名称，比快照快）

    Returns:
        DataFrame: columns=[code, name, index]
    """
    from utils.tdx_data_source import get_tdx_source

    tdx = get_tdx_source()
    if not tdx or not tdx._connected:
        return pd.DataFrame()

    api = tdx._api
    rows = []

    for market in [1, 0]:
        market_name = "SH" if market == 1 else "SZ"
        start = 0
        page_size = 1000
        for _ in range(20):
            try:
                stocks = api.get_security_list(market, start)
                if not stocks:
                    break
                for s in stocks:
                    code = str(s.get("code", "")).zfill(6)
                    name = str(s.get("name", ""))
                    if not code or len(code) != 6:
                        continue
                    first = code[0]
                    if first not in ("0", "3", "6"):
                        continue
                    if "指数" in name or "ETF" in name.upper() or "债" in name:
                        continue
                    rows.append({"code": code, "name": name, "index": f"TDX_{market_name}"})
                start += page_size
                if len(stocks) < page_size:
                    break
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
                # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
                break

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.drop_duplicates(subset=["code"], keep="first")
        logger.info(f"[TDX] 全市场股票列表: {len(df)} 只")
    return df


# ============================================================
# 内置高流动性股票白名单（最后降级方案，仅用于烟雾测试）
# ============================================================
_BUILTIN_BLUE_CHIP_POOL = [
    ("600519", "贵州茅台"),
    ("601318", "中国平安"),
    ("600036", "招商银行"),
    ("601166", "兴业银行"),
    ("000858", "五粮液"),
    ("000333", "美的集团"),
    ("600276", "恒瑞医药"),
    ("601888", "中国中免"),
    ("600030", "中信证券"),
    ("601398", "工商银行"),
    ("601939", "建设银行"),
    ("601288", "农业银行"),
    ("601628", "中国人寿"),
    ("600028", "中国石化"),
    ("601857", "中国石油"),
    ("600900", "长江电力"),
    ("600887", "伊利股份"),
    ("000651", "格力电器"),
    ("000001", "平安银行"),
    ("600000", "浦发银行"),
    ("601668", "中国建筑"),
    ("601390", "中国中铁"),
    ("601186", "中国铁建"),
    ("601800", "中国交建"),
    ("600048", "保利发展"),
    ("600340", "华夏幸福"),
    ("000002", "万科A"),
    ("600690", "海尔智家"),
    ("000568", "泸州老窖"),
    ("002304", "洋河股份"),
]


def _get_builtin_pool() -> pd.DataFrame:
    """获取内置白名单股票池（用于网络不可用时的烟雾测试）"""
    return pd.DataFrame(
        {
            "code": [c for c, _ in _BUILTIN_BLUE_CHIP_POOL],
            "name": [n for _, n in _BUILTIN_BLUE_CHIP_POOL],
            "index": "BUILTIN_BLUE_CHIP",
        }
    )


def get_industry_map(symbols: list[str]) -> dict:
    """获取股票->行业的映射

    Args:
        symbols: 股票代码列表

    Returns:
        dict: {code: industry_name}
    """
    ak = _get_akshare()
    if ak is None:
        return {}

    industry_map: dict = {}
    # 使用全市场行业分类（一次 API 调用）
    try:
        df = ak.stock_board_industry_name_em()
        if df is None or df.empty:
            return {}

        # 遍历每个行业板块获取成分股
        for _, row in df.iterrows():
            industry_name = row.get("板块名称", "")
            if not industry_name:
                continue
            try:
                cons_df = ak.stock_board_industry_cons_em(symbol=industry_name)
                if cons_df is None or cons_df.empty:
                    continue
                for code in cons_df["代码"].astype(str).str.zfill(6):
                    if code in symbols:
                        industry_map[code] = industry_name
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError): # noqa: BLE001
                continue
        logger.info(f"行业映射: {len(industry_map)} / {len(symbols)} 只匹配成功")
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # noqa: BLE001
        logger.warning(f"获取行业映射失败: {e}")

    return industry_map


if __name__ == "__main__":
    # 烟雾测试
    logging.basicConfig(level=logging.INFO)
    universe = get_universe("hs300_zz500")
    logger.info(universe.head(10))
    logger.info(f"\n总数: {len(universe)}")
