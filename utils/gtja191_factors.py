"""
GTJA191 因子库 — Alpha191 / 国泰君安191因子

来源：国泰君安证券2017年6月研报《数量化专题: 基于短周期价量特征的多因子选股体系》

SC-5 修复 (2026-09-12): 原实现声称「基于 HKUDS/Vibe-Trading 工业级实现, 完整 189
因子」, 但 VibeTradingAdapter 实际只承载 OHLCV 行情族 (get_ohlcv/get_price_dataframe),
并无 list_factors / compute_single_stock / compute_one_factor / get_meta 四个因子
接口 —— 运行时全部 AttributeError 死链 (审查报告 2026-09-11 §P2-3 实测复现:
GTJA191Factors().alpha144(df) -> AttributeError)。

现改为**两层真实后端**:
1. 主后端 = ms_strategy.factors.gtja191_factors (21 因子纯 Python 实现, T04 修正版,
   独立可用, 与 utils/alpha_factor/technical.py 的默认回退同源);
2. 便捷快捷方法 (alpha001/005/010/028/040/072/144/158/189) 中, 21 因子内已实现的
   直接经主后端计算; 其余因子显式返回 None 并 WARNING —— 不再对不存在的接口
   发起调用, 消除「宣称 189 因子 ≠ 事实」的死链。

宣称口径修正: 本模块当前可用因子 = 21 个 (与 ms_strategy 库一致), 不是 189。
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

logger = logging.getLogger("gtja191_factors")

# ms_strategy 21 因子库已实现的因子号 (alphaN, N 不带前导零)
_MS_STRATEGY_IMPLEMENTED: dict[int, str] = {
    4: "RANK(CLOSE) — 反转",
    6: "上涨日/下跌日相关",
    12: "上涨日/下跌日成交量比",
    19: "短期/长期成交量均值比",
    22: "动量",
    24: "反转",
    25: "动量",
    26: "阴线日振幅累计",
    28: "KDJ 类趋势",
    30: "5日收益率20日标准差",
    33: "1-RANK(STD(RET,20))",
    40: "上涨日成交量增长率之和",
    43: "上涨/下跌日波动率比",
    54: "阳线天数",
    57: "5日累计收益/5日前价格均值",
    85: "振幅加权成交量占比",
    101: "CORR(CLOSE,VOLUME,20)",
    131: "RANK(DELAY(CLOSE,5))",
    132: "动量",
    144: "下跌日量价效率",
    178: "动量",
}

# 便捷快捷方法覆盖的因子号 → 是否在主后端可用
_SHORTCUT_NUMBERS: tuple[int, ...] = (1, 5, 10, 28, 40, 72, 144, 158, 189)


def _ms_strategy_factors() -> Any:
    """加载 ms_strategy 21 因子库 (延迟导入, 独立于 utils 包初始化)."""
    from ms_strategy.factors.gtja191_factors import (  # noqa: N814 - 同名类延迟导入
        GTJA191Factors as MsGtja191Factors,
    )

    return MsGtja191Factors()


class GTJA191Factors:
    """GTJA191 短周期量价因子计算器（21/191 因子, ms_strategy 纯 Python 实现）

    所有因子方法接受 pd.DataFrame (含 open/high/low/close/volume/amount 列,
    按时间升序), 返回 float 或 None (数据不足/因子未实现)。
    """

    def __init__(self, lookback: int = 20):
        """
        Args:
            lookback: 部分因子的默认统计窗口（如 alpha144）
        """
        self.lookback = lookback
        self._backend: Any | None = None
        self._factor_ids: list[str] | None = None

    # ------------------------- 后端 -------------------------

    def _get_backend(self) -> Any:
        if self._backend is None:
            self._backend = _ms_strategy_factors()
        return self._backend

    # ------------------------- 因子列表查询 -------------------------

    @property
    def factor_ids(self) -> list[str]:
        """所有**可用** GTJA191 因子 ID 列表 (gtja191_NNN 格式, 21 个)"""
        if self._factor_ids is None:
            self._factor_ids = [
                f"gtja191_{n:03d}" for n in sorted(_MS_STRATEGY_IMPLEMENTED)
            ]
        return list(self._factor_ids)

    @property
    def count(self) -> int:
        """GTJA191 可用因子总数 (当前 21)"""
        return len(_MS_STRATEGY_IMPLEMENTED)

    def list_by_theme(self, theme: str) -> list[str]:
        """按主题筛选因子

        Args:
            theme: momentum / reversal / volume / volatility /
                   liquidity / microstructure

        主题映射基于研报分类对 21 因子的归类; 未识别主题返回空列表。
        """
        themes: dict[str, list[int]] = {
            "momentum": [22, 25, 57, 132, 178],
            "reversal": [4, 19, 26, 131],
            "volume": [6, 12, 40, 54, 85],
            "volatility": [24, 30, 33, 43],
            "liquidity": [144],
            "microstructure": [28, 101],
        }
        return [f"gtja191_{n:03d}" for n in themes.get(theme, [])]

    def get_formula(self, alpha_id: str) -> str:
        """获取因子公式 (从 ms_strategy 实现的 docstring 首行提取)"""
        try:
            backend = self._get_backend()
            fn = getattr(backend, self._id_to_method(alpha_id), None)
            if fn is None:
                return ""
            doc = (fn.__doc__ or "").strip().splitlines()
            # docstring 首行形如 "Alpha4: RANK(CLOSE)"; 个别因子 (alpha144) 首行
            # 冒号后为空, 公式在紧随的下一行 —— 回退取首个非空行, 而非误取"含义"等
            # 后续解释字段
            if doc and ":" in doc[0]:
                text = doc[0].split(":", 1)[1].strip()
                if text:
                    return text
                for line in doc[1:]:
                    if line.strip():
                        return line.strip()
                return ""
            return doc[0] if doc else ""
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError):
            return ""

    def get_info(self, alpha_id: str) -> dict[str, Any]:
        """获取因子详细信息"""
        try:
            fn = getattr(self._get_backend(), self._id_to_method(alpha_id), None)
            if fn is None:
                return {"error": f"因子未实现: {alpha_id}"}
            doc = (fn.__doc__ or "").strip()
            return {
                "alpha_id": alpha_id,
                "implemented": True,
                "doc": doc,
                "formula": self.get_formula(alpha_id),
            }
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError) as e:
            return {"error": str(e)}

    # ------------------------- 因子计算 -------------------------

    @staticmethod
    def _id_to_method(alpha_id: str) -> str:
        """gtja191_004 -> alpha4 (ms_strategy 命名风格, 不带前导零)"""
        suffix = alpha_id.split("_")[-1]
        return f"alpha{int(suffix)}"

    def _compute_one(self, df: pd.DataFrame, num: int) -> float | None:
        """经主后端计算单个因子; 未实现/数据不足返回 None."""
        if num not in _MS_STRATEGY_IMPLEMENTED:
            logger.warning(
                "gtja191_%03d 不在 21 因子实现集内, 返回 None (SC-5: 不再死链)",
                num,
            )
            return None
        fn = getattr(self._get_backend(), f"alpha{num}", None)
        if fn is None:
            return None
        try:
            result: float | None = fn(df)
            return result
        except (ValueError, TypeError, KeyError, RuntimeError):
            return None

    def compute(
        self,
        df: pd.DataFrame,
        factor_ids: list[str] | None = None,
    ) -> dict[str, float | None]:
        """批量计算 GTJA191 因子

        Args:
            df: 行情 DataFrame，需包含 open/high/low/close/volume/amount
            factor_ids: 指定要计算的因子 ID (gtja191_NNN)，None 则计算全部 21 个

        Returns:
            {alpha_id: float_value} 字典，未实现/计算失败的因子值为 None
        """
        if df is None or len(df) < 2:
            return {}

        ids = factor_ids if factor_ids is not None else self.factor_ids
        out: dict[str, float | None] = {}
        for alpha_id in ids:
            try:
                num = int(alpha_id.split("_")[-1])
            except (ValueError, IndexError):
                out[alpha_id] = None
                continue
            out[alpha_id] = self._compute_one(df, num)
        return out

    def compute_series(
        self,
        df: pd.DataFrame,
        alpha_id: str,
    ) -> pd.Series | None:
        """计算单个因子的滚动时间序列

        ms_strategy 后端为快照式 (返回末截面值), 时间序列扩展未实现 ——
        显式返回 None 并 WARNING, 不再调用不存在的 compute_one_factor。
        """
        logger.warning(
            "compute_series 未实现 (ms_strategy 后端为快照式), 返回 None: %s",
            alpha_id,
        )
        return None

    # ------------------------- 经典因子快捷方法 -------------------------

    def alpha001(self, df: pd.DataFrame) -> float | None:
        """GTJA #1: 量价秩相关 (未在 21 因子集内)"""
        return self._compute_one(df, 1)

    def alpha005(self, df: pd.DataFrame) -> float | None:
        """GTJA #5: 量价时序秩相关的最大值 (未在 21 因子集内)"""
        return self._compute_one(df, 5)

    def alpha010(self, df: pd.DataFrame) -> float | None:
        """GTJA #10: 下跌波动平方的滚动最大值 (未在 21 因子集内)"""
        return self._compute_one(df, 10)

    def alpha028(self, df: pd.DataFrame) -> float | None:
        """GTJA #28: KDJ 类趋势因子"""
        return self._compute_one(df, 28)

    def alpha040(self, df: pd.DataFrame) -> float | None:
        """GTJA #40: 上涨下跌成交量比"""
        return self._compute_one(df, 40)

    def alpha072(self, df: pd.DataFrame) -> float | None:
        """GTJA #72: 成交量变动与收益的相关 (未在 21 因子集内)"""
        return self._compute_one(df, 72)

    def alpha144(self, df: pd.DataFrame) -> float | None:
        """GTJA #144: 下跌日量价效率

        过去 N 个交易日内，下跌日"收益率绝对值/成交额"的平均值
        高值：下跌放量、单位成交额推动的价格跌幅大
        低值：下跌缩量或承接较好
        """
        return self._compute_one(df, 144)

    def alpha158(self, df: pd.DataFrame) -> float | None:
        """GTJA #158: 长期趋势判断 (未在 21 因子集内)"""
        return self._compute_one(df, 158)

    def alpha189(self, df: pd.DataFrame) -> float | None:
        """GTJA #189: 条件成交量衰减加权 (未在 21 因子集内)"""
        return self._compute_one(df, 189)
