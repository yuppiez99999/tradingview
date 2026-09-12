"""ETF 国家队资金监测模块 v2.0 — 集成版（移植自 10_第三方项目/etf-tracker）

v8.6 集成 (2026-09-09):
    原 `etf_tracker.py` (github.com/yuppiez99999/etf-tracker) 为独立脚本，
    本模块将其核心管线移植为主量化系统第一方工具，供以下入口复用：
      * CLI : 量化策略系统_统一入口_v8.6.py --etf-flow
      * UI  : ui/pages/06_💰_ETF资金流向.py
      * 模型: SocialSecurityETFTracker.analyze(flow_data) 真实资金流输入

数据源优先级（符合 AGENTS.md §6 全局标准）：
    P1 Wind MCP (HTTP直连, 前复权 qfq) → P3 akshare (新浪ETF历史) → P6 模拟数据兜底

零新增第三方依赖：仅 urllib (标准库) + akshare/pandas (系统已有)。

核心逻辑：
    1. 覆盖 24 只主流宽基/主题 ETF 实时行情 (Wind MCP fund_data)
    2. 通过 K线成交额 + 涨跌幅方向估算资金净流入
    3. 连续 N 日资金流趋势检测 → 国家队加仓/减仓信号 (2亿/10亿/50亿 三级阈值)
    4. 自动构建 ETF 成交额 TOP 排名 + Markdown 报告 + 每日归档
"""

from __future__ import annotations

import logging
import os
import random
import sys
from datetime import timedelta

from utils.datetime_utils import now_bj

logger = logging.getLogger(__name__)

# 国内金融 API 不走系统代理 (AGENTS.md §6 强制规则)
_NO_PROXY_DOMAINS = (
    "push2his.eastmoney.com,push2.eastmoney.com,eastmoney.com,"
    "sinajs.cn,sina.com.cn,mcp.wind.com.cn,wind.com.cn"
)
os.environ.setdefault("NO_PROXY", _NO_PROXY_DOMAINS)

# ==================== 数据源探测 ====================
WIND_FUND_ENDPOINT = "https://mcp.wind.com.cn/vserver_fund_data/mcp/"
_WIND_API_KEY_CACHE: str | None = None


def _get_wind_api_key() -> str | None:
    """Wind MCP API key: 环境变量 > ~/.wind-aifinmarket/config"""
    global _WIND_API_KEY_CACHE
    if _WIND_API_KEY_CACHE is not None:
        return _WIND_API_KEY_CACHE
    env_key = os.environ.get("WIND_API_KEY")
    if env_key:
        _WIND_API_KEY_CACHE = env_key.strip()
        return _WIND_API_KEY_CACHE
    cfg = os.path.join(os.path.expanduser("~"), ".wind-aifinmarket", "config")
    try:
        if os.path.isfile(cfg):
            with open(cfg, encoding="utf-8-sig") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if line.startswith("export "):
                        line = line[7:].strip()
                    if line.startswith("WIND_API_KEY="):
                        _WIND_API_KEY_CACHE = line.split("=", 1)[1].strip()
                        return _WIND_API_KEY_CACHE
    except OSError:
        pass
    _WIND_API_KEY_CACHE = None
    return None


def _detect_wind_available() -> bool:
    """Wind MCP 可用性 = API key 已配置（HTTP 直连模式）"""
    return _get_wind_api_key() is not None


def _detect_akshare() -> bool:
    try:
        import akshare  # noqa: F401

        return True
    except (ImportError, ModuleNotFoundError):
        return False


WIND_MCP_AVAILABLE = _detect_wind_available()
AK_AVAILABLE = _detect_akshare()


def source_enabled(source_mode: str, source_name: str) -> bool:
    """数据源开关: auto 模式下全部允许"""
    if source_mode == "auto":
        return True
    return source_mode == source_name


# ==================== 模拟数据 (P6 兜底, 永不崩溃) ====================
_MOCK_PRICE_MAP = {
    "510300": 3.85, "510310": 3.86, "159919": 3.84, "510500": 6.20,
    "510510": 6.18, "510050": 2.65, "510180": 6.85, "159915": 1.98,
    "159952": 1.97, "588000": 1.85, "588080": 1.84, "560010": 1.52,
    "512100": 1.53, "159647": 1.25, "515080": 1.45, "515180": 1.46,
    "512890": 1.38, "512880": 1.28, "512800": 1.15, "512170": 0.78,
    "512010": 0.82, "512760": 1.35, "515030": 1.42, "518880": 8.95,
}


def generate_mock_kline(code: str, days: int = 5) -> list[dict]:
    """生成模拟 K 线数据（演示/自检/兜底）"""
    base_price = _MOCK_PRICE_MAP.get(code, 2.0)
    kline = []
    for i in range(days):
        date = (now_bj() - timedelta(days=days - i - 1)).strftime("%Y-%m-%d")
        change_pct = random.uniform(-2, 3)
        base_price *= 1 + change_pct / 100
        amount = random.uniform(50_000_000, 500_000_000)  # 5000万 - 5亿
        kline.append(
            {
                "date": date,
                "close": round(base_price, 2),
                "change_pct": round(change_pct, 2),
                "volume": int(amount / base_price),
                "amount": amount,
                "net_flow": amount * (1 if change_pct >= 0 else -1),
            }
        )
    return kline


# ==================== 配置区 ====================
# (code, name, market, category) 24 只主流标的，与 etf-tracker CONFIG 保持一致
_ETF_TUPLE = [
    ("510300", "沪深300ETF华泰柏瑞", "sh", "宽基核心"),
    ("510310", "沪深300ETF易方达", "sh", "宽基核心"),
    ("159919", "沪深300ETF嘉实", "sz", "宽基核心"),
    ("510500", "中证500ETF南方", "sh", "宽基核心"),
    ("510510", "中证500ETF广发", "sh", "宽基核心"),
    ("510050", "上证50ETF华夏", "sh", "蓝筹核心"),
    ("510180", "上证180ETF华安", "sh", "蓝筹核心"),
    ("159915", "创业板ETF易方达", "sz", "成长科技"),
    ("159952", "创业板ETF广发", "sz", "成长科技"),
    ("588000", "科创50ETF华夏", "sh", "成长科技"),
    ("588080", "科创50ETF易方达", "sh", "成长科技"),
    ("560010", "中证1000ETF富国", "sh", "小盘风格"),
    ("512100", "中证1000ETF南方", "sh", "小盘风格"),
    ("159647", "国证2000ETF万家", "sz", "小盘风格"),
    ("515080", "中证红利ETF易方达", "sh", "防御红利"),
    ("515180", "中证红利ETF富国", "sh", "防御红利"),
    ("512890", "红利低波100ETF华泰柏瑞", "sh", "防御红利"),
    ("512880", "证券ETF国泰", "sh", "金融主题"),
    ("512800", "银行ETF华宝", "sh", "金融主题"),
    ("512170", "医疗ETF华宝", "sh", "医药主题"),
    ("512010", "医药ETF易方达", "sh", "医药主题"),
    ("512760", "半导体ETF国泰", "sh", "科技主题"),
    ("515030", "新能源车ETF华夏", "sh", "新能源主题"),
    ("518880", "黄金ETF华安", "sh", "避险资产"),
]

CONFIG: dict[str, object] = {
    "etf_list": [
        {"code": c, "name": n, "market": m, "category": cat}
        for c, n, m, cat in _ETF_TUPLE
    ],
    "state_keywords": ["中央汇金", "证金", "社保", "国家队", "中投"],
    "signal_threshold": {
        "high": 50_000_000_000,    # 50亿以上 - 高置信度（国家队级别资金）
        "medium": 10_000_000_000,  # 10亿以上 - 中等置信度
        "low": 2_000_000_000,      # 2亿以上 - 关注级别
    },
}

ETF_LIST: list[dict] = CONFIG["etf_list"]  # type: ignore[assignment]

# 报告目录 / 归档目录（默认主系统目录）
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORT_DIR = os.path.join(BASE_DIR, "reports")
# 归档根目录优先级: 显式参数 > 环境变量 ETF_TRACKER_ARCHIVE_DIR > 主系统每日报告归档
DEFAULT_ARCHIVE_DIR = os.path.join(BASE_DIR, "每日报告归档")
ARCHIVE_ENV_VAR = "ETF_TRACKER_ARCHIVE_DIR"


def ensure_dirs() -> None:
    os.makedirs(REPORT_DIR, exist_ok=True)


# ==================== Wind MCP (P1) — HTTP 直连 ====================
def _wind_http_fund(tool_name: str, params: dict) -> dict | None:
    """Wind MCP fund_data HTTP 直连 (urllib, 无新依赖)，返回解析后的 SSE JSON dict。"""
    api_key = _get_wind_api_key()
    if not api_key:
        return None
    import json as _json
    import urllib.request

    payload = _json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": params},
        }
    ).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    try:
        # 绕过系统代理 (国内 Wind 端点直连)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        req = urllib.request.Request(WIND_FUND_ENDPOINT, data=payload, headers=headers)
        resp = opener.open(req, timeout=60)
        text = resp.read().decode("utf-8", errors="replace")
    except (OSError, ValueError) as exc:  # Wind 网络/SSL/超时与响应解析异常降级到下一数据源, 留日志不静默
        logger.warning("Wind MCP 请求失败 (%s): %s", tool_name, exc)
        return None
    if not text or not text.strip():
        return None
    # SSE 解析: 提取 "data: {json}" 行
    for line in reversed(text.strip().split("\n")):
        line = line.strip()
        if line.startswith("data: "):
            try:
                parsed = _json.loads(line[6:])
                return parsed if isinstance(parsed, dict) else None
            except (ValueError, TypeError) as exc:
                logger.warning("Wind MCP SSE 行解析失败: %s", exc)
                return None
    try:
        parsed = _json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except (ValueError, TypeError) as exc:
        logger.warning("Wind MCP 响应体解析失败: %s", exc)
        return None


def _parse_fund_kline_rows(sse_data: dict) -> list[dict]:
    """解析 Wind MCP fund_data K线响应 → [{date, close, volume, amount}, ...]

    fund_data 字段: TIME / OPEN / MATCH(收盘) / HIGH / LOW / TURNOVER(成交额) / VOLUME(成交量)
    """
    if not isinstance(sse_data, dict):
        return []
    result = sse_data.get("result") or sse_data.get("data") or {}
    content = result.get("content") if isinstance(result, dict) else None
    if not content and isinstance(sse_data.get("result"), dict):
        content = sse_data["result"].get("content")
    if not content or not isinstance(content, list):
        return []
    text = content[0].get("text", "") if isinstance(content[0], dict) else str(content[0])
    import json as _json

    try:
        inner = _json.loads(text)
    except (ValueError, TypeError) as exc:
        logger.warning("Wind MCP K线内层 JSON 解析失败: %s", exc)
        return []
    data = inner.get("data", inner)
    rows = data.get("rows", [])
    if not rows:
        return []
    columns = [c["name"].upper() for c in data.get("columns", [])]
    idx = {name: i for i, name in enumerate(columns)}
    parsed = []
    for row in rows:
        time_val = row[idx["TIME"]] if "TIME" in idx else ""
        date_str = str(time_val)[:10]
        close = float(row[idx["MATCH"]]) if "MATCH" in idx and row[idx["MATCH"]] else 0
        volume = float(row[idx["VOLUME"]]) if "VOLUME" in idx and row[idx["VOLUME"]] else 0
        amount = float(row[idx["TURNOVER"]]) if "TURNOVER" in idx and row[idx["TURNOVER"]] else 0
        parsed.append({"date": date_str, "close": close, "volume": volume, "amount": amount})
    return parsed


def _fetch_wind_fund_kline(windcode: str, days: int) -> list[dict] | None:
    """通过 Wind MCP 获取 ETF K线 (前复权 price_type=1)，返回最近 days 个交易日"""
    end_date = now_bj()
    start_date = end_date - timedelta(days=int(days * 1.5) + 10)
    sse = _wind_http_fund(
        "get_fund_kline",
        {
            "windcode": windcode,
            "begin_date": start_date.strftime("%Y%m%d"),
            "end_date": end_date.strftime("%Y%m%d"),
            "price_type": 1,  # 前复权
        },
    )
    if not sse:
        return None
    rows = _parse_fund_kline_rows(sse)
    if not rows:
        return None
    return rows[-days:] if len(rows) > days else rows


# ==================== 数据获取层 ====================
def _etf_name(code: str) -> str:
    return next((e["name"] for e in ETF_LIST if e["code"] == code), code)


def get_etf_basic_info(code: str, market: str, source_mode: str = "auto") -> dict:
    """获取 ETF 实时行情 (Wind MCP P1 → akshare P3 → 模拟数据 P6)"""
    if source_enabled(source_mode, "wind") and WIND_MCP_AVAILABLE:
        try:
            rows = _fetch_wind_fund_kline(f"{code}.{market.upper()}", days=2)
            if rows and len(rows) >= 1:
                latest = rows[-1]
                close = latest["close"]
                prev_close = rows[-2]["close"] if len(rows) >= 2 else close
                change_pct = (close / prev_close - 1) * 100 if prev_close else 0.0
                return {
                    "code": code,
                    "name": _etf_name(code),
                    "latest_price": close,
                    "change_pct": round(change_pct, 2),
                    "volume": int(latest["volume"]) if latest["volume"] else None,
                    "amount": latest["amount"],
                    "source": "Wind MCP",
                }
        except (OSError, ValueError, TypeError, KeyError, IndexError) as exc:  # Wind 拉取/解析失败降级到 akshare/mock, 留日志不静默
            logger.warning("Wind MCP 实时行情失败 (%s), 降级: %s", code, exc)

    if source_enabled(source_mode, "akshare") and AK_AVAILABLE:
        try:
            import akshare as ak

            df = ak.fund_etf_hist_sina(symbol=f"{market}{code}")
            if df is not None and len(df) >= 2:
                latest = df.iloc[-1]
                prev_close = float(df.iloc[-2]["close"])
                close = float(latest["close"])
                change_pct = (close / prev_close - 1) * 100 if prev_close else 0.0
                return {
                    "code": code,
                    "name": _etf_name(code),
                    "latest_price": close,
                    "change_pct": round(change_pct, 2),
                    "volume": int(latest.get("volume", 0)) if "volume" in df.columns else None,
                    "amount": float(latest.get("amount", 0)) if "amount" in df.columns else 0,
                    "source": "akshare",
                }
        except (OSError, ValueError, TypeError, KeyError, IndexError, ImportError) as exc:  # akshare 拉取/解析失败降级到 mock, 留日志不静默
            logger.warning("akshare 实时行情失败 (%s), 降级: %s", code, exc)

    if source_enabled(source_mode, "mock"):
        row = generate_mock_kline(code, days=1)[-1]
        return {
            "code": code,
            "name": _etf_name(code),
            "latest_price": row["close"],
            "change_pct": row["change_pct"],
            "volume": row["volume"],
            "amount": row["amount"],
            "source": "模拟数据",
        }
    return {"code": code, "name": _etf_name(code), "error": "所有数据源不可用"}


def get_etf_kline(code: str, market: str, days: int = 5, source_mode: str = "auto") -> dict:
    """获取 ETF K线数据用于计算资金流 (Wind MCP P1 → akshare P3 → 模拟数据 P6)"""
    if source_enabled(source_mode, "wind") and WIND_MCP_AVAILABLE:
        try:
            rows = _fetch_wind_fund_kline(f"{code}.{market.upper()}", days=days + 1)
            if rows and len(rows) >= 1:
                kline_data = []
                for i, row in enumerate(rows):
                    close = row["close"]
                    prev_close = rows[i - 1]["close"] if i > 0 else close
                    change_pct = (close / prev_close - 1) * 100 if prev_close else 0.0
                    amount = row["amount"]
                    kline_data.append(
                        {
                            "date": row["date"],
                            "close": close,
                            "change_pct": round(change_pct, 2),
                            "volume": row["volume"],
                            "amount": amount,
                            "net_flow": amount * (1 if change_pct >= 0 else -1),
                        }
                    )
                return {"code": code, "kline": kline_data[-days:], "source": "Wind MCP"}
        except (OSError, ValueError, TypeError, KeyError, IndexError) as exc:  # Wind 拉取/解析失败降级到 akshare/mock, 留日志不静默
            logger.warning("Wind MCP K线失败 (%s), 降级: %s", code, exc)

    if source_enabled(source_mode, "akshare") and AK_AVAILABLE:
        try:
            import akshare as ak

            df = ak.fund_etf_hist_sina(symbol=f"{market}{code}")
            if df is not None and len(df) >= 2:
                df = df.assign(change_pct=lambda d: d["close"].pct_change().fillna(0) * 100)
                recent = df.tail(days).reset_index(drop=True)
                kline_data = []
                for _, row in recent.iterrows():
                    amount = float(row.get("amount", row.get("volume", 0)))
                    change_pct = float(row.get("change_pct", 0))
                    kline_data.append(
                        {
                            "date": str(row.get("date", "")),
                            "close": float(row.get("close", 0)),
                            "change_pct": round(change_pct, 2),
                            "volume": float(row.get("volume", 0)),
                            "amount": amount,
                            "net_flow": amount * (1 if change_pct >= 0 else -1),
                        }
                    )
                return {"code": code, "kline": kline_data, "source": "akshare"}
        except (OSError, ValueError, TypeError, KeyError, IndexError, ImportError) as exc:  # akshare 拉取/解析失败降级到 mock, 留日志不静默
            logger.warning("akshare K线失败 (%s), 降级: %s", code, exc)

    if source_enabled(source_mode, "mock"):
        return {"code": code, "kline": generate_mock_kline(code, days=days), "source": "模拟数据"}
    return {"code": code, "error": "所有数据源不可用"}


def get_etf_scale_ranking(etf_info: dict) -> list[dict]:
    """构建 ETF 规模/成交额 TOP 排名（复用已采集行情，避免重复请求）"""
    scale_list = []
    for etf in ETF_LIST:
        basic = etf_info.get(etf["code"], {})
        if "error" in basic or not basic:
            continue
        amount = basic.get("amount", 0) or 0
        scale_list.append(
            {
                "代码": etf["code"],
                "名称": basic.get("name", etf["name"]),
                "最新价": basic.get("latest_price", 0),
                "涨跌幅%": round(basic.get("change_pct", 0), 2),
                "成交额(亿)": round(amount / 1e8, 2) if amount else 0,
                "类别": etf.get("category", ""),
            }
        )
    scale_list.sort(key=lambda x: x.get("成交额(亿)", 0), reverse=True)
    return scale_list


# ==================== 分析层 ====================
MOCK_SOURCE_NAME = "模拟数据"


def calculate_fund_flow_summary(
    etf_list: list[dict], days: int = 5, source_mode: str = "auto"
) -> list[dict]:
    """计算每只ETF的N日资金流汇总 → 按窗口累计净流入降序列表

    每项字段兼容 UI 页与社保追踪器: code/name/category/price/change_pct/
    amount_yi/net_flow_yi/avg_net_flow_yi/positive_days/trend/source/daily_flows

    SC-10 (2026-09-12): Wind/akshare 均不可用时逐项落到 P6 模拟兜底,
    此前的信号/建议/归档三件套**不含任何顶层降级标记** —— 模拟数据的
    均匀随机净流 (5000万~5亿/日) 5 日累计期望即足以触发 50 亿
    "国家队强加仓信号" 级别的假信号 (实测 20 次 seed 试验 13 次越线)。
    现汇总层标记 ``mock_degraded`` (存在任一模拟源即 True), 由消费方
    (detect_state_fund_signals / build_investment_suggestion /
    generate_report) 透传展示。
    """
    results = []
    for etf in etf_list:
        code = etf["code"]
        kline_result = get_etf_kline(code, etf["market"], days=days, source_mode=source_mode)
        if "error" in kline_result:
            continue
        kline = kline_result["kline"]
        if not kline:
            continue

        total_net_flow = sum(k["net_flow"] for k in kline)
        avg_flow = total_net_flow / len(kline) if kline else 0
        positive_days = sum(1 for k in kline if k["net_flow"] > 0)
        latest_price = kline[-1]["close"]
        latest_change = kline[-1]["change_pct"]
        latest_amount = kline[-1]["amount"]

        trend = "中性"
        if all(k["net_flow"] > 0 for k in kline):
            trend = "连续流入"
        elif all(k["net_flow"] < 0 for k in kline):
            trend = "连续流出"
        elif positive_days >= len(kline) - 1:
            trend = "以流入为主"
        elif len(kline) - positive_days >= len(kline) - 1:
            trend = "以流出为主"

        results.append(
            {
                "code": code,
                "name": etf["name"],
                "category": etf.get("category", ""),
                "price": round(latest_price, 4),
                "change_pct": round(latest_change, 2),
                "amount_yi": round(latest_amount / 1e8, 2),  # 亿元
                "net_flow_yi": round(total_net_flow / 1e8, 2),
                "avg_net_flow_yi": round(avg_flow / 1e8, 2),
                "positive_days": f"{positive_days}/{len(kline)}",
                "trend": trend,
                "source": kline_result["source"],
                "mock_degraded": kline_result["source"] == MOCK_SOURCE_NAME,
                "daily_flows": [
                    {"date": k["date"], "flow_yi": round(k["net_flow"] / 1e8, 2)}
                    for k in kline
                ],
            }
        )
    results.sort(key=lambda x: x["net_flow_yi"], reverse=True)
    return results


def detect_state_fund_signals(flow_results: list) -> list:
    """检测国家队加仓/减仓信号 (三级阈值 50亿/10亿/2亿)"""
    signals = []
    for item in flow_results:
        total_flow_yi = item["net_flow_yi"]
        trend = item["trend"]
        if total_flow_yi >= 50 and trend in ["连续流入", "以流入为主"]:
            confidence, signal_type = "高", "强加仓信号"
        elif total_flow_yi >= 50:
            confidence, signal_type = "高", "大额加仓信号"
        elif total_flow_yi >= 10:
            confidence, signal_type = "中", "加仓信号"
        elif total_flow_yi >= 2:
            confidence, signal_type = "低", "关注信号"
        elif total_flow_yi <= -50:
            confidence, signal_type = "高", "强减仓信号"
        elif total_flow_yi <= -10:
            confidence, signal_type = "中", "减仓信号"
        elif total_flow_yi <= -2:
            confidence, signal_type = "低", "关注信号(流出)"
        else:
            continue
        # SC-10: 模拟数据不得生成"国家队加仓"级信号 —— 降级为"数据不可用"提示
        if item.get("mock_degraded"):
            signals.append(
                {
                    **item,
                    "signal_type": "信号不可用(模拟数据)",
                    "confidence": "数据降级",
                }
            )
            continue
        signals.append(
            {
                **item,
                "signal_type": signal_type,
                "confidence": confidence,
            }
        )
    return signals


def build_investment_suggestion(flow_results: list, signals: list) -> dict:
    """生成投资建议聚合: 整体趋势 / 高置信信号 / 类别风格轮动

    SC-10: 存在模拟数据源时 overall/style_rotation 标注 "(模拟数据)",
    并新增 ``mock_degraded`` 字段 —— 风格轮动"增持/减持"是对资金方向的
    指令性表述, 模拟数据下生成会误导调仓。
    """
    mock_degraded = any(f.get("mock_degraded") for f in flow_results)
    suffix = "(模拟数据)" if mock_degraded else ""
    total_net = sum(f["net_flow_yi"] for f in flow_results)
    overall = (
        ("净流入" if total_net > 0 else ("净流出" if total_net < 0 else "中性"))
        + suffix
    )
    high_signals = [s for s in signals if s["confidence"] == "高"]

    cat_flow: dict[str, float] = {}
    for f in flow_results:
        cat = f.get("category", "未知")
        cat_flow[cat] = cat_flow.get(cat, 0) + f["net_flow_yi"]
    style_rotation = {
        cat: ("增持" if v >= 30 else "减持" if v <= -30 else "持有")
        for cat, v in sorted(cat_flow.items())
    }

    return {
        "overall_trend": overall,
        "total_net_flow_yi": round(total_net, 2),
        "high_confidence_signals": high_signals,
        "style_rotation": style_rotation,
        "mock_degraded": mock_degraded,
    }


# ==================== 报告生成 ====================
def visualize_flow_bars(flow_data: list, max_bars: int = 50) -> str:
    """ASCII 可视化资金流柱状图 (正数 █ / 负数 ░)"""
    if not flow_data:
        return "无数据"
    max_abs = max(abs(d["flow_yi"]) for d in flow_data)
    if max_abs == 0:
        return "无数据"
    lines = []
    for d in flow_data:
        width = max(1, int(abs(d["flow_yi"]) / max_abs * max_bars))
        bar = "█" * width if d["flow_yi"] >= 0 else "░" * width
        sign = "+" if d["flow_yi"] >= 0 else "-"
        lines.append(
            f"  {d['date'][5:]} │ {bar:<{max_bars}}  [{sign}{abs(d['flow_yi']):.2f}亿]"
        )
    return "\n".join(lines)


def _signal_md(signals: list) -> str:
    """报告第二节: 国家队信号检测"""
    s_hi = [s for s in signals if s["confidence"] == "高"]
    s_med = [s for s in signals if s["confidence"] == "中"]
    s_low = [s for s in signals if s["confidence"] == "低"]
    lines = [
        "## 🔥 二、国家队信号检测",
        "",
        "> **信号判定规则**",
        "> - 🔴 **高置信度**：5日净流入 ≥ 50亿 或 ≤ -50亿，且连续资金趋势一致",
        "> - 🟡 **中置信度**：5日净流入 ≥ 10亿 或 ≤ -10亿",
        "> - 🟢 **低置信度**：5日净流入 ≥ 2亿 或 ≤ -2亿（关注级别）",
        "",
        f"**检测到 {len(signals)} 条潜在信号**",
        "",
    ]
    if s_hi:
        lines += ["### 🔴 高置信度信号",
                  "| ETF名称 | 代码 | 累计净流入(亿) | 资金趋势 | 信号 |",
                  "|---------|------|-------------|---------|------|"]
        for s in s_hi:
            lines.append(
                f"| {s['name']} | {s['code']} | **{s['net_flow_yi']:.2f}** | {s['trend']} | {s['signal_type']} |"
            )
        lines.append("")
    if s_med:
        lines += ["### 🟡 中置信度信号",
                  "| ETF名称 | 代码 | 累计净流入(亿) | 资金趋势 | 信号 |",
                  "|---------|------|-------------|---------|------|"]
        for s in s_med:
            lines.append(
                f"| {s['name']} | {s['code']} | {s['net_flow_yi']:.2f} | {s['trend']} | {s['signal_type']} |"
            )
        lines.append("")
    if s_low:
        lines += ["### 🟢 关注级信号",
                  "| ETF名称 | 代码 | 累计净流入(亿) | 资金趋势 |",
                  "|---------|------|-------------|---------|"]
        for s in s_low:
            lines.append(f"| {s['name']} | {s['code']} | {s['net_flow_yi']:.2f} | {s['trend']} |")
        lines.append("")
    return "\n".join(lines)


def generate_report(
    flow_results: list,
    signals: list,
    scale_ranking: list,
    window_days: int = 5,
    top_n: int = 15,
    report_time: str | None = None,
) -> str:
    """生成完整 Markdown 报告（与 etf-tracker 原格式保持一致）"""
    report_time = report_time or now_bj().strftime("%Y-%m-%d %H:%M:%S")
    total_inflow = sum(max(f["net_flow_yi"], 0) for f in flow_results)
    total_outflow = sum(-min(f["net_flow_yi"], 0) for f in flow_results)
    net_flow = total_inflow - total_outflow
    # SC-10: 模拟数据报告必须显式警示 (此前报告正文不区分真实/模拟)
    mock_degraded = any(f.get("mock_degraded") for f in flow_results)

    lines = [
        "# 📊 ETF 国家队资金监测报告", "",
        (
            "> ⚠️ **数据降级警示**：本次报告含模拟数据（Wind MCP 与 akshare "
            "均不可用），信号与资金流均非真实，不构成任何操作参考。"
            if mock_degraded
            else ""
        ),
        f"**生成时间**：{report_time}",
        f"**监测标的**：{len(ETF_LIST)} 只主流宽基ETF | **监测窗口**：{window_days} 日",
        "",
        "---", "",
        "## 📈 一、今日行情速览（按成交额排序）", "",
        "| 排名 | ETF名称 | 代码 | 最新价 | 涨跌幅 | 成交额(亿) | 类别 |",
        "|------|---------|------|--------|--------|-----------|------|",
    ]
    for i, etf in enumerate(scale_ranking[:top_n], 1):
        lines.append(
            f"| {i} | {etf['名称']} | {etf['代码']} | {etf['最新价']} | "
            f"{etf['涨跌幅%']:+.2f}% | {etf['成交额(亿)']:.2f} | {etf['类别']} |"
        )
    lines.append("")
    lines.append(_signal_md(signals))
    lines.append("")

    # 三、资金流向 TOP10
    lines += ["---", "", "## 💰 三、资金流向TOP10（国家队重点关注）", "",
              "**整体资金流向汇总**",
              f"- 📥 总净流入：**{total_inflow:.2f} 亿元**",
              f"- 📤 总净流出：**{total_outflow:.2f} 亿元**",
              f"- 📊 净资金流向：**{'净流入' if net_flow >= 0 else '净流出'} {abs(net_flow):.2f} 亿元**",
              "",
              "| 排名 | ETF名称 | 代码 | 类别 | 累计净流入(亿) | 日均净流入(亿) | 资金趋势 |",
              "|------|---------|------|------|-------------|---------------|---------|"]
    for i, f in enumerate(flow_results[:10], 1):
        lines.append(
            f"| {i} | {f['name']} | {f['code']} | {f['category']} | {f['net_flow_yi']:.2f} | "
            f"{f['avg_net_flow_yi']:.2f} | {f['trend']} |"
        )
    lines.append("")

    # 四、重点标的趋势图
    lines += ["---", "", "## 📊 四、重点标的资金流趋势图（近5日）", "",
              "**图例**：`█` 净流入 | `░` 净流出", ""]
    for f in flow_results[:5]:
        lines.append(
            f"### {f['name']} ({f['code']}) - {window_days}日净流入: **{f['net_flow_yi']:.2f}亿**"
        )
        lines.append("```")
        lines.append(visualize_flow_bars(f["daily_flows"]))
        lines.append("```")
        lines.append(f"资金趋势：**{f['trend']}**")
        lines.append("")

    # 五、风险提示 + 六、全量明细
    lines += ["---", "", "## ⚠️ 五、风险提示", "",
              "1. **资金流为估算值**：本系统通过K线成交额与涨跌幅估算资金流向，与ETF实际申购赎回数据存在差异",
              "2. **国家队动作的推断性**：大额资金流可能是国家队操作，也可能是机构、外资等其他主体",
              "3. **滞后性**：本系统为T+1监测，不包含实时交易信号",
              "4. **仅供参考**：本报告不构成投资建议，投资需谨慎", "",
              "---", "", "## 📋 六、全量监测标的明细", "",
              "| 代码 | ETF名称 | 类别 | 最新价 | 涨跌幅% | 成交额(亿) | 累计净流入(亿) | 正流天数 | 资金趋势 |",
              "|------|---------|------|--------|---------|-----------|-------------|---------|---------|"]
    for item in flow_results:
        lines.append(
            f"| {item['code']} | {item['name']} | {item['category']} | {item['price']} | "
            f"{item['change_pct']}% | {item['amount_yi']} | {item['net_flow_yi']} | "
            f"{item['positive_days']} | {item['trend']} |"
        )
    lines += ["", "---", "",
              "*本报告由 ETF 国家队监测系统 v2.0 (集成版) 自动生成*",
              "*数据来源于 Wind MCP / akshare，资金流为估算值，仅供参考*",
              f"*生成时间：{report_time}*",
              "*数据仅供参考，不构成投资建议*"]
    return "\n".join(lines)


def archive_to_daily_reports(report_content: str, archive_root: str | None = None) -> str:
    """归档报告至每日报告归档目录 (YYYY-MM-DD/ETF资金监测_YYYYMMDD.md)"""
    today = now_bj().strftime("%Y-%m-%d")
    if archive_root is None:
        archive_root = os.environ.get(ARCHIVE_ENV_VAR, DEFAULT_ARCHIVE_DIR)
    archive_dir = os.path.join(archive_root, today)
    os.makedirs(archive_dir, exist_ok=True)
    archive_file = os.path.join(
        archive_dir, f"ETF资金监测_{now_bj().strftime('%Y%m%d')}.md"
    )
    with open(archive_file, "w", encoding="utf-8") as f:
        f.write(report_content)
    return archive_file


# ==================== 主类 ====================
class ETFFundFlowTracker:
    """ETF 国家队资金监测器（集成版）— 兼容旧 ETFFundFlowMonitor 接口

    供 UI 页 06 / 统一入口 --etf-flow / 社保追踪 consume 复用。
    方法: analyze_fund_flow / detect_signals / get_investment_suggestion /
          generate_report / run / archive
    """

    ETF_LIST: list[dict] = ETF_LIST

    def __init__(
        self,
        days: int = 5,
        source: str = "auto",
        top_n: int = 15,
        archive_dir: str | None = None,
    ) -> None:
        self.days = days
        self.source = source
        self.top_n = top_n
        self.archive_dir = archive_dir
        self.flow_results: list[dict] = []
        self.signals: list[dict] = []
        self.suggestions: dict = {}
        self.report: str = ""
        self.flow_data: dict = {}  # {code: {...}} 兼容 _build_etf_flow_data
        self.scale_ranking: list[dict] = []

    def analyze_fund_flow(self, source_mode: str | None = None) -> dict:
        """取数 + 资金流汇总 + 规模排名，返回 flow_data dict"""
        mode = source_mode or self.source
        self.flow_results = calculate_fund_flow_summary(
            ETF_LIST, days=self.days, source_mode=mode
        )
        self.flow_data = {
            f["code"]: {
                "name": f["name"],
                "category": f["category"],
                "net_flow_yi": f["net_flow_yi"],
                "trend": f["trend"],
                "price": f["price"],
                "change_pct": f["change_pct"],
                "amount_yi": f["amount_yi"],
                "daily_flows": f["daily_flows"],
                # SC-10: 消费方 (SocialSecurityETFTracker.analyze) 依赖
                # flow_data 感知数据降级 —— 此前该字段链路断裂
                "source": f.get("source", ""),
                "mock_degraded": bool(f.get("mock_degraded")),
            }
            for f in self.flow_results
        }
        self.scale_ranking = get_etf_scale_ranking(
            {
                f["code"]: {
                    "name": f["name"],
                    "latest_price": f["price"],
                    "change_pct": f["change_pct"],
                    "amount": f["amount_yi"] * 1e8,
                }
                for f in self.flow_results
            }
        )
        return self.flow_data

    def detect_signals(self) -> list[dict]:
        """基于已汇总的资金流检测国家队信号"""
        self.signals = detect_state_fund_signals(self.flow_results)
        return self.signals

    def get_investment_suggestion(self) -> dict:
        """投资建议聚合（整体趋势/高置信信号/风格轮动）"""
        self.suggestions = build_investment_suggestion(self.flow_results, self.signals)
        return self.suggestions

    def generate_report(self) -> str:
        """生成 Markdown 报告并返回"""
        self.report = generate_report(
            self.flow_results,
            self.signals,
            self.scale_ranking,
            window_days=self.days,
            top_n=self.top_n,
        )
        return self.report

    def archive(self, content: str | None = None) -> str | None:
        """归档报告至每日报告归档目录"""
        if content is None:
            content = self.report or self.generate_report()
        if not content.strip():
            return None
        return archive_to_daily_reports(content, archive_root=self.archive_dir)

    def run(self, archive: bool = True) -> tuple:
        """端到端运行: 取数 → 信号 → 建议 → 报告 → (可选)归档

        Returns:
            (report_path, report_content)
        """
        self.analyze_fund_flow()
        self.detect_signals()
        self.get_investment_suggestion()
        self.report = self.generate_report()
        ensure_dirs()
        report_file = os.path.join(
            REPORT_DIR, f"report_{now_bj().strftime('%Y%m%d')}.md"
        )
        with open(report_file, "w", encoding="utf-8") as f:
            f.write(self.report)
        with open(os.path.join(REPORT_DIR, "latest.md"), "w", encoding="utf-8") as f:
            f.write(self.report)
        if archive:
            self.archive(self.report)
        return report_file, self.report


def run_tracker(
    days: int = 5,
    source: str = "auto",
    top_n: int = 15,
    archive_dir: str | None = None,
    no_archive: bool = False,
) -> tuple:
    """模块级便捷入口（= 独立脚本 python utils/etf_fund_tracker.py 的等价调用）"""
    tracker = ETFFundFlowTracker(
        days=days, source=source, top_n=top_n, archive_dir=archive_dir
    )
    return tracker.run(archive=not no_archive)


def main(argv: list[str] | None = None) -> None:
    import argparse

    # Windows 控制台 GBK 下打印 emoji 会报错 → 强制 UTF-8
    try:
        reconfigure = getattr(sys.stdout, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    parser = argparse.ArgumentParser(description="ETF 国家队资金监测系统 (集成版)")
    parser.add_argument("--days", type=int, default=5, help="资金流监测窗口天数")
    parser.add_argument(
        "--source",
        default="auto",
        choices=["auto", "wind", "akshare", "mock"],
        help="数据源模式: auto(降级) / wind / akshare / mock(演示)",
    )
    parser.add_argument("--top", type=int, default=15, help="报告展示排名条数")
    parser.add_argument("--no-archive", action="store_true", help="不归档到每日报告目录")
    parser.add_argument(
        "--archive-dir", default=None, help="归档根目录 (默认: 主系统每日报告归档)"
    )
    args = parser.parse_args(argv)
    report_file, _ = run_tracker(
        days=args.days,
        source=args.source,
        top_n=args.top,
        archive_dir=args.archive_dir,
        no_archive=args.no_archive,
    )
    print(f"\n✅ 报告生成完成: {report_file}")


if __name__ == "__main__":
    main()
