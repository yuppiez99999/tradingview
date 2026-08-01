# -*- coding: utf-8 -*-
"""
GTJA191 因子库 — Alpha191 / 国泰君安191因子（完整版）

来源：国泰君安证券2017年6月研报《数量化专题: 基于短周期价量特征的多因子选股体系》
实现：基于 HKUDS/Vibe-Trading (GitHub 14.3k Stars) 的工业级实现

完整 189 个短周期价量因子（Alpha001 - Alpha189），覆盖：
- 动量反转类 (momentum / reversal)
- 成交量类 (volume)
- 波动率类 (volatility)
- 流动性类 (liquidity)
- 价量关系类 (microstructure)

数据需求：
- open / high / low / close / volume / amount（成交额，千元）
- 至少 lookback + 1 个日频数据点

输出：
- {alpha_001: value, alpha_002: value, ..., alpha_189: value}
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from utils.vibe_trading_adapter import get_vibe_adapter


class GTJA191Factors:
    """GTJA191 短周期量价因子计算器（完整 189 因子版）

    使用 Vibe-Trading 工业级实现，所有因子均经过验证。
    """

    def __init__(self, lookback: int = 20):
        """
        Args:
            lookback: 部分因子的默认统计窗口（如 alpha144），
                      大多数因子有自己固定的窗口定义
        """
        self.lookback = lookback
        self._adapter = get_vibe_adapter()
        self._factor_ids: Optional[List[str]] = None
        self._factor_meta: Dict[str, Any] = {}

    # ------------------------- 因子列表查询 -------------------------

    @property
    def factor_ids(self) -> List[str]:
        """所有 GTJA191 因子 ID 列表"""
        if self._factor_ids is None:
            self._factor_ids = self._adapter.list_factors(zoo="gtja191")
        return self._factor_ids

    @property
    def count(self) -> int:
        """GTJA191 因子总数"""
        return len(self.factor_ids)

    def list_by_theme(self, theme: str) -> List[str]:
        """按主题筛选因子

        Args:
            theme: momentum / reversal / volume / volatility /
                   liquidity / microstructure / value / quality
        """
        return self._adapter.list_factors(zoo="gtja191", theme=theme)

    def get_formula(self, alpha_id: str) -> str:
        """获取因子公式"""
        try:
            meta = self._adapter.get_meta(alpha_id)
            return meta.formula
        except Exception as e:
            return ""

    def get_info(self, alpha_id: str) -> Dict[str, Any]:
        """获取因子详细信息"""
        try:
            meta = self._adapter.get_meta(alpha_id)
            return {
                "alpha_id": meta.alpha_id,
                "themes": meta.themes,
                "formula": meta.formula,
                "columns_required": meta.columns_required,
                "min_warmup_bars": meta.min_warmup_bars,
                "decay_horizon": meta.decay_horizon,
                "notes": meta.notes,
            }
        except Exception as e:
            return {"error": str(e)}

    # ------------------------- 因子计算 -------------------------

    def compute(
        self,
        df: pd.DataFrame,
        factor_ids: Optional[List[str]] = None,
    ) -> Dict[str, Optional[float]]:
        """批量计算 GTJA191 因子

        Args:
            df: 行情 DataFrame，需包含 open/high/low/close/volume/amount
            factor_ids: 指定要计算的因子 ID，None 则计算全部

        Returns:
            {alpha_id: float_value} 字典，计算失败的因子值为 None
        """
        if df is None or len(df) < 2:
            return {}

        result = self._adapter.compute_single_stock(df, factor_ids=factor_ids, zoo="gtja191")
        return result.values

    def compute_series(
        self,
        df: pd.DataFrame,
        alpha_id: str,
    ) -> Optional[pd.Series]:
        """计算单个因子的完整时间序列

        Args:
            df: 行情 DataFrame
            alpha_id: 因子 ID (如 "gtja191_001")

        Returns:
            因子值时间序列，失败返回 None
        """
        return self._adapter.compute_one_factor(df, alpha_id)

    # ------------------------- 经典因子快捷方法 -------------------------

    def alpha001(self, df: pd.DataFrame) -> Optional[float]:
        """GTJA #1: 量价秩相关
        (-1 * CORR(RANK(DELTA(LOG(VOLUME), 1)), RANK(((CLOSE - OPEN) / OPEN)), 6))
        放量不涨或缩量不跌预示短期反转
        """
        return self.compute(df, ["gtja191_001"]).get("gtja191_001")

    def alpha005(self, df: pd.DataFrame) -> Optional[float]:
        """GTJA #5: 量价时序秩相关的最大值
        (-1 * TSMAX(CORR(TSRANK(VOLUME, 5), TSRANK(HIGH, 5), 5), 3))
        """
        return self.compute(df, ["gtja191_005"]).get("gtja191_005")

    def alpha010(self, df: pd.DataFrame) -> Optional[float]:
        """GTJA #10: 下跌波动平方的滚动最大值
        RANK(MAX(((RET < 0) ? STD(RET, 20) : CLOSE)^2), 5)
        """
        return self.compute(df, ["gtja191_010"]).get("gtja191_010")

    def alpha028(self, df: pd.DataFrame) -> Optional[float]:
        """GTJA #28: KDJ 类趋势因子
        3*SMA(...) - 2*SMA(SMA(...))
        """
        return self.compute(df, ["gtja191_028"]).get("gtja191_028")

    def alpha040(self, df: pd.DataFrame) -> Optional[float]:
        """GTJA #40: 上涨下跌成交量比
        SUM(上涨日成交量,26) / SUM(下跌日成交量,26) * 100
        """
        return self.compute(df, ["gtja191_040"]).get("gtja191_040")

    def alpha072(self, df: pd.DataFrame) -> Optional[float]:
        """GTJA #72: 成交量变动与收益的相关
        -1 * CORR(DELTA(VOLUME, 1), CLOSE/DELAY(CLOSE,1), 10)
        """
        return self.compute(df, ["gtja191_072"]).get("gtja191_072")

    def alpha144(self, df: pd.DataFrame) -> Optional[float]:
        """GTJA #144: 下跌日量价效率
        过去 N 个交易日内，下跌日"收益率绝对值/成交额"的平均值
        高值：下跌放量、单位成交额推动的价格跌幅大
        低值：下跌缩量或承接较好
        """
        return self.compute(df, ["gtja191_144"]).get("gtja191_144")

    def alpha158(self, df: pd.DataFrame) -> Optional[float]:
        """GTJA #158: 长期趋势判断
        (CLOSE - TSMIN(LOW, 250)) / (TSMAX(HIGH, 250) - TSMIN(LOW, 250))
        """
        return self.compute(df, ["gtja191_158"]).get("gtja191_158")

    def alpha189(self, df: pd.DataFrame) -> Optional[float]:
        """GTJA #189: 条件成交量衰减加权
        DECAYLINEAR(CONDITION, 12)，最后一个 GTJA191 因子
        """
        return self.compute(df, ["gtja191_189"]).get("gtja191_189")
