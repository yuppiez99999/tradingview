"""
期货期权市场机会扫描器 v1.0
扫描维度：商品期货 / 股指期货 / 期权波动率 / 对冲需求
数据源：iFinD EDB（宏观大宗商品）+ 本地组合配置
"""

from __future__ import annotations

import json
import math
import os
import sys
from datetime import datetime
from typing import Any

# 保证可导入项目内 ifind skill 与 data 模块
_HERE = os.path.dirname(__file__)
_IFIND_SKILL = os.path.normpath(os.path.join(_HERE, "..", "skills", "ifind-finance-data"))
_DATA_DIR = os.path.normpath(os.path.join(_HERE, "data"))
for _p in [_IFIND_SKILL, _DATA_DIR]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from data.edb_futures_data import EDBFuturesData  # noqa: E402

try:
    from utils.akshare_futures import fetch_futures_historical
    HAS_AKSHARE_FUTURES = True
except Exception:
    HAS_AKSHARE_FUTURES = False

# 名称 -> akshare 期货代码映射（仅用于 EDB 无数据时的回退）
_AKSHARE_FUTURES_MAP = {
    "锡": "SN0.SH",
    "铜": "CU0.SH",
    "铝": "AL0.SH",
    "锌": "ZN0.SH",
    "镍": "NI0.SH",
    "铅": "PB0.SH",
    "银": "AG0.SH",
    "工业硅": "SI0.DCE",
    "多晶硅": "SI0.DCE",
    "碳酸锂": "LC0.SH",
    "原油": "SC0.INE",
    "PTA": "TA0.CZCE",
    "动力煤": "ZC0.CZCE",
    "螺纹钢": "RB0.SH",
    "铁矿石": "I0.DCE",
    "热轧卷板": "HC0.SH",
    "焦炭": "J0.DCE",
    "焦煤": "JM0.DCE",
    "大豆": "A0.DCE",
    "玉米": "C0.DCE",
    "棕榈油": "P0.DCE",
    "黄金": "AU0.SH",
    "棉花": "CF0.CZCE",
    "甲醇": "MA0.CZCE",
    "玻璃": "FG0.CZCE",
    "沪深300": "IF2506.CFE",
    "中证500": "IC2506.CFE",
    "中证1000": "IM2506.CFE",
}


# ========================
# 工具函数
# ========================

def _parse_edb_table(raw_text: str) -> list[tuple[str, float]]:
    """从 iFinD EDB 返回的文本中解析 日期|数值 表格"""
    rows: list[tuple[str, float]] = []
    for line in raw_text.splitlines():
        line = line.strip()
        if not line.startswith("|") or line.startswith("|---") or line.startswith("|日期"):
            continue
        parts = [p.strip() for p in line.strip("|").split("|")]
        if len(parts) < 2:
            continue
        date_str = parts[0]
        value_str = parts[1].replace(",", "").replace("，", "").strip()
        try:
            rows.append((date_str, float(value_str)))
        except ValueError:
            continue
    return rows


def _safe_num(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def _pct_change(series: list[tuple[str, float]], window: int = 20) -> float | None:
    if len(series) < window + 1:
        return None
    return (series[-1][1] - series[-window - 1][1]) / series[-window - 1][1]


def _volatility(series: list[tuple[str, float]], window: int = 20) -> float | None:
    if len(series) < window + 1:
        return None
    rets = []
    for i in range(1, window + 1):
        prev = series[-i - 1][1]
        curr = series[-i][1]
        if prev > 0:
            rets.append(math.log(curr / prev))
    if len(rets) < 2:
        return None
    mean_r = sum(rets) / len(rets)
    var = sum((r - mean_r) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(252)


def _trend_score(series: list[tuple[str, float]], window: int = 20) -> float | None:
    if len(series) < window:
        return None
    recent = [v for _, v in series[-window:]]
    up = sum(1 for i in range(1, len(recent)) if recent[i] > recent[i - 1])
    return up / (len(recent) - 1)


def _rsi(series: list[tuple[str, float]], window: int = 14) -> float | None:
    """简易 RSI，用于辅助判断超买/超卖"""
    if len(series) < window + 1:
        return None
    gains = []
    losses = []
    for i in range(1, window + 1):
        prev = series[-i - 1][1]
        curr = series[-i][1]
        if prev <= 0:
            continue
        change = (curr - prev) / prev
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    if not gains or not losses:
        return None
    avg_gain = sum(gains) / len(gains)
    avg_loss = sum(losses) / len(losses)
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def _max_drawdown(series: list[tuple[str, float]], window: int = 20) -> float | None:
    """近 window 日最大回撤"""
    if len(series) < window:
        return None
    recent = [v for _, v in series[-window:]]
    peak = recent[0]
    max_dd = 0.0
    for v in recent[1:]:
        if v > peak:
            peak = v
        dd = (peak - v) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
    return max_dd


def _to_edb_like(name: str, df: Any) -> dict[str, Any]:
    """将 AKShare DataFrame 转成 EDBFuturesData.fetch() 的近似结构"""
    try:
        if df is None or (hasattr(df, "empty") and df.empty):
            return {}
        if not hasattr(df, "columns"):
            return {}
        rename_map = {}
        if "date" in df.columns:
            rename_map["date"] = "date"
        for c in ["open", "high", "low", "close", "volume"]:
            if c in df.columns:
                rename_map[c] = c
        if rename_map:
            df = df.rename(columns=rename_map)
        series: list[tuple[str, float]] = []
        for _, row in df.iterrows():
            dt = row.get("date")
            close = row.get("close")
            if dt is None or close is None:
                continue
            series.append((str(dt), float(close)))
        latest = series[-1][1] if series else None
        prev = series[-2][1] if len(series) >= 2 else None
        latest_date = series[-1][0] if series else None
        return {"series": series, "latest": latest, "prev": prev, "latest_date": latest_date}
    except Exception:
        return {}


# ========================
# 扫描逻辑
# ========================

def _akshare_fallback(name: str) -> dict[str, Any]:
    """EDB 无数据时的 AKShare 回退"""
    if not HAS_AKSHARE_FUTURES:
        return {}
    symbol = _AKSHARE_FUTURES_MAP.get(name)
    if not symbol:
        return {}
    df = fetch_futures_historical(symbol, days=60)
    return _to_edb_like(name, df)


class FuturesOptionsScanner:
    def __init__(self):
        self.report_time = datetime.now().strftime("%Y-%m-%d %H:%M")
        self.items: list[dict[str, Any]] = []
        self._raw_series: dict[str, list[tuple[str, float]]] = {}

    def scan_commodity_futures(self):
        """商品期货：覆盖 AI 算力链、十五五战略资源、能源/化工"""
        candidates = [
            # AI 算力 / 电子制造链
            ("锡", "AI算力/半导体/焊料"),
            ("铜", "AI算力/电力/基建"),
            ("铝", "AI算力/轻量化/电力"),
            ("银", "AI算力/光伏/贵金属"),
            ("工业硅", "AI算力/半导体/光伏"),
            ("多晶硅", "AI算力/半导体/光伏"),
            ("碳酸锂", "AI算力/新能源/储能"),
            ("原油", "AI算力/化工/运输"),
            ("PTA", "AI算力/化工/纤维"),
            ("动力煤", "AI算力/电力成本"),
            # 十五五：高端制造 / 军工 / 基建
            ("螺纹钢", "十五五/基建/高端制造"),
            ("铁矿石", "十五五/基建/钢铁安全"),
            ("热轧卷板", "十五五/基建/机械"),
            # 十五五：粮食 / 资源安全
            ("大豆", "十五五/粮食安全"),
            ("玉米", "十五五/粮食安全"),
            ("棕榈油", "十五五/油脂安全"),
            # 避险 / 贵金属
            ("黄金", "避险/货币信用"),
            # 其他战略品种
            ("棉花", "纺织/消费"),
        ]
        for name, tag in candidates:
            data = EDBFuturesData.fetch(name)
            rows = data.get("series", [])
            if not rows and HAS_AKSHARE_FUTURES:
                data = _akshare_fallback(name)
                rows = data.get("series", [])
            if not rows:
                continue
            self._raw_series[name] = rows
            ret20 = _pct_change(rows, 20)
            vol20 = _volatility(rows, 20)
            trend = _trend_score(rows, 20)
            latest = _safe_num(data.get("latest"))
            prev = _safe_num(data.get("prev"))
            chg_1d = (latest - prev) / prev if prev > 0 else None

            score = 0.0
            reasons: list[str] = []
            if ret20 is not None:
                if ret20 > 0.05:
                    score += 2
                    reasons.append(f"20日上涨{ret20:.1%}")
                elif ret20 < -0.05:
                    score -= 2
                    reasons.append(f"20日下跌{abs(ret20):.1%}")
            if vol20 is not None:
                if name in {"工业硅", "多晶硅", "碳酸锂", "PTA", "铁矿石"}:
                    if vol20 > 0.30:
                        score += 1
                        reasons.append("战略品种波动偏高")
                    elif vol20 < 0.12:
                        score -= 1
                        reasons.append("战略品种波动偏低")
                else:
                    if vol20 > 0.35:
                        score += 1
                        reasons.append("波动率偏高")
                    elif vol20 < 0.15:
                        score -= 1
                        reasons.append("波动率偏低")
            if trend is not None:
                if trend > 0.65:
                    score += 1
                    reasons.append("趋势偏强")
                elif trend < 0.35:
                    score -= 1
                    reasons.append("趋势偏弱")
            if chg_1d is not None:
                if chg_1d > 0.01:
                    score += 0.5
                    reasons.append("昨日上涨")
                elif chg_1d < -0.01:
                    score -= 0.5
                    reasons.append("昨日下跌")

            self.items.append({
                "category": "商品期货",
                "name": name,
                "tag": tag,
                "latest": latest,
                "latest_date": data.get("latest_date"),
                "chg_1d": chg_1d,
                "ret20": ret20,
                "vol20": vol20,
                "trend20": trend,
                "score": score,
                "reasons": reasons,
                "opportunity": "关注" if score >= 2 else ("观望" if score <= -2 else "中性"),
            })

    def scan_index_futures(self):
        """股指期货：沪深300 / 中证500 / 中证1000"""
        candidates = ["沪深300", "中证500", "中证1000"]
        for name in candidates:
            data = EDBFuturesData.fetch(name)
            rows = data.get("series", [])
            if not rows and HAS_AKSHARE_FUTURES:
                data = _akshare_fallback(name)
                rows = data.get("series", [])
            if not rows:
                continue
            ret20 = _pct_change(rows, 20)
            vol20 = _volatility(rows, 20)
            trend = _trend_score(rows, 20)
            latest = _safe_num(data.get("latest"))
            prev = _safe_num(data.get("prev"))
            chg_1d = (latest - prev) / prev if prev > 0 else None

            score = 0.0
            reasons: list[str] = []
            if ret20 is not None:
                if ret20 > 0.03:
                    score += 2
                    reasons.append(f"20日上涨{ret20:.1%}")
                elif ret20 < -0.03:
                    score -= 2
                    reasons.append(f"20日下跌{abs(ret20):.1%}")
            if vol20 is not None:
                if vol20 > 0.22:
                    score += 1
                    reasons.append("波动率偏高，期权机会多")
                elif vol20 < 0.12:
                    score -= 1
                    reasons.append("波动率偏低")
            if trend is not None:
                if trend > 0.6:
                    score += 1
                    reasons.append("趋势偏强")
                elif trend < 0.4:
                    score -= 1
                    reasons.append("趋势偏弱")
            if chg_1d is not None:
                if chg_1d > 0.005:
                    score += 0.5
                    reasons.append("昨日上涨")
                elif chg_1d < -0.005:
                    score -= 0.5
                    reasons.append("昨日下跌")

            self.items.append({
                "category": "股指期货",
                "name": name,
                "latest": latest,
                "latest_date": data.get("latest_date"),
                "chg_1d": chg_1d,
                "ret20": ret20,
                "vol20": vol20,
                "trend20": trend,
                "score": score,
                "reasons": reasons,
                "opportunity": "关注" if score >= 2 else ("观望" if score <= -2 else "中性"),
            })

    def scan_ai_power_core(self):
        """AI 算力核心观察池：6 个高优先级品种，叠加人工定性标注

        定性维度（用户给定）：
          - AI关联度：1-5 星
          - 供需紧度：极度紧缺 / 偏紧 / 中性 / 过剩
          - 当前位置：超跌反弹 / 回调较深 / 相对抗跌 / 历史低位 等
        量化叠加：RSI 超买超卖 + 最大回撤 + 趋势/波动
        """
        # 用户给定的人工标注层
        AI_CORE_ANNOTATIONS = {
            "锡": {
                "ai_relevance": 5,
                "supply_tightness": "极度紧缺",
                "position": "超跌反弹中",
                "manual_score": 5,
                "note": "AI焊料/半导体封装核心，供给刚性",
            },
            "铜": {
                "ai_relevance": 5,
                "supply_tightness": "偏紧",
                "position": "短期回调较深",
                "manual_score": 5,
                "note": "AI数据中心+电网+新能源共建需求",
            },
            "铝": {
                "ai_relevance": 4,
                "supply_tightness": "偏紧",
                "position": "相对抗跌",
                "manual_score": 4,
                "note": "AI轻量化+电力传输",
            },
            "银": {
                "ai_relevance": 4,
                "supply_tightness": "中性",
                "position": "贵金属+工业双支撑",
                "manual_score": 4,
                "note": "光伏银浆+避险双驱动",
            },
            "碳酸锂": {
                "ai_relevance": 3,
                "supply_tightness": "过剩→边际改善",
                "position": "历史低位",
                "manual_score": 3,
                "note": "储能/新能源，过剩产能出清中",
            },
            "多晶硅": {
                "ai_relevance": 3,
                "supply_tightness": "过剩→出清中",
                "position": "历史低位",
                "manual_score": 3,
                "note": "光伏+半导体上游，加入观察标的",
            },
        }

        # 把已有商品期货扫描结果中这 6 个品种挑出来，叠加定性层
        for item in self.items:
            if item.get("category") != "商品期货":
                continue
            name = item.get("name", "")
            if name not in AI_CORE_ANNOTATIONS:
                continue
            ann = AI_CORE_ANNOTATIONS[name]
            series = []
            # 重新取 series 用于 RSI/回撤
            for entry in self._raw_series.get(name, []):
                series.append(entry)
            rsi14 = _rsi(series, 14) if series else None
            max_dd = _max_drawdown(series, 20) if series else None

            # 量化评分沿用 scan_commodity_futures 的结果，再叠加定性加权
            quant_score = item.get("score", 0.0)
            # 定性基础分：manual_score / 5 * 3（最多 +3）
            qual_score = ann["manual_score"] / 5.0 * 3.0

            bonus = 0.0
            bonus_reasons: list[str] = []
            if rsi14 is not None:
                if rsi14 < 30:
                    bonus += 1.0
                    bonus_reasons.append(f"RSI{rsi14:.0f}超卖")
                elif rsi14 > 70:
                    bonus -= 0.5
                    bonus_reasons.append(f"RSI{rsi14:.0f}超买")
            if max_dd is not None and max_dd > 0.08:
                bonus += 0.5
                bonus_reasons.append(f"近20日回撤{max_dd:.1%}")

            final_score = round(quant_score + qual_score + bonus, 2)

            item.update({
                "category": "AI算力核心",
                "ai_relevance": "*" * ann["ai_relevance"],
                "supply_tightness": ann["supply_tightness"],
                "position": ann["position"],
                "manual_score": ann["manual_score"],
                "note": ann["note"],
                "rsi14": rsi14,
                "max_dd_20": max_dd,
                "quant_score": quant_score,
                "qual_score": round(qual_score, 2),
                "score": final_score,
                "reasons": item.get("reasons", []) + bonus_reasons + [f"AI关联度{ann['ai_relevance']}星"],
                "opportunity": "关注" if final_score >= 3 else ("观望" if final_score <= 0 else "中性"),
            })

    def scan_options_opportunity(self):
        """期权机会：基于指数波动率与趋势"""
        for item in self.items:
            if item.get("category") != "股指期货":
                continue
            vol20 = item.get("vol20")
            trend20 = item.get("trend20")
            if vol20 is None or trend20 is None:
                continue
            opt_score = 0.0
            reasons: list[str] = []
            if vol20 > 0.22:
                opt_score += 2
                reasons.append("高波动利好期权卖方/买方")
            if trend20 and trend20 > 0.65:
                opt_score += 1
                reasons.append("趋势明确适合方向性期权")
            if trend20 and trend20 < 0.35:
                opt_score += 0.5
                reasons.append("弱势环境适合 protective put")
            self.items.append({
                "category": "期权策略",
                "name": f"{item['name']}期权",
                "latest": item.get("latest"),
                "latest_date": item.get("latest_date"),
                "chg_1d": item.get("chg_1d"),
                "ret20": item.get("ret20"),
                "vol20": vol20,
                "trend20": trend20,
                "score": opt_score,
                "reasons": reasons,
                "opportunity": "关注" if opt_score >= 2 else ("观望" if opt_score <= -2 else "中性"),
            })

    def hedge_alignment(self):
        """与当前组合配置的对齐度"""
        portfolio_hints = {
            "棉花": "已有棉花期货/期权保护，波动放大时调整保护宽度",
            "黄金": "已有黄金ETF，可继续作为避险底仓",
            "沪深300": "已有IF对冲，关注基差与展期成本",
            "中证500": "已有IC对冲，关注中小盘相对强弱",
            "中证1000": "已有IM对冲，关注小盘流动性",
        }
        aligned = []
        for item in self.items:
            name = item.get("name", "")
            for key, hint in portfolio_hints.items():
                if key in name:
                    aligned.append({
                        "name": name,
                        "hint": hint,
                        "score": item.get("score", 0),
                        "opportunity": item.get("opportunity", "中性"),
                    })
        return aligned

    def run(self) -> dict[str, Any]:
        self.scan_commodity_futures()
        self.scan_ai_power_core()
        self.scan_index_futures()
        self.scan_options_opportunity()
        aligned = self.hedge_alignment()
        ranked = sorted(self.items, key=lambda x: x.get("score", 0), reverse=True)
        ai_core = [x for x in ranked if x.get("category") == "AI算力核心"]
        return {
            "report_time": self.report_time,
            "scanned": len(ranked),
            "ai_power_core": ai_core,
            "top_opportunities": [x for x in ranked if x.get("opportunity") == "关注"][:10],
            "neutral": [x for x in ranked if x.get("opportunity") == "中性"][:10],
            "watch_out": [x for x in ranked if x.get("opportunity") == "观望"][:10],
            "portfolio_alignment": aligned,
            "all": ranked,
        }


# ========================
# 报告输出
# ========================

def _fmt_num(v: Any) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)


def print_report(result: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("=" * 80)
    lines.append("  期货期权市场机会扫描报告")
    lines.append(f"  生成时间：{result.get('report_time')}")
    lines.append("=" * 80)
    lines.append("")
    lines.append("  数据说明：基于 iFinD EDB 现货/指数价格数据，数据截止日期以实际返回为准。")
    lines.append("  当前模型为初筛框架，尚未接入基差、IV/PCR、成交量持仓量等更细颗粒度数据。")
    lines.append("")

    lines.append("")
    lines.append("【AI算力核心观察池】高优先级品种（定性+量化叠加）")
    lines.append("-" * 80)
    ai_core = result.get("ai_power_core", [])
    if not ai_core:
        lines.append("  无")
    for item in ai_core:
        lines.append(f"  {item['name']} | AI关联度 {item.get('ai_relevance','')} | 供需 {item.get('supply_tightness','')} | 位置 {item.get('position','')}")
        lines.append(f"    最新价：{_fmt_num(item.get('latest'))}  日期：{item.get('latest_date')}")
        lines.append(f"    量化分：{_fmt_num(item.get('quant_score'))}  定性分：{_fmt_num(item.get('qual_score'))}  RSI14：{_fmt_num(item.get('rsi14'))}  20日回撤：{_fmt_num(item.get('max_dd_20'))}")
        lines.append(f"    综合评分：{_fmt_num(item.get('score'))}  判断：{item.get('opportunity')}")
        lines.append(f"    备注：{item.get('note','')}")
        if item.get("reasons"):
            lines.append(f"    原因：{'; '.join(item['reasons'])}")
        lines.append("")

    lines.append("")
    lines.append("【关注】有机会品种")
    lines.append("-" * 80)
    top = result.get("top_opportunities", [])
    if not top:
        lines.append("  无")
    for item in top:
        lines.append(f"  {item['category']} | {item['name']} | {item.get('tag', '')}")
        lines.append(f"    最新价/指数：{_fmt_num(item.get('latest'))}  日期：{item.get('latest_date')}")
        lines.append(f"    日涨跌：{_fmt_num(item.get('chg_1d'))}  20日收益：{_fmt_num(item.get('ret20'))}  20日波动：{_fmt_num(item.get('vol20'))}  20日趋势：{_fmt_num(item.get('trend20'))}")
        lines.append(f"    综合评分：{_fmt_num(item.get('score'))}  判断：{item.get('opportunity')}")
        if item.get("reasons"):
            lines.append(f"    原因：{'; '.join(item['reasons'])}")
        lines.append("")

    lines.append("")
    lines.append("【中性】暂无明显机会")
    lines.append("-" * 80)
    for item in result.get("neutral", []):
        lines.append(f"  {item['category']} | {item['name']} | {item.get('tag', '')} | 评分 {_fmt_num(item.get('score'))}")

    lines.append("")
    lines.append("【观望】风险较高或信号不足")
    lines.append("-" * 80)
    for item in result.get("watch_out", []):
        lines.append(f"  {item['category']} | {item['name']} | {item.get('tag', '')} | 评分 {_fmt_num(item.get('score'))}")

    lines.append("")
    lines.append("【组合对齐】与当前持仓/对冲的相关提示")
    lines.append("-" * 80)
    for item in result.get("portfolio_alignment", []):
        lines.append(f"  {item['name']} | {item['hint']} | 评分 {_fmt_num(item.get('score'))} | {item.get('opportunity')}")

    lines.append("")
    lines.append("=" * 80)
    lines.append("  说明：以上为基于 EDB 宏观/价格数据的初筛，不构成交易建议。")
    lines.append("  如需更细颗粒度，可补充期货主力合约基差、期权 IV/PCR、成交量持仓量等数据。")
    lines.append("=" * 80)
    return "\n".join(lines)


# ========================
# 主入口
# ========================

def main():
    scanner = FuturesOptionsScanner()
    result = scanner.run()
    report = print_report(result)

    out_dir = os.path.join(os.path.dirname(__file__), "reports")
    os.makedirs(out_dir, exist_ok=True)
    today = datetime.now().strftime("%Y%m%d")
    md_path = os.path.join(out_dir, f"futures_options_scan_{today}.md")
    json_path = os.path.join(out_dir, f"futures_options_scan_{today}.json")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write(report)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(report)
    print(f"\n报告已保存：{md_path}")
    print(f"JSON 已保存：{json_path}")


if __name__ == "__main__":
    main()
