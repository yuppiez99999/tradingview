# -*- coding: utf-8 -*-
"""
金融文本情感分析引擎
基于 YiZhao-FinDataSet 构建, 提供多维度情感与语义分析

功能:
  1. 金融实体识别 (公司/行业/政策/产品)
  2. 情感极性分析 (正面/负面/中性)
  3. 情绪强度量化 (0-1 归一化)
  4. 行业主题分类
  5. 事件类型识别
"""

import math
from collections import Counter
from enum import Enum
from typing import Dict, List, Optional, Tuple

# [V75] bridge import for YiZhaoDataLoader
try:
    from ..bridges.yizhao_data import YiZhaoDataLoader, YiZhaoDocument
except ImportError:
    YiZhaoDataLoader = None
    YiZhaoDocument = None


# ============================================================
# 金融情感词典
# ============================================================
class SentimentPolarity(Enum):
    STRONG_POSITIVE = 2
    POSITIVE = 1
    NEUTRAL = 0
    NEGATIVE = -1
    STRONG_NEGATIVE = -2


class EventType(Enum):
    POLICY = "政策"
    EARNINGS = "业绩"
    M_AND_A = "并购重组"
    REGULATION = "监管"
    RISK = "风险事件"
    INNOVATION = "技术创新"
    MARKET = "市场行情"
    FINANCING = "融资"
    OTHER = "其他"


# ============================================================
# 核心词典定义 (基于金融领域语料)
# ============================================================
STRONG_POSITIVE_WORDS = [
    "暴涨",
    "涨停",
    "翻倍",
    "爆发",
    "突破",
    "创历史新高",
    "超预期",
    "重大利好",
    "政策红利",
    "行业拐点",
    "技术突破",
    "独家",
    "核心竞争力",
    "龙头",
    "寡头",
    "垄断",
    "颠覆性",
    "革命性",
    "大幅增长",
    "业绩暴增",
    "利润翻番",
    "高增长",
    "井喷",
    "中标大单",
    "重大合同",
    "战略合作",
    "获批上市",
]

POSITIVE_WORDS = [
    "上涨",
    "增长",
    "利好",
    "盈利",
    "分红",
    "创新",
    "扩产",
    "政策支持",
    "补贴",
    "中标",
    "获批",
    "升级",
    "领涨",
    "增持",
    "回购",
    "优化",
    "突破",
    "改善",
    "提升",
    "净流入",
    "加仓",
    "看好",
    "推荐",
    "买入",
    "增配",
    "业绩预增",
    "扭亏为盈",
    "产能释放",
    "订单饱满",
]

NEGATIVE_WORDS = [
    "下跌",
    "下滑",
    "亏损",
    "减持",
    "处罚",
    "放缓",
    "承压",
    "低迷",
    "萎缩",
    "困境",
    "挑战",
    "欠佳",
    "不及预期",
    "下降",
    "缩减",
    "谨慎",
    "观望",
    "降低",
    "净流出",
    "减仓",
    "看空",
    "卖出",
    "减持",
    "回避",
]

STRONG_NEGATIVE_WORDS = [
    "暴跌",
    "跌停",
    "暴雷",
    "退市",
    "破产",
    "违约",
    "爆仓",
    "重大利空",
    "黑天鹅",
    "系统性风险",
    "崩盘",
    "踩踏",
    "巨额亏损",
    "资不抵债",
    "财务造假",
    "被立案",
    "被调查",
    "ST",
    "退市风险",
    "清盘",
    "无法表示意见",
    "资金链断裂",
]

# 程度副词 (修饰情感强度)
INTENSIFIERS = {
    "大幅": 1.5,
    "显著": 1.4,
    "明显": 1.3,
    "持续": 1.2,
    "略微": 0.7,
    "小幅": 0.7,
    "轻微": 0.6,
    "微": 0.5,
    "极其": 2.0,
    "非常": 1.8,
    "特别": 1.6,
    "相当": 1.4,
    "急剧": 2.0,
    "剧烈": 1.8,
    "猛烈": 1.8,
}

# 否定词 (翻转情感)
NEGATION_WORDS = ["不", "没", "无", "非", "未", "别", "莫", "勿", "难以", "无法", "不能", "不会", "不再"]

# 行业分类词典
INDUSTRY_KEYWORDS = {
    "能源": ["煤炭", "石油", "天然气", "能源", "电力", "电网", "发电", "光伏", "风电", "核电", "储能", "氢能"],
    "半导体": ["芯片", "半导体", "晶圆", "光刻", "刻蚀", "封装", "集成电路", "EDA", "AI芯片"],
    "医药": ["医药", "制药", "创新药", "仿制药", "疫苗", "生物药", "医疗器械", "CXO", "临床"],
    "制造": ["制造", "机械", "设备", "工业", "机床", "机器人", "自动化", "精密"],
    "金融": ["银行", "保险", "证券", "基金", "信托", "期货", "资管", "理财", "投行"],
    "消费": ["消费", "零售", "电商", "餐饮", "旅游", "家电", "汽车", "服装"],
    "科技": ["人工智能", "AI", "大数据", "云计算", "物联网", "5G", "区块链", "量子"],
    "基建": ["基建", "房地产", "建筑", "建材", "水泥", "钢铁", "工程"],
    "新能源": ["新能源", "锂电", "电池", "光伏", "储能", "充电桩", "电动车", "碳中和"],
}


# ============================================================
# 情感分析器
# ============================================================
class FinSentimentAnalyzer:
    """金融文本情感分析引擎"""

    def __init__(self, loader: YiZhaoDataLoader = None):
        self.loader = loader or get_yizhao_loader()

        # 构建高效查找集合
        self._strong_pos = set(STRONG_POSITIVE_WORDS)
        self._pos = set(POSITIVE_WORDS)
        self._neg = set(NEGATIVE_WORDS)
        self._strong_neg = set(STRONG_NEGATIVE_WORDS)
        self._negation = set(NEGATION_WORDS)

        # 构建 Aho-Corasick 风格的快速匹配 (简化版)
        self._all_words = {}
        for w in self._strong_pos:
            self._all_words[w] = ("strong_pos", 2.0)
        for w in self._pos:
            self._all_words[w] = ("pos", 1.0)
        for w in self._neg:
            self._all_words[w] = ("neg", -1.0)
        for w in self._strong_neg:
            self._all_words[w] = ("strong_neg", -2.0)

    # ---- 情感分析 ----
    def analyze_text(self, text: str) -> Dict:
        """
        分析单段文本的情感
        返回: {'polarity': str, 'score': float, 'confidence': float, ...}
        """
        if not text:
            return self._neutral_result()

        hits = self._find_sentiment_hits(text)
        if not hits:
            return self._neutral_result()

        # 计算加权得分
        total_score = 0.0
        total_weight = 0.0
        for hit in hits:
            _word, _category, base_score, negated = hit
            weight = abs(base_score)
            score = -base_score if negated else base_score
            total_score += score * weight
            total_weight += weight

        if total_weight == 0:
            return self._neutral_result()

        raw_score = total_score / total_weight
        normalized_score = self._normalize_score(raw_score)
        confidence = min(total_weight / 5.0, 1.0)

        # 判定极性
        if normalized_score >= 0.7:
            polarity = "strong_positive"
        elif normalized_score >= 0.55:
            polarity = "positive"
        elif normalized_score <= 0.3:
            polarity = "strong_negative"
        elif normalized_score <= 0.45:
            polarity = "negative"
        else:
            polarity = "neutral"

        return {
            "polarity": polarity,
            "score": round(normalized_score, 4),
            "raw_score": round(raw_score, 4),
            "confidence": round(confidence, 4),
            "hit_count": len(hits),
            "top_words": [h[0] for h in hits[:5]],
        }

    def analyze_document(self, doc: YiZhaoDocument) -> Dict:
        """分析整篇文档的情感 (标题权重更高)"""
        title_result = self.analyze_text(doc.title)
        body_result = self.analyze_text(doc.text[:3000])

        # 标题权重 0.3, 正文权重 0.7
        combined_score = title_result["score"] * 0.3 + body_result["score"] * 0.7

        polarity = "neutral"
        if combined_score >= 0.7:
            polarity = "strong_positive"
        elif combined_score >= 0.55:
            polarity = "positive"
        elif combined_score <= 0.3:
            polarity = "strong_negative"
        elif combined_score <= 0.45:
            polarity = "negative"

        return {
            "polarity": polarity,
            "score": round(combined_score, 4),
            "title_sentiment": title_result,
            "body_sentiment": body_result,
            "fin_score": doc.fin_int_score,
            "source": doc.source_domain,
        }

    def analyze_code_sentiment(self, code: str, top_k: int = 20) -> Dict:
        """分析指定标的的整体舆情情绪"""
        results = self.loader.search_by_code(code, top_k=top_k)
        if not results:
            return {"error": "无相关文档", "code": code}

        scores = []
        polarities = Counter()
        industries = Counter()
        event_types = Counter()

        for r in results:
            analysis = self.analyze_document(r.doc)
            scores.append(analysis["score"])
            polarities[analysis["polarity"]] += 1

            # 行业分类
            inds = self.classify_industry(r.doc.text[:2000])
            for ind in inds:
                industries[ind] += 1

            # 事件分类
            events = self.classify_event(r.doc.text[:2000])
            for evt in events:
                event_types[evt] += 1

        avg_score = sum(scores) / len(scores)
        std_score = (sum((s - avg_score) ** 2 for s in scores) / len(scores)) ** 0.5

        return {
            "code": code,
            "article_count": len(results),
            "sentiment_score": round(avg_score, 4),
            "sentiment_std": round(std_score, 4),
            "dominant_polarity": polarities.most_common(1)[0][0] if polarities else "neutral",
            "polarity_distribution": dict(polarities),
            "top_industries": industries.most_common(3),
            "top_events": event_types.most_common(3),
            "signal": self._sentiment_to_signal(avg_score, std_score),
        }

    def analyze_market_sentiment(self, codes: Optional[List[str]] = None) -> Dict:
        """分析组合整体市场情绪"""
        if codes is None:
            codes = list(self.loader.config.portfolio_keywords.keys())

        all_scores = []
        code_results = {}

        for code in codes:
            result = self.analyze_code_sentiment(code, top_k=10)
            if "error" not in result:
                all_scores.append(result["sentiment_score"])
                code_results[code] = result

        if not all_scores:
            return {"error": "无数据"}

        avg = sum(all_scores) / len(all_scores)

        # 极值检测
        extreme_bullish = [c for c, r in code_results.items() if r["sentiment_score"] >= 0.7]
        extreme_bearish = [c for c, r in code_results.items() if r["sentiment_score"] <= 0.3]

        return {
            "portfolio_sentiment": round(avg, 4),
            "market_bias": "bullish" if avg > 0.55 else "bearish" if avg < 0.45 else "neutral",
            "extreme_bullish_codes": extreme_bullish,
            "extreme_bearish_codes": extreme_bearish,
            "code_details": code_results,
            "advice": self._generate_market_advice(avg, extreme_bullish, extreme_bearish),
        }

    # ---- 行业分类 ----
    def classify_industry(self, text: str) -> List[str]:
        """识别文本涉及的行业"""
        matched = []
        for industry, keywords in INDUSTRY_KEYWORDS.items():
            for kw in keywords:
                if kw in text:
                    matched.append(industry)
                    break
        return list(set(matched)) if matched else ["综合"]

    # ---- 事件分类 ----
    def classify_event(self, text: str) -> List[EventType]:
        """识别文本涉及的事件类型"""
        events = []

        event_patterns = {
            EventType.POLICY: ["政策", "国务院", "发改委", "工信部", "央行", "监管", "法规", "规划"],
            EventType.EARNINGS: ["业绩", "财报", "营收", "利润", "净利润", "EPS", "ROE", "分红"],
            EventType.M_AND_A: ["并购", "重组", "收购", "资产注入", "借壳", "整合"],
            EventType.REGULATION: ["处罚", "问询函", "监管函", "立案", "调查", "警示", "ST"],
            EventType.RISK: ["风险", "违约", "爆仓", "诉讼", "冻结", "查封", "债务"],
            EventType.INNOVATION: ["研发", "专利", "技术", "突破", "创新", "新产品", "获批"],
            EventType.MARKET: ["涨跌", "行情", "成交量", "资金", "北向", "主力", "板块"],
            EventType.FINANCING: ["融资", "定增", "配股", "发债", "IPO", "再融资", "募资"],
        }

        for event_type, patterns in event_patterns.items():
            for p in patterns:
                if p in text:
                    events.append(event_type)
                    break

        return events if events else [EventType.OTHER]

    # ---- 辅助方法 ----
    def _find_sentiment_hits(self, text: str) -> List[Tuple]:
        """
        在文本中查找情感词命中
        返回: [(word, category, base_score, negated), ...]
        """
        hits = []
        # 按长度降序排序, 优先匹配长词 (如 '创历史新高' 优先于 '新高')
        sorted_words = sorted(self._all_words.keys(), key=len, reverse=True)

        for word in sorted_words:
            idx = text.find(word)
            if idx == -1:
                continue

            category, base_score = self._all_words[word]

            # 检查前方是否有否定词 (5字符窗口)
            prefix = text[max(0, idx - 5) : idx]
            negated = any(n in prefix for n in self._negation)

            # 检查程度副词
            intensity = 1.0
            for intensifier, factor in INTENSIFIERS.items():
                if intensifier in prefix:
                    intensity = factor
                    break

            adjusted_score = base_score * intensity

            hits.append((word, category, adjusted_score, negated))

        return hits

    def _normalize_score(self, raw_score: float) -> float:
        """将原始得分归一化到 [0, 1]"""
        return 1.0 / (1.0 + math.exp(-raw_score * 3))

    def _neutral_result(self) -> Dict:
        return {
            "polarity": "neutral",
            "score": 0.5,
            "raw_score": 0.0,
            "confidence": 0.0,
            "hit_count": 0,
            "top_words": [],
        }

    def _sentiment_to_signal(self, score: float, std: float) -> str:
        """将情绪得分转换为交易信号"""
        if score >= 0.70:
            return "strong_buy"
        elif score >= 0.58:
            return "buy" if std < 0.2 else "buy_cautious"
        elif score <= 0.30:
            return "strong_sell"
        elif score <= 0.42:
            return "sell" if std < 0.2 else "sell_cautious"
        else:
            return "hold"

    def _generate_market_advice(
        self, avg_sentiment: float, extreme_bullish: List[str], extreme_bearish: List[str]
    ) -> str:
        """根据情绪生成市场建议"""
        if extreme_bearish and avg_sentiment < 0.4:
            return "市场情绪偏悲观, 建议降低仓位或增加对冲"
        elif extreme_bullish and avg_sentiment > 0.6:
            return "市场情绪偏乐观, 注意高位风险, 可适度获利了结"
        elif avg_sentiment > 0.55:
            return "整体情绪偏暖, 维持现有配置, 关注回撤"
        elif avg_sentiment < 0.45:
            return "情绪偏冷, 观望为主, 等待明确信号"
        else:
            return "市场情绪中性, 按原定策略执行"


# ============================================================
# 便捷函数
# ============================================================
_global_analyzer: Optional[FinSentimentAnalyzer] = None


def get_sentiment_analyzer() -> FinSentimentAnalyzer:
    """获取全局情感分析器 (单例)"""
    global _global_analyzer
    if _global_analyzer is None:
        _global_analyzer = FinSentimentAnalyzer()
    return _global_analyzer


# TODO(v8.5): get_yizhao_loader 应在 bridges/yizhao_data.py 中定义, 当前为 stub
_global_yizhao_loader = None


def get_yizhao_loader():
    """获取全局易兆数据加载器 (单例 stub)

    TODO(v8.5): 迁移到 bridges/yizhao_data.py, 与 YiZhaoDataLoader 共置
    """
    global _global_yizhao_loader
    if _global_yizhao_loader is None:
        if YiZhaoDataLoader is not None:
            _global_yizhao_loader = YiZhaoDataLoader()
        else:
            return None
    return _global_yizhao_loader


# ============================================================
# 测试入口
# ============================================================
if __name__ == "__main__":
    analyzer = get_sentiment_analyzer()

    # 测试文本分析
    test_texts = [
        "中国神华发布2025年报，净利润大幅增长45%，超出市场预期，分红比例提升至60%",
        "阳光电源因海外订单不及预期，股价承压下行，机构下调目标价",
        "北方华创在半导体设备领域取得重大技术突破，国产替代进程加速",
    ]

    print("=" * 60)
    print("  金融文本情感分析测试")
    print("=" * 60)

    for text in test_texts:
        result = analyzer.analyze_text(text)
        print(f"\n文本: {text[:50]}...")
        print(f"  极性: {result['polarity']:>15}")
        print(f"  得分: {result['score']:.3f} (置信度: {result['confidence']:.2f})")
        print(f"  命中词: {result['top_words']}")

    # 测试组合情绪
    if analyzer.loader.is_loaded:
        print(f"\n{'=' * 60}")
        print("  组合整体情绪分析")
        print(f"{'=' * 60}")
        market = analyzer.analyze_market_sentiment()
        if "error" not in market:
            print(f"  组合情绪得分: {market['portfolio_sentiment']:.3f}")
            print(f"  市场倾向: {market['market_bias']}")
            print(f"  建议: {market['advice']}")
