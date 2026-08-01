"""另类数据指标 v1.0

模仿 Two Sigma / Renaissance / Citadel 另类数据团队

核心指标:
1. 卫星图像经济指标 — 夜间灯光强度 / 油罐液位 / 港口活动 / 农作物监测
2. 网络流量/搜索指数 — 百度指数 / 微博热搜 / 应用下载量
3. 招聘数据 — 大厂招聘岗位数 / 薪资变化
4. 专利数据 — 专利申请数 / 引用数 / 技术领域分布
5. 供应链物流 — 港口吞吐量 / 货运量 / 库存周转
6. 高管行为 — 增减持 / 调研 / 出差

参考:
- Katona, Painter & Patatoukas (2022) "Understanding the City-Level Economics of COVID-19"
- Zhu et al. (2020) "Deep Learning for Satellite-Based Estimation of Poverty"

A股适配:
- 卫星数据: 长江/珠三角港口活动 (集装箱/油轮 AIS 数据)
- 搜索指数: 百度指数 (个股/行业关键词)
- 招聘: 智联/猎聘 (互联网/半导体岗位)
- 专利: 国家知识产权局 (发明专利申请)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)


# ============================================================
# 数据结构
# ============================================================


@dataclass
class SatelliteIndicator:
    """卫星图像指标"""

    region: str  # 区域 (上海港/宁波港/...)
    indicator_type: str  # PORT_ACTIVITY / OIL_TANK / NIGHT_LIGHTS / CROP
    value: float  # 指标值
    timestamp: datetime = field(default_factory=datetime.now)
    # 变化
    yoy_change: float = 0.0  # 同比变化 (%)
    mom_change: float = 0.0  # 环比变化 (%)
    # 关联标的
    related_symbols: List[str] = field(default_factory=list)
    related_industries: List[str] = field(default_factory=list)
    # 置信度
    confidence: float = 0.8  # 数据置信度


@dataclass
class SearchIndexIndicator:
    """搜索指数指标"""

    keyword: str  # 关键词 (公司名/产品/行业)
    platform: str  # BAIDU / WEIBO / ZHIHU
    index_value: float  # 指数值
    timestamp: datetime = field(default_factory=datetime.now)
    # 趋势
    trend_7d: float = 0.0  # 7日趋势
    trend_30d: float = 0.0  # 30日趋势
    # 突增标记
    is_breakout: bool = False
    # 关联标的
    related_symbols: List[str] = field(default_factory=list)


@dataclass
class RecruitmentIndicator:
    """招聘数据指标"""

    company: str
    job_count: int  # 岗位数
    avg_salary: float = 0.0
    timestamp: datetime = field(default_factory=datetime.now)
    # 变化
    job_count_yoy: float = 0.0
    salary_change: float = 0.0
    related_symbol: str = ""  # 关联股票代码


@dataclass
class PatentIndicator:
    """专利数据指标"""

    company: str
    patent_count: int = 0
    citation_count: int = 0
    timestamp: datetime = field(default_factory=datetime.now)
    # 技术领域分布
    tech_distribution: Dict[str, int] = field(default_factory=dict)
    # 变化
    patent_count_yoy: float = 0.0
    citation_growth: float = 0.0
    related_symbol: str = ""


@dataclass
class AltDataSignal:
    """另类数据综合信号"""

    symbol: str
    # 卫星指标汇总
    satellite_score: float = 0.0  # [-1, 1]
    satellite_indicators: List[Dict[str, Any]] = field(default_factory=list)
    # 搜索指数汇总
    search_score: float = 0.0
    search_indicators: List[Dict[str, Any]] = field(default_factory=list)
    # 招聘数据
    recruitment_score: float = 0.0
    recruitment_indicators: List[Dict[str, Any]] = field(default_factory=list)
    # 专利数据
    patent_score: float = 0.0
    patent_indicators: List[Dict[str, Any]] = field(default_factory=list)
    # 综合
    composite_score: float = 0.0  # [-1, 1]
    confidence: float = 0.0
    # 数据覆盖
    coverage: float = 0.0  # [0, 1] 数据覆盖率


@dataclass
class AltDataResult:
    """另类数据分析结果"""

    signals: Dict[str, AltDataSignal] = field(default_factory=dict)
    market_alt_score: float = 0.0
    # 全局异常
    anomalies: List[Dict[str, Any]] = field(default_factory=list)
    # 元数据
    total_indicators: int = 0
    coverage_summary: Dict[str, float] = field(default_factory=dict)


# ============================================================
# 另类数据指标引擎
# ============================================================


class AltDataIndicators:
    """另类数据指标引擎

    用法:
        engine = AltDataIndicators()
        # 添加指标
        engine.add_satellite(SatelliteIndicator(
            region="宁波港", indicator_type="PORT_ACTIVITY",
            value=85.5, yoy_change=12.3,
            related_symbols=["601016", "601919"],
        ))
        # 分析
        result = engine.analyze(symbols=["601016"])
    """

    # 默认关联映射 (A股行业 → 标的)
    INDUSTRY_SYMBOL_MAP = {
        "港口航运": ["601016", "601919", "600018"],
        "半导体": ["688981", "002049", "300142"],
        "新能源车": ["300750", "002594", "002475"],
        "算力": ["300308", "000977", "688256"],
        "医药": ["600276", "603259", "300142"],
    }

    def __init__(
        self,
        # 权重
        w_satellite: float = 0.3,
        w_search: float = 0.25,
        w_recruitment: float = 0.2,
        w_patent: float = 0.25,
        # 异常阈值
        anomaly_threshold: float = 2.0,  # 2 倍标准差视为异常
        # 数据过期阈值 (天)
        data_expiry_days: int = 30,
    ):
        self.w_sat = float(w_satellite)
        self.w_search = float(w_search)
        self.w_recruit = float(w_recruitment)
        self.w_patent = float(w_patent)
        self.anomaly_threshold = float(anomaly_threshold)
        self.expiry_days = int(data_expiry_days)

        # 指标存储
        self.satellite_data: List[SatelliteIndicator] = []
        self.search_data: List[SearchIndexIndicator] = []
        self.recruitment_data: List[RecruitmentIndicator] = []
        self.patent_data: List[PatentIndicator] = []

    # ------------------------------------------------------------
    # 数据添加
    # ------------------------------------------------------------

    def add_satellite(self, indicator: SatelliteIndicator) -> None:
        self.satellite_data.append(indicator)

    def add_search(self, indicator: SearchIndexIndicator) -> None:
        self.search_data.append(indicator)

    def add_recruitment(self, indicator: RecruitmentIndicator) -> None:
        self.recruitment_data.append(indicator)

    def add_patent(self, indicator: PatentIndicator) -> None:
        self.patent_data.append(indicator)

    def add_satellite_batch(self, indicators: List[SatelliteIndicator]) -> int:
        self.satellite_data.extend(indicators)
        return len(indicators)

    # ------------------------------------------------------------
    # 综合分析
    # ------------------------------------------------------------

    def analyze(self, symbols: List[str]) -> AltDataResult:
        """分析多标的的另类数据信号"""
        result = AltDataResult()
        cutoff = datetime.now() - timedelta(days=self.expiry_days)
        total_indicators = 0

        for sym in symbols:
            signal = AltDataSignal(symbol=sym)
            coverage_count = 0

            # 1) 卫星指标
            sat_related = [s for s in self.satellite_data if sym in s.related_symbols and s.timestamp > cutoff]
            if sat_related:
                signal.satellite_score = self._calc_satellite_score(sat_related)
                signal.satellite_indicators = [
                    {
                        "region": s.region,
                        "type": s.indicator_type,
                        "value": s.value,
                        "yoy": s.yoy_change,
                        "mom": s.mom_change,
                    }
                    for s in sat_related[:5]
                ]
                coverage_count += 1
                total_indicators += len(sat_related)

            # 2) 搜索指数
            search_related = [s for s in self.search_data if sym in s.related_symbols and s.timestamp > cutoff]
            if search_related:
                signal.search_score = self._calc_search_score(search_related)
                signal.search_indicators = [
                    {
                        "keyword": s.keyword,
                        "platform": s.platform,
                        "value": s.index_value,
                        "trend_7d": s.trend_7d,
                        "breakout": s.is_breakout,
                    }
                    for s in search_related[:5]
                ]
                coverage_count += 1
                total_indicators += len(search_related)

            # 3) 招聘数据
            recruit_related = [r for r in self.recruitment_data if r.related_symbol == sym and r.timestamp > cutoff]
            if recruit_related:
                signal.recruitment_score = self._calc_recruitment_score(recruit_related)
                signal.recruitment_indicators = [
                    {
                        "company": r.company,
                        "job_count": r.job_count,
                        "avg_salary": r.avg_salary,
                        "job_yoy": r.job_count_yoy,
                    }
                    for r in recruit_related[:3]
                ]
                coverage_count += 1
                total_indicators += len(recruit_related)

            # 4) 专利数据
            patent_related = [p for p in self.patent_data if p.related_symbol == sym and p.timestamp > cutoff]
            if patent_related:
                signal.patent_score = self._calc_patent_score(patent_related)
                signal.patent_indicators = [
                    {
                        "company": p.company,
                        "patents": p.patent_count,
                        "citations": p.citation_count,
                        "patent_yoy": p.patent_count_yoy,
                    }
                    for p in patent_related[:3]
                ]
                coverage_count += 1
                total_indicators += len(patent_related)

            # 综合评分
            signal.composite_score = (
                self.w_sat * signal.satellite_score
                + self.w_search * signal.search_score
                + self.w_recruit * signal.recruitment_score
                + self.w_patent * signal.patent_score
            )
            signal.composite_score = max(-1.0, min(1.0, signal.composite_score))

            # 覆盖率与置信度
            signal.coverage = coverage_count / 4.0
            signal.confidence = min(coverage_count / 3.0, 1.0)

            result.signals[sym] = signal

            # 异常检测
            if abs(signal.composite_score) > 0.5:
                result.anomalies.append(
                    {
                        "symbol": sym,
                        "score": signal.composite_score,
                        "coverage": signal.coverage,
                    }
                )

        # 全市场评分
        all_scores = [s.composite_score for s in result.signals.values()]
        result.market_alt_score = float(np.mean(all_scores)) if all_scores else 0.0

        # 覆盖率汇总
        result.coverage_summary = {
            "satellite": len(self.satellite_data),
            "search": len(self.search_data),
            "recruitment": len(self.recruitment_data),
            "patent": len(self.patent_data),
        }
        result.total_indicators = total_indicators

        return result

    # ------------------------------------------------------------
    # 评分函数
    # ------------------------------------------------------------

    def _calc_satellite_score(self, indicators: List[SatelliteIndicator]) -> float:
        """卫星指标评分 [-1, 1]"""
        scores: List[float] = []
        for ind in indicators:
            # 同比变化归一化到 [-1, 1]
            score = max(-1.0, min(1.0, ind.yoy_change / 50.0))
            scores.append(score * ind.confidence)
        return float(np.mean(scores)) if scores else 0.0

    def _calc_search_score(self, indicators: List[SearchIndexIndicator]) -> float:
        """搜索指数评分 [-1, 1]"""
        scores: List[float] = []
        for ind in indicators:
            # 7日趋势归一化
            score = max(-1.0, min(1.0, ind.trend_7d / 100.0))
            # 突增加权
            if ind.is_breakout:
                score *= 1.5
            scores.append(score)
        return float(np.mean(scores)) if scores else 0.0

    def _calc_recruitment_score(self, indicators: List[RecruitmentIndicator]) -> float:
        """招聘评分 [-1, 1]"""
        scores: List[float] = []
        for ind in indicators:
            # 招聘数同比 + 薪资变化
            job_score = max(-1.0, min(1.0, ind.job_count_yoy / 50.0))
            salary_score = max(-1.0, min(1.0, ind.salary_change / 20.0))
            scores.append(0.6 * job_score + 0.4 * salary_score)
        return float(np.mean(scores)) if scores else 0.0

    def _calc_patent_score(self, indicators: List[PatentIndicator]) -> float:
        """专利评分 [-1, 1]"""
        scores: List[float] = []
        for ind in indicators:
            # 专利数同比 + 引用增长
            patent_score = max(-1.0, min(1.0, ind.patent_count_yoy / 30.0))
            citation_score = max(-1.0, min(1.0, ind.citation_growth / 50.0))
            scores.append(0.5 * patent_score + 0.5 * citation_score)
        return float(np.mean(scores)) if scores else 0.0

    # ------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------

    def get_signal(self, symbol: str) -> Optional[AltDataSignal]:
        """获取单标的信号"""
        result = self.analyze([symbol])
        return result.signals.get(symbol)

    def load_demo_data(self, symbols: List[str]) -> int:
        """加载演示数据 (用于测试)"""
        np.random.seed(42)
        count = 0
        for sym in symbols:
            # 卫星数据
            self.add_satellite(
                SatelliteIndicator(
                    region=f"港口_{sym[:3]}",
                    indicator_type="PORT_ACTIVITY",
                    value=float(np.random.uniform(60, 100)),
                    yoy_change=float(np.random.uniform(-15, 25)),
                    mom_change=float(np.random.uniform(-5, 8)),
                    related_symbols=[sym],
                    confidence=float(np.random.uniform(0.7, 0.95)),
                )
            )
            # 搜索数据
            self.add_search(
                SearchIndexIndicator(
                    keyword=f"{sym}",
                    platform="BAIDU",
                    index_value=float(np.random.uniform(500, 5000)),
                    trend_7d=float(np.random.uniform(-30, 50)),
                    trend_30d=float(np.random.uniform(-15, 30)),
                    is_breakout=bool(np.random.random() > 0.85),
                    related_symbols=[sym],
                )
            )
            # 招聘数据
            self.add_recruitment(
                RecruitmentIndicator(
                    company=f"公司_{sym}",
                    job_count=int(np.random.randint(50, 500)),
                    avg_salary=float(np.random.uniform(15, 45)),
                    job_count_yoy=float(np.random.uniform(-20, 40)),
                    salary_change=float(np.random.uniform(-10, 15)),
                    related_symbol=sym,
                )
            )
            # 专利数据
            self.add_patent(
                PatentIndicator(
                    company=f"公司_{sym}",
                    patent_count=int(np.random.randint(10, 200)),
                    citation_count=int(np.random.randint(50, 1000)),
                    patent_count_yoy=float(np.random.uniform(-10, 30)),
                    citation_growth=float(np.random.uniform(-15, 40)),
                    tech_distribution={"AI": np.random.randint(5, 50), "芯片": np.random.randint(5, 50)},
                    related_symbol=sym,
                )
            )
            count += 4
        return count

    def summarize(self, result: AltDataResult) -> Dict[str, Any]:
        """生成摘要"""
        return {
            "total_signals": len(result.signals),
            "market_alt_score": result.market_alt_score,
            "total_indicators": result.total_indicators,
            "coverage": result.coverage_summary,
            "anomalies_count": len(result.anomalies),
            "top_positive": sorted(
                [(s, r.composite_score) for s, r in result.signals.items()],
                key=lambda x: -x[1],
            )[:5],
            "top_negative": sorted(
                [(s, r.composite_score) for s, r in result.signals.items()],
                key=lambda x: x[1],
            )[:5],
            "avg_coverage": float(np.mean([r.coverage for r in result.signals.values()])) if result.signals else 0.0,
        }
