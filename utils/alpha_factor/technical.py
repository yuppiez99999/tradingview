"""量价技术因子模块 — 集成 GTJA191 (国泰君安191因子)

双实现回退策略:
1. 优先尝试 utils.gtja191_factors (vibe_trading_adapter, 完整189因子) — 需 vibe adapter 支持
2. 回退到 ms_strategy.factors.gtja191_factors (纯 Python 21因子, T04 修正版) — 独立可用

默认精选 20 个低相关因子 (ms_strategy 实际实现), 覆盖 6 大主题:
- 反转 (4):  alpha4, alpha19, alpha26, alpha131
- 动量 (5):  alpha22, alpha25, alpha28, alpha132, alpha178
- 成交量 (4): alpha6, alpha12, alpha54, alpha85
- 波动率 (4): alpha30, alpha33, alpha40, alpha43
- 流动性 (2): alpha57, alpha144
- 量价关系 (1): alpha101

参考:
- 国泰君安《基于短周期价量特征的多因子选股体系》2017.06 (GTJA191)
- GTJA191 六大因子族: 趋势/反转/波动/量价相关/回归残差/移动平均
- v8.6.15: 因子数 9→20, 覆盖全部 21 个纯 Python 实现 (排除 alpha24 因与 alpha26 高相关)
"""


from __future__ import annotations

import numpy as np
import pandas as pd

from utils.alpha_factor.base import FactorValue

# 默认精选 20 个低相关 GTJA191 因子 (ms_strategy 21因子中排除 alpha24, 其余全选)
# 覆盖 6 大主题:
#   反转(4):   alpha4(RANK(CLOSE)), alpha19(短/长量比), alpha26, alpha131(RANK(DELAY(CLOSE)))
#   动量(5):   alpha22(中价5日MA), alpha25(上涨日量累计), alpha28(KDJ类), alpha132(20日WMA), alpha178(5日收益)
#   成交量(4): alpha6(上涨占比), alpha12(上/下跌量比), alpha54(阳线天数), alpha85(振幅加权量)
#   波动率(4): alpha30(STD), alpha33(1-RANK(STD)), alpha40(上涨量增速), alpha43(涨跌波比)
#   流动性(2): alpha57(5日累计/均价), alpha144(下跌量价效率)
#   量价(1):   alpha101(CORR(CLOSE,VOLUME,20))
DEFAULT_GTJA = [
    # 反转 (4)
    "gtja191_004",  # RANK(CLOSE) — 收盘价历史排名
    "gtja191_019",  # 短期/长期成交量均值比 — 量缩反转
    "gtja191_026",  # 阴线日振幅累计 — 反转信号
    "gtja191_131",  # RANK(DELAY(CLOSE,5)) — 延迟收盘排名
    # 动量 (5)
    "gtja191_022",  # 中价5日收益率20日均值 — 平滑动量
    "gtja191_025",  # 上涨日成交量累计 — 量能动量
    "gtja191_028",  # KDJ类趋势 (5日跌幅排名) — 金叉类
    "gtja191_132",  # 日收益率20日加权移动平均 — 衰减加权动量
    "gtja191_178",  # 5日收益率 — 简单动量
    # 成交量 (4)
    "gtja191_006",  # 上涨天数占比 — 市场参与度
    "gtja191_012",  # 上涨日/下跌日成交量比 — 量能方向
    "gtja191_054",  # 阳线天数 (CLOSE>OPEN) — 蜡烛图形态
    "gtja191_085",  # 振幅加权成交量占比 — 量能分布
    # 波动率 (4)
    "gtja191_030",  # 5日收益率20日标准差 — 基础波动
    "gtja191_033",  # 1-RANK(STD(RET,20)) — 低波异象
    "gtja191_040",  # 上涨日成交量增长率之和 — 波动-量结合
    "gtja191_043",  # 上涨/下跌日波动率比 — 方向性波动
    # 流动性 (2)
    "gtja191_057",  # 5日累计收益/5日前价格均值 — 收益效率
    "gtja191_144",  # 下跌日量价效率 — 流动性枯竭
    # 量价关系 (1)
    "gtja191_101",  # CORR(CLOSE,VOLUME,20) — 量价相关性
]
# 向后兼容别名 (旧名暗示30个, 实际精选9个, 新代码用 DEFAULT_GTJA)
DEFAULT_GTJA_30 = DEFAULT_GTJA


def _to_ohlcv_df(data: dict[str, list[float]]) -> pd.DataFrame | None:
    """将 price_data 的单标的 dict 转为 GTJA191 所需的 OHLCV DataFrame

    Args:
        data: {"closes": [...], "volumes": [...], "highs": [...], "lows": [...], "opens": [...], "amounts": [...]}

    Returns:
        DataFrame with columns: open/high/low/close/volume/amount, 按时间升序
    """
    closes = data.get("closes", [])
    if len(closes) < 2:
        return None
    n = len(closes)
    highs = data.get("highs", closes)
    lows = data.get("lows", closes)
    opens = data.get("opens", closes)
    vols = data.get("volumes", [0] * n)
    amounts = data.get("amounts", [c * v for c, v in zip(closes, vols)])  # 近似: 成交额 = 量×收

    # 长度对齐
    min_len = min(len(closes), len(highs), len(lows), len(opens), len(vols), len(amounts))
    if min_len < 2:
        return None

    df = pd.DataFrame({
        "open": opens[-min_len:],
        "high": highs[-min_len:],
        "low": lows[-min_len:],
        "close": closes[-min_len:],
        "volume": vols[-min_len:],
        "amount": amounts[-min_len:],
    })
    return df


def _id_to_method(alpha_id: str) -> str:
    """gtja191_004 -> alpha4 (ms_strategy 命名风格, 不带前导零)"""
    suffix = alpha_id.split("_")[-1]
    return f"alpha{int(suffix)}"


def _compute_via_ms_strategy(df: pd.DataFrame, ids: list[str]) -> dict[str, float | None]:
    """用 ms_strategy 版本 (21因子纯 Python 实现) 计算

    Args:
        df: OHLCV DataFrame
        ids: ["gtja191_001", "gtja191_005", ...]

    Returns:
        {alpha_id: float_value}
    """
    try:
        from ms_strategy.factors.gtja191_factors import GTJA191Factors as _GTJA_MS
    except ImportError:
        return {}

    try:
        g = _GTJA_MS()
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
        return {}

    out: dict[str, float | None] = {}
    for alpha_id in ids:
        method_name = _id_to_method(alpha_id)
        fn = getattr(g, method_name, None)
        if fn is None:
            continue
        try:
            val = fn(df)
            out[alpha_id] = val
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            out[alpha_id] = None
    return out


def _compute_via_utils(df: pd.DataFrame, ids: list[str]) -> dict[str, float | None]:
    """用 utils 版本 (vibe adapter, 189因子) 计算

    优先级低于 ms_strategy, 因 vibe_trading_adapter 当前不支持 list_factors。
    """
    try:
        from utils.gtja191_factors import GTJA191Factors as _GTJA_UTILS
        g = _GTJA_UTILS()
        return g.compute(df, factor_ids=ids)
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
        return {}


def compute_technical_factors(
    price_data: dict[str, dict[str, list[float]]],
    selected_ids: list[str] | None = None,
    all_factors: bool = False,
    prefer: str = "ms_strategy",
) -> dict[str, FactorValue]:
    """量价技术类因子 (GTJA191), 默认精选 21 个

    Args:
        price_data: {symbol: {"closes": [...], "volumes": [...], ...}}
        selected_ids: 指定 GTJA191 因子 ID 列表 (如 ["gtja191_001", ...])
        all_factors: True 则尝试计算全部 (仅 utils 版本支持 189 个)
        prefer: "ms_strategy" (默认, 21个纯Python) 或 "utils" (189个, 需 vibe adapter)

    Returns:
        {GTJA_001: FactorValue, ...}, 因子名格式 GTJA_xxx
    """
    # 确定要计算的因子 ID
    if selected_ids:
        ids = selected_ids
    elif all_factors and prefer == "utils":
        try:
            from utils.gtja191_factors import GTJA191Factors
            ids = GTJA191Factors().factor_ids
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            ids = DEFAULT_GTJA
    else:
        ids = DEFAULT_GTJA

    # 选择计算函数
    if prefer == "utils":
        primary_fn = _compute_via_utils
        fallback_fn = _compute_via_ms_strategy
    else:
        primary_fn = _compute_via_ms_strategy
        fallback_fn = _compute_via_utils

    factors: dict[str, FactorValue] = {}
    for sym, data in price_data.items():
        df = _to_ohlcv_df(data)
        if df is None or len(df) < 2:
            continue

        # 主实现
        try:
            values_map = primary_fn(df, ids)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            values_map = {}

        # 回退实现 (仅当主实现完全失败时)
        if not values_map:
            try:
                values_map = fallback_fn(df, ids)
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
                # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
                values_map = {}

        for alpha_id, val in values_map.items():
            if val is None:
                continue
            try:
                fval = float(val)
            except (TypeError, ValueError):
                continue
            if np.isnan(fval) or np.isinf(fval):
                continue
            # gtja191_001 -> GTJA_001
            name = f"GTJA_{alpha_id.split('_')[-1]}" if "_" in alpha_id else f"GTJA_{alpha_id}"
            if name not in factors:
                factors[name] = FactorValue(name=name, category="Technical", values={})
            factors[name].values[sym] = fval

    return factors


def list_available_factors() -> list[str]:
    """查询可用 GTJA191 因子 ID 列表 (格式: gtja191_004, 带前导零)"""
    # 优先 ms_strategy (独立可用)
    try:
        from ms_strategy.factors.gtja191_factors import GTJA191Factors
        g = GTJA191Factors()
        ms_methods = [m for m in dir(g) if m.startswith("alpha")]
        # 统一为 gtja191_NNN 格式 (带前导零, 与 DEFAULT_GTJA 一致)
        return [f"gtja191_{int(m[5:]):03d}" for m in ms_methods]
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError): # ms_strategy 不可用时静默回退到 utils 版本 (降级语义, 非吞错)
        pass
    # 回退 utils (需 vibe adapter)
    try:
        from utils.gtja191_factors import GTJA191Factors
        return GTJA191Factors().factor_ids
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError): # 两个实现均不可用时返回空列表, 调用方据此跳过 Technical 因子
        return []
