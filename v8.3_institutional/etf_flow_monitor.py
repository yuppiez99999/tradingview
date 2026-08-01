"""
实时ETF资金流向监控 + 盘后报告
数据源: iFinD MCP > Wind MCP > tushare > yfinance
功能: 盘中每15分钟决策，盘后生成完整报告
集成: 28-终极量化交易系统7.1
"""

import csv
import json
import logging
import os
import re
import sys
from datetime import datetime
from typing import Dict, List, Optional

try:
    import requests
    REQUESTS_AVAILABLE = True
except Exception:
    REQUESTS_AVAILABLE = False

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    stream=sys.stdout,
)
logger = logging.getLogger('etf_flow')

os.environ['NO_PROXY'] = '*'
os.environ['HTTP_PROXY'] = ''
os.environ['HTTPS_PROXY'] = ''
os.environ['http_proxy'] = ''
os.environ['https_proxy'] = ''

# 项目根目录（兼容原路径和新路径）
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_CACHE_DIR = os.path.join(_BASE_DIR, 'data', 'cache')

# 兼容旧路径（11_量化策略）
_OLD_BASE_DIR = os.path.abspath(os.path.join(_BASE_DIR, '..', '..', '11_量化策略'))
if os.path.exists(_OLD_BASE_DIR):
    _CACHE_DIR = os.path.join(_OLD_BASE_DIR, 'data', 'cache')

try:
    import tushare as ts
    TUSHARE_AVAILABLE = True
except Exception:
    TUSHARE_AVAILABLE = False

try:
    YFINANCE_AVAILABLE = True
except Exception:
    YFINANCE_AVAILABLE = False

# ============ 重试工具 ============

def _retry_call(fn, *args, retries: int = 2, delay: float = 0.5, **kwargs):
    """轻量重试：失败后短暂休眠并重试，仍失败则返回 None"""
    last_exc = None
    for _ in range(max(retries, 1)):
        try:
            result = fn(*args, **kwargs)
            if result is not None:
                return result
        except Exception as exc:
            last_exc = exc
        import time
        time.sleep(delay)
    if last_exc:
        logger.debug(f"重试耗尽: {fn.__name__} -> {last_exc}")
    return None


# ============ 数据源：iFinD MCP (安全版本) ============
# 安全修复: 禁止从配置文件读取 Token,仅允许环境变量
_IFIND_TOKEN = os.environ.get("IFIND_TOKEN", "")
IFIND_AVAILABLE = bool(_IFIND_TOKEN)
IFIND_CLIENT = None
if IFIND_AVAILABLE:
    try:
        import importlib.util
        skill_dir = os.path.join(os.path.expanduser("~"), ".trae", "skills", "ifind-finance-data")
        call_path = os.path.join(skill_dir, "call.py")
        spec = importlib.util.spec_from_file_location("ifind_call", call_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        IFIND_CLIENT = mod
        logger.info("iFinD MCP 连接器加载成功")
    except Exception as e:
        IFIND_CLIENT = None
        logger.warning(f"iFinD MCP 连接器不可用: {e}")

def _exec_ifind(server_type: str, tool_name: str, params: dict) -> dict:
    """调用 iFinD MCP API"""
    if not IFIND_CLIENT:
        return {"error": "iFinD MCP 不可用"}
    try:
        result = IFIND_CLIENT.call(server_type, tool_name, params)
        if isinstance(result, dict) and result.get("error"):
            return {"error": result["error"].get("message", str(result["error"])[:200])}
        if isinstance(result, dict) and result.get("data"):
            return {"data": result["data"], "source": "iFinD MCP"}
        return result
    except Exception as e:
        return {"error": str(e)}

# iFinD MCP (最高优先) + Wind MCP (次优先)
try:
    import sys as _sys
    _strat_dir = os.path.dirname(os.path.abspath(__file__))
    if _strat_dir not in _sys.path:
        _sys.path.insert(0, _strat_dir)
    from wind_mcp_fetcher import wind_get_quote
    WIND_MCP_AVAILABLE = True
except Exception:
    WIND_MCP_AVAILABLE = False


def _load_local_price(etf_code: str) -> Optional[Dict]:
    """从本地缓存读取ETF最近价格数据（纯标准库）"""
    candidates = [
        os.path.join(_CACHE_DIR, f'price_{etf_code}_daily.csv'),
        os.path.join(_CACHE_DIR, f'price_{etf_code}.SH_daily.csv'),
        os.path.join(_CACHE_DIR, f'price_{etf_code}.SZ_daily.csv'),
        os.path.join(_CACHE_DIR, f'price_{etf_code}_daily.json'),
        os.path.join(_CACHE_DIR, f'price_{etf_code}.SH_daily.json'),
        os.path.join(_CACHE_DIR, f'price_{etf_code}.SZ_daily.json'),
    ]

    rows = []
    used_path = None
    for path in candidates:
        if path.endswith('.csv') and os.path.exists(path):
            try:
                with open(path, encoding='utf-8-sig') as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        rows.append({
                            'date': (row.get('date') or '').strip(),
                            'close': float(row.get('close', 'nan') or 'nan'),
                        })
                used_path = path
                break
            except Exception:
                rows = []
        elif path.endswith('.json') and not rows and os.path.exists(path):
            try:
                with open(path, encoding='utf-8') as f:
                    raw = json.load(f)
                if isinstance(raw, list):
                    rows = [{
                        'date': str(item.get('date', '')).strip(),
                        'close': float(item.get('close', 'nan') or 'nan'),
                    } for item in raw]
                used_path = path
                break
            except Exception:
                rows = []

    valid = [r for r in rows if r['date'] and r['close'] == r['close'] and r['close'] > 0]
    if not valid:
        return None

    valid.sort(key=lambda x: x['date'])
    latest = valid[-1]['close']
    prev = valid[-2]['close'] if len(valid) > 1 else latest

    # 近5日平均收盘价
    recent = valid[-5:] if len(valid) >= 5 else valid
    avg_close = sum(r['close'] for r in recent) / len(recent)

    return {
        'latest': latest,
        'prev': prev,
        'change_pct': (latest - prev) / prev * 100 if prev > 0 else 0.0,
        'avg_close': avg_close,
        'source': 'local_cache',
        'cache_path': used_path,
    }

def _parse_ifind_fund_quotes(ifind_data: dict) -> Optional[Dict]:
    """
    解析 iFinD fund_highfreq_quotes 的嵌套返回结构。
    返回字段: price / change_pct / volume / amount_yi
    """
    try:
        inner = ifind_data.get("result", {}).get("content", [{}])[0].get("text")
        if not inner:
            return None
        outer = json.loads(inner)
        if not isinstance(outer, dict) or outer.get("code") != 1 or not outer.get("data"):
            return None
        payload = json.loads(outer["data"]) if isinstance(outer["data"], str) else outer["data"]
        tables = payload.get("tables") or []
        if len(tables) < 2:
            return None
        row = tables[-1]
        if len(row) < 7:
            return None
        latest_price = float(row[3] or 0)
        change_pct = float(row[4] or 0)
        amount = float(row[5] or 0)
        volume = int(float(row[6] or 0))
        return {
            "price": latest_price,
            "change_pct": change_pct * 100,
            "volume": volume,
            "amount_yi": round(amount * 1e-8, 2),
        }
    except Exception:
        return None


def _fetch_sina_quote(etf_code: str) -> Optional[Dict]:
    """
    通过新浪财经接口获取 ETF 实时行情。
    返回字段: price / change_pct / volume / amount_yi
    """
    if not REQUESTS_AVAILABLE:
        return None

    def _call() -> Optional[Dict]:
        market = "sh" if etf_code.startswith("5") or etf_code.startswith("51") or etf_code.startswith("58") else "sz"
        symbol = f"{market}{etf_code}"
        url = f"https://hq.sinajs.cn/list={symbol}"
        headers = {"Referer": "https://finance.sina.com.cn"}
        resp = requests.get(url, headers=headers, timeout=10)
        text = resp.text.strip()
        match = re.search(r'"(.*)"', text)
        if not match:
            return None
        fields = match.group(1).split(",")
        if len(fields) < 10:
            return None
        name = fields[0]
        prev_close = float(fields[2] or 0)
        price = float(fields[3] or 0)
        float(fields[4] or 0)
        float(fields[5] or 0)
        volume = int(float(fields[8] or 0))
        amount = float(fields[9] or 0)
        change_pct = ((price - prev_close) / prev_close * 100) if prev_close > 0 else 0.0
        return {
            "name": name,
            "price": price,
            "change_pct": round(change_pct, 2),
            "volume": volume,
            "amount_yi": round(amount * 1e-8, 2),
        }

    return _retry_call(_call, retries=2, delay=0.5)


# 国家队关注的ETF列表
NATIONAL_TEAM_ETFS = [
    {"code": "510050", "name": "上证50ETF华夏", "category": "宽基"},
    {"code": "510300", "name": "沪深300ETF华泰柏瑞", "category": "宽基"},
    {"code": "510500", "name": "中证500ETF南方", "category": "宽基"},
    {"code": "588000", "name": "科创50ETF华夏", "category": "成长科技"},
    {"code": "588080", "name": "科创50ETF易方达", "category": "成长科技"},
    {"code": "512760", "name": "半导体ETF国泰", "category": "科技主题"},
    {"code": "512880", "name": "证券ETF国泰", "category": "金融主题"},
    {"code": "512800", "name": "银行ETF华宝", "category": "金融主题"},
    {"code": "518880", "name": "黄金ETF华安", "category": "避险资产"},
    {"code": "512170", "name": "医疗ETF华宝", "category": "医药主题"},
    {"code": "515030", "name": "新能源车ETF华夏", "category": "新能源主题"},
    {"code": "159915", "name": "创业板ETF易方达", "category": "成长科技"},
    {"code": "512100", "name": "中证1000ETF南方", "category": "小盘风格"},
]

# 信号阈值（亿元）
SIGNAL_THRESHOLDS = {
    "high": 50,     # 强信号
    "medium": 10,   # 中信号
    "low": 2,       # 关注信号
}

# ETF -> 相关个股映射（用于交易决策）
ETF_TO_STOCKS = {
    "510050": {"板块": "上证50大盘蓝筹", "个股票池": ["600036", "601318", "600519", "600276", "601166"]},
    "510300": {"板块": "沪深300核心资产", "个股票池": ["600036", "600276", "601088", "002648", "600346"]},
    "510500": {"板块": "中盘成长", "个股票池": ["002493", "000301", "601233", "603225", "000059"]},
    "588000": {"板块": "科创板科技", "个股票池": ["688041", "300308", "688017", "002371"]},
    "588080": {"板块": "科创板科技", "个股票池": ["688041", "300308", "688017", "002371"]},
    "512760": {"板块": "半导体", "个股票池": ["688041", "002371", "300308"]},
    "512880": {"板块": "券商", "个股票池": ["600030", "601211", "600837"]},
    "512800": {"板块": "银行", "个股票池": ["600036", "601166", "000001"]},
    "518880": {"板块": "黄金避险", "个股票池": ["600489", "601899", "601547"]},
    "512170": {"板块": "医疗医药", "个股票池": ["600276", "300760", "603259"]},
    "515030": {"板块": "新能源", "个股票池": ["300274", "600875", "600089"]},
    "159915": {"板块": "创业板成长", "个股票池": ["300274", "300308", "300760"]},
    "512100": {"板块": "小盘风格", "个股票池": ["688017", "603225", "000425"]},
}

# ETF -> 相关ETF替代品映射
ETF_TO_RELATED = {
    "510050": ["510300", "510500"],
    "510300": ["510050", "510500", "512100"],
    "588000": ["588080", "159915", "512760"],
    "588080": ["588000", "159915", "512760"],
    "512760": ["588000", "515030"],
    "518880": ["159980"],  # 黄金ETF -> 有色ETF
    "512170": ["512290"],  # 医疗 -> 生物医药ETF
}


class ETFRealTimeTracker:
    """实时ETF资金流向追踪器 + 盘后报告生成"""

    def __init__(self):
        self.pro = None
        if TUSHARE_AVAILABLE:
            try:
                self.pro = ts.pro_api()
                logger.info("tushare API 初始化成功")
            except Exception as e:
                logger.warning(f"tushare API 初始化失败: {e}")

        # iFinD MCP 连接测试
        self.ifind_ok = False
        if IFIND_AVAILABLE:
            try:
                test = _exec_ifind(
                    "fund",
                    "fund_highfreq_quotes",
                    {
                        "symbols": "510300",
                        "indicators": "最新价,涨跌幅,成交额,成交量",
                        "data_mode": "real_time",
                        "interval": 1,
                    },
                )
                self.ifind_ok = isinstance(test, dict) and bool(test.get("data")) and not test.get("error")
                logger.info(f"iFinD MCP {'连接成功' if self.ifind_ok else '不可用'}")
            except Exception as e:
                logger.warning(f"iFinD MCP 初始化失败: {e}")

        # Wind MCP 连接测试
        self.wind_ok = False
        if WIND_MCP_AVAILABLE:
            try:
                wind_data = wind_get_quote('510300', is_fund=True)
                self.wind_ok = wind_data is not None and wind_data.get('price', 0) > 0
                logger.info(f"Wind MCP {'连接成功' if self.wind_ok else '不可用'}")
            except Exception as e:
                logger.warning(f"Wind MCP 初始化失败: {e}")

        self._flow_cache: Dict[str, Dict] = {}
        self._source_stats: Dict[str, int] = {}

    def _record_source(self, source: Optional[str]) -> None:
        if source:
            self._source_stats[source] = self._source_stats.get(source, 0) + 1

    def get_source_stats(self) -> Dict[str, int]:
        return dict(self._source_stats)

    def primary_source(self) -> Optional[str]:
        if not self._source_stats:
            return None
        return max(self._source_stats.items(), key=lambda item: item[1])[0]

    def get_etf_fund_flow(self, etf_code: str) -> Optional[Dict]:
        """获取ETF资金流向数据（含单次运行缓存）"""
        if etf_code in self._flow_cache:
            return self._flow_cache[etf_code]

        result = self._fetch_etf_fund_flow(etf_code)
        if result is not None:
            self._flow_cache[etf_code] = result
        return result

    def _fetch_etf_fund_flow(self, etf_code: str) -> Optional[Dict]:
        """实际获取ETF资金流向数据"""
        # 优先：iFinD MCP
        if self.ifind_ok and IFIND_AVAILABLE:
            try:
                ifind_data = _exec_ifind(
                    "fund",
                    "fund_highfreq_quotes",
                    {
                        "symbols": etf_code,
                        "indicators": "最新价,涨跌幅,成交额,成交量",
                        "data_mode": "real_time",
                        "interval": 1,
                    },
                )
                if isinstance(ifind_data, dict) and ifind_data.get("data") and not ifind_data.get("error"):
                    parsed = _parse_ifind_fund_quotes(ifind_data["data"])
                    if parsed:
                        result = {
                            "code": etf_code,
                            "name": "",
                            "net_flow_yi": parsed["amount_yi"],
                            "change_pct": parsed["change_pct"],
                            "volume": parsed["volume"],
                            "amount_yi": parsed["amount_yi"],
                            "trend": "流入" if parsed["amount_yi"] > 0 else "流出" if parsed["amount_yi"] < 0 else "中性",
                            "source": "ifind_mcp",
                        }
                        for etf in NATIONAL_TEAM_ETFS:
                            if etf["code"] == etf_code:
                                result["name"] = etf["name"]
                                result["category"] = etf["category"]
                                break
                        self._record_source(result.get("source"))
                        return result
            except Exception:
                pass

        # 次优先：Wind MCP
        if self.wind_ok and WIND_MCP_AVAILABLE:
            try:
                wind_data = wind_get_quote(etf_code, is_fund=True)
                if wind_data and wind_data.get('price', 0) > 0:
                    net_flow = wind_data.get("net_flow")
                    amount = wind_data.get("amount")
                    result = {
                        "code": etf_code,
                        "name": "",
                        "net_flow_yi": round(net_flow * 1e-8, 2) if net_flow else 0.0,
                        "change_pct": wind_data.get("change", wind_data.get("change_pct", 0)),
                        "volume": wind_data.get("volume", 0),
                        "amount_yi": round(amount * 1e-8, 2) if amount else 0.0,
                        "trend": "流入" if (net_flow or 0) > 0 else "流出" if (net_flow or 0) < 0 else "中性",
                        "source": "wind_mcp",
                    }
                    for etf in NATIONAL_TEAM_ETFS:
                        if etf["code"] == etf_code:
                            result["name"] = etf["name"]
                            result["category"] = etf["category"]
                            break
                    self._record_source(result.get("source"))
                    return result
            except Exception:
                pass

        # 备用：新浪财经
        try:
            sina_data = _fetch_sina_quote(etf_code)
            if sina_data and sina_data.get("price", 0) > 0:
                result = {
                    "code": etf_code,
                    "name": sina_data.get("name", ""),
                    "net_flow_yi": sina_data.get("amount_yi", 0.0),
                    "change_pct": sina_data.get("change_pct", 0.0),
                    "volume": sina_data.get("volume", 0),
                    "amount_yi": sina_data.get("amount_yi", 0.0),
                    "trend": "流入" if sina_data.get("amount_yi", 0) > 0 else "流出" if sina_data.get("amount_yi", 0) < 0 else "中性",
                    "source": "sina",
                }
                for etf in NATIONAL_TEAM_ETFS:
                    if etf["code"] == etf_code:
                        result.setdefault("category", etf["category"])
                        break
                self._record_source(result.get("source"))
                return result
        except Exception:
            pass

        # 真实数据失败时直接跳过，不输出模拟/缓存/估算结果
        return None

    def detect_signals(self, flow_data: Dict) -> List[Dict]:
        """检测国家队资金信号"""
        signals = []

        for code, data in flow_data.items():
            net_flow = data.get("net_flow_yi", 0)

            # 信号强度判定
            if net_flow >= SIGNAL_THRESHOLDS["high"]:
                confidence = "高"
                signal_type = "国家队强加仓信号"
            elif net_flow >= SIGNAL_THRESHOLDS["medium"]:
                confidence = "中"
                signal_type = "国家队加仓信号"
            elif net_flow >= SIGNAL_THRESHOLDS["low"]:
                confidence = "低"
                signal_type = "国家队关注信号"
            elif net_flow <= -SIGNAL_THRESHOLDS["high"]:
                confidence = "高"
                signal_type = "国家队强减仓信号"
            elif net_flow <= -SIGNAL_THRESHOLDS["medium"]:
                confidence = "中"
                signal_type = "国家队减仓信号"
            elif net_flow <= -SIGNAL_THRESHOLDS["low"]:
                confidence = "低"
                signal_type = "国家队减持关注"
            else:
                continue

            signals.append({
                "code": code,
                "name": data.get("name", code),
                "category": data.get("category", "未知"),
                "net_flow_yi": net_flow,
                "change_pct": data.get("change_pct", 0),
                "trend": data.get("trend", "中性"),
                "signal_type": signal_type,
                "confidence": confidence,
                "source": data.get("source", "未知"),
            })

        # 排序：置信度 > 净流入金额
        signals.sort(key=lambda x: (
            0 if x["confidence"] == "高" else 1 if x["confidence"] == "中" else 2,
            -abs(x["net_flow_yi"])
        ))

        return signals

    def generate_report(self, signals: List[Dict], flow_data: Dict) -> str:
        """生成实时资金流向报告"""
        lines = []
        lines.append("# 实时ETF资金流向监控报告")
        lines.append("")
        lines.append(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"**监控标的**: {len(flow_data)} 只ETF")
        lines.append(f"**数据来源**: {'iFinD MCP' if any(d.get('source') == 'ifind_mcp' for d in flow_data.values()) else 'Wind MCP' if any(d.get('source') == 'wind_mcp' for d in flow_data.values()) else 'tushare' if any(d.get('source') == 'tushare' for d in flow_data.values()) else 'yfinance' if any(d.get('source') == 'yfinance' for d in flow_data.values()) else 'sina' if any(d.get('source') == 'sina' for d in flow_data.values()) else 'local_cache' if any(d.get('source') == 'local_cache' for d in flow_data.values()) else '模拟数据'}")
        lines.append("")
        lines.append("---")
        lines.append("")

        # 资金流向概况
        total_flow = sum(d["net_flow_yi"] for d in flow_data.values())
        overall_trend = "净流入" if total_flow > 0 else "净流出" if total_flow < 0 else "平衡"

        lines.append("## 一、资金流向概况")
        lines.append("")
        lines.append(f"- **整体态势**: {overall_trend}")
        lines.append(f"- **今日净流入**: {total_flow:+.2f} 亿元")
        lines.append(f"- **强信号数量**: {len([s for s in signals if s['confidence'] == '高'])} 条")
        lines.append("")

        # 信号详情
        if signals:
            lines.append("## 二、国家队资金信号")
            lines.append("")
            lines.append("| ETF名称 | 代码 | 净流入(亿) | 涨跌幅 | 信号类型 | 置信度 |")
            lines.append("|---------|------|-----------|--------|---------|--------|")
            for s in signals[:10]:
                "green" if s["net_flow_yi"] > 0 else "red"
                lines.append(f"| {s['name']} | {s['code']} | {s['net_flow_yi']:+.2f} | {s['change_pct']:+.2f}% | {s['signal_type']} | {s['confidence']} |")
            lines.append("")

        # ETF资金流向排行
        lines.append("## 三、ETF资金流向排行")
        lines.append("")
        sorted_flows = sorted(flow_data.items(), key=lambda x: -abs(x[1]["net_flow_yi"]))
        lines.append("| 排名 | ETF名称 | 净流入(亿) | 涨跌幅 | 成交额(亿) |")
        lines.append("|------|---------|-----------|--------|-----------|")
        for i, (_code, data) in enumerate(sorted_flows[:15], 1):
            arrow = "📈" if data["net_flow_yi"] > 0 else "📉"
            lines.append(f"| {i} | {arrow} {data['name']} | {data['net_flow_yi']:+.2f} | {data['change_pct']:+.2f}% | {data['amount_yi']:.2f} |")
        lines.append("")

        # 投资建议
        lines.append("## 四、投资建议")
        lines.append("")

        # 强信号建议
        strong_signals = [s for s in signals if s["confidence"] == "高"]
        if strong_signals:
            for s in strong_signals:
                if "加仓" in s["signal_type"]:
                    lines.append(f"📈 **{s['name']}** - {s['signal_type']}")
                    lines.append(f"   - 净流入: {s['net_flow_yi']:.2f}亿元")
                    lines.append("   - 建议关注相关板块机会")
                    lines.append("")
                elif "减仓" in s["signal_type"]:
                    lines.append(f"📉 **{s['name']}** - {s['signal_type']}")
                    lines.append(f"   - 净流出: {abs(s['net_flow_yi']):.2f}亿元")
                    lines.append("   - 建议谨慎")
                    lines.append("")
        else:
            lines.append("⚠️ 当前无强信号，建议继续观察")
            lines.append("")

        lines.append("---")
        lines.append("*本报告由实时ETF资金流向监控系统自动生成*")
        lines.append(f"*数据源: {'iFinD MCP' if any(d.get('source') == 'ifind_mcp' for d in flow_data.values()) else 'Wind MCP' if any(d.get('source') == 'wind_mcp' for d in flow_data.values()) else 'tushare' if any(d.get('source') == 'tushare' for d in flow_data.values()) else 'yfinance' if any(d.get('source') == 'yfinance' for d in flow_data.values()) else 'sina' if any(d.get('source') == 'sina' for d in flow_data.values()) else 'local_cache' if any(d.get('source') == 'local_cache' for d in flow_data.values()) else '模拟数据'}*")

        return "\n".join(lines)

    def generate_trading_plan(self, signals: List[Dict], flow_data: Dict) -> str:
        """根据ETF资金流向信号生成交易计划决策"""
        lines = []
        lines.append("## 五、交易计划决策")
        lines.append("")

        if not signals:
            lines.append("> 当前无显著资金信号，维持现有持仓不变。")
            return "\n".join(lines)

        strong_buy = [s for s in signals if '强加仓' in s['signal_type']]
        strong_sell = [s for s in signals if '强减仓' in s['signal_type']]
        med_buy = [s for s in signals if s['confidence'] == '中' and '加仓' in s['signal_type']]
        med_sell = [s for s in signals if s['confidence'] == '中' and '减仓' in s['signal_type']]

        # === 总体判断 ===
        total_flow = sum(d['net_flow_yi'] for d in flow_data.values())
        if total_flow > 100:
            market_stance = "强烈看多"
            action_tone = "积极进攻"
        elif total_flow > 30:
            market_stance = "偏多"
            action_tone = "谨慎加仓"
        elif total_flow < -100:
            market_stance = "强烈看空"
            action_tone = "大幅减仓"
        elif total_flow < -30:
            market_stance = "偏空"
            action_tone = "逐步减仓"
        else:
            market_stance = "中性震荡"
            action_tone = "高抛低吸"

        lines.append(f"**市场总判**: {market_stance} | **操作基调**: {action_tone}")
        lines.append(f"**累计净流入**: {total_flow:+.1f} 亿 | **信号总数**: {len(signals)}")
        lines.append("")

        # === 具体操作计划 ===
        lines.append("### 5.1 操作计划")
        lines.append("")

        # 强买入信号 -> 推荐加仓标的
        if strong_buy:
            lines.append(f"**强加仓信号 ({len(strong_buy)}条)** — 建议增持以下标的:")
            lines.append("")
            lines.append("| 优先 | ETF | 净流入 | 关联个股/ETF | 建议操作 | 仓位调整 |")
            lines.append("|------|-----|--------|-------------|---------|---------|")

            for i, s in enumerate(strong_buy, 1):
                code = s['code']
                stock_info = ETF_TO_STOCKS.get(code, {})
                sector = stock_info.get('板块', s['category'])
                stocks = stock_info.get('个股票池', [])
                related_etfs = ETF_TO_RELATED.get(code, [])

                action = "加仓"
                adjustment = "+3~5%"
                targets = ", ".join(stocks[:3]) if stocks else "-"
                if related_etfs:
                    targets += f" (替代ETF: {', '.join(related_etfs[:1])})"

                lines.append(f"| {i} | {s['name']}({code}) | +{s['net_flow_yi']:.1f}亿 | {targets} | {action} | {adjustment} |")
            lines.append("")

        # 强卖出信号 -> 推荐减仓
        if strong_sell:
            lines.append(f"**强减仓信号 ({len(strong_sell)}条)** — 建议减持以下标的:")
            lines.append("")
            for s in strong_sell:
                code = s['code']
                stock_info = ETF_TO_STOCKS.get(code, {})
                sector = stock_info.get('板块', s['category'])
                stocks = stock_info.get('个股票池', [])
                related_etfs = ETF_TO_RELATED.get(code, [])
                targets = ", ".join(stocks[:3]) if stocks else "-"
                lines.append(f"- [{s['name']}({code})] {s['net_flow_yi']:.1f}亿 -> 减持 {targets} | 仓位 -3~5%")
                if related_etfs:
                    lines.append(f"  > 替代方案: 转向 {', '.join(related_etfs)}")
            lines.append("")

        # 中等买入信号
        if med_buy:
            lines.append(f"**中等加仓信号 ({len(med_buy)}条)** — 可逢低建仓:")
            lines.append("")
            for s in med_buy:
                code = s['code']
                stock_info = ETF_TO_STOCKS.get(code, {})
                stocks = stock_info.get('个股票池', [])
                targets = ", ".join(stocks[:2]) if stocks else "-"
                lines.append(f"- [{s['name']}({code})] +{s['net_flow_yi']:.1f}亿 -> 关注 {targets} | 仓位 +1~2%")
            lines.append("")

        # 中等卖出信号
        if med_sell:
            lines.append(f"**中等减仓信号 ({len(med_sell)}条)** — 可适当止盈:")
            lines.append("")
            for s in med_sell:
                code = s['code']
                stock_info = ETF_TO_STOCKS.get(code, {})
                stocks = stock_info.get('个股票池', [])
                targets = ", ".join(stocks[:2]) if stocks else "-"
                lines.append(f"- [{s['name']}({code})] {s['net_flow_yi']:.1f}亿 -> 减仓 {targets} | 仓位 -1~2%")
            lines.append("")

        # === 板块轮动 ===
        lines.append("### 5.2 板块轮动建议")
        lines.append("")

        # 按板块汇总
        sector_flows = {}
        for s in signals:
            code = s['code']
            stock_info = ETF_TO_STOCKS.get(code, {})
            sector = stock_info.get('板块', s['category'])
            sector_flows[sector] = sector_flows.get(sector, 0) + s['net_flow_yi']

        ranked_sectors = sorted(sector_flows.items(), key=lambda x: -x[1])

        lines.append("| 板块 | 资金信号 | 操作建议 |")
        lines.append("|------|---------|---------|")
        for sector, flow in ranked_sectors:
            if flow > 50:
                advice = "超配"
            elif flow > 10:
                advice = "增配"
            elif flow < -50:
                advice = "低配"
            elif flow < -10:
                advice = "减配"
            else:
                advice = "标配"
            arrow = "📈" if flow > 0 else "📉" if flow < 0 else "➡️"
            lines.append(f"| {arrow} {sector} | {flow:+.1f}亿 | {advice} |")
        lines.append("")

        # === 仓位建议 ===
        lines.append("### 5.3 整体仓位建议")
        lines.append("")

        # 根据资金流向计算建议仓位
        if market_stance == "强烈看多":
            suggest_position = "85-95%"
            cash_reserve = "5-15%"
        elif market_stance == "偏多":
            suggest_position = "70-85%"
            cash_reserve = "15-30%"
        elif market_stance == "强烈看空":
            suggest_position = "30-50%"
            cash_reserve = "50-70%"
        elif market_stance == "偏空":
            suggest_position = "50-65%"
            cash_reserve = "35-50%"
        else:
            suggest_position = "60-75%"
            cash_reserve = "25-40%"

        lines.append("| 指标 | 建议 |")
        lines.append("|------|------|")
        lines.append(f"| 建议仓位 | **{suggest_position}** |")
        lines.append(f"| 现金储备 | **{cash_reserve}** |")
        lines.append(f"| 操作基调 | **{action_tone}** |")
        lines.append(f"| 强信号方向 | {'多头' if len(strong_buy) > len(strong_sell) else '空头' if len(strong_sell) > len(strong_buy) else '均衡'} |")
        lines.append("")

        # === 风控指令 ===
        lines.append("### 5.4 今日风控指令")
        lines.append("")

        if market_stance in ("强烈看空", "偏空"):
            lines.append("1. 单只止损线收紧至 **-10%**（正常 -15%）")
            lines.append("2. 板块ETF止损线收紧至 **-15%**（正常 -20%）")
            lines.append("3. 暂停新增开仓，仅维持核心底仓")
        elif market_stance == "中性震荡":
            lines.append("1. 单只止损线维持 **-15%**")
            lines.append("2. 涨幅超30%及时止盈半仓")
            lines.append("3. 关注尾盘是否有突破信号")
        else:
            lines.append("1. 单只止损线可放宽至 **-18%**（正常 -15%）")
            lines.append("2. 涨幅超40%再考虑止盈")
            lines.append("3. 可在回调时加仓强势板块")

        lines.append("")
        lines.append("---")
        lines.append(f"*交易计划由实时ETF资金流向监控自动生成 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")

        return "\n".join(lines)

    def collect_flow_data(self) -> Dict[str, Dict]:
        """统一采集当前配置中的 ETF 资金流向"""
        flow_data: Dict[str, Dict] = {}
        for etf in NATIONAL_TEAM_ETFS:
            data = self.get_etf_fund_flow(etf["code"])
            if data is None:
                continue
            flow_data[etf["code"]] = data
        return flow_data

    def run_intraday(self) -> Dict:
        """
        盘中决策模式（每15分钟执行）
        返回决策结果，供 llm_intraday_decision_engine.py 使用
        """
        logger.info("===== 盘中ETF资金流向决策 =====")

        flow_data = self.collect_flow_data()
        if not flow_data:
            logger.warning("未获取到任何ETF数据")
            return {"signals": [], "flow_data": {}, "market_stance": "未知"}

        # 检测信号
        signals = self.detect_signals(flow_data)

        # 计算市场总判
        total_flow = sum(d['net_flow_yi'] for d in flow_data.values())
        if total_flow > 100:
            market_stance = "强烈看多"
        elif total_flow > 30:
            market_stance = "偏多"
        elif total_flow < -100:
            market_stance = "强烈看空"
        elif total_flow < -30:
            market_stance = "偏空"
        else:
            market_stance = "中性震荡"

        # 生成决策摘要
        decision = {
            "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "market_stance": market_stance,
            "total_flow_yi": round(total_flow, 2),
            "signal_count": len(signals),
            "strong_buy_count": len([s for s in signals if '强加仓' in s['signal_type']]),
            "strong_sell_count": len([s for s in signals if '强减仓' in s['signal_type']]),
            "signals": signals[:10],  # 只保留前10条
            "flow_data": flow_data,
            "source_stats": self.get_source_stats(),
            "primary_source": self.primary_source(),
        }

        # 保存盘中决策
        self._save_intraday_decision(decision)

        logger.info(f"市场总判: {market_stance} | 累计净流入: {total_flow:+.1f}亿 | 信号数: {len(signals)}")

        return decision

    def run_eod(self) -> str:
        """
        盘后报告模式（收盘后执行）
        生成完整 Markdown 报告
        """
        logger.info("===== 盘后ETF资金流向报告 =====")

        flow_data = self.collect_flow_data()
        if not flow_data:
            logger.warning("未获取到任何ETF数据")
            return ""

        # 检测信号
        signals = self.detect_signals(flow_data)

        # 生成报告
        report = self.generate_report(signals, flow_data)
        trading_plan = self.generate_trading_plan(signals, flow_data)
        full_report = report + "\n\n" + trading_plan

        # 保存到盘后报告目录
        reports_dir = os.path.join(_BASE_DIR, 'reports')
        os.makedirs(reports_dir, exist_ok=True)
        report_path = os.path.join(reports_dir, f"etf_flow_report_{datetime.now().strftime('%Y%m%d')}.md")

        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(full_report)

        logger.info(f"盘后报告已保存: {report_path}")

        # 同时保存 JSON 格式（供 LLM 决策引擎使用）
        json_path = os.path.join(reports_dir, f"etf_flow_report_{datetime.now().strftime('%Y%m%d')}.json")
        total_flow = round(sum(d['net_flow_yi'] for d in flow_data.values()), 2)
        market_stance = (
            "强烈看多" if total_flow > 100 else
            "偏多" if total_flow > 30 else
            "强烈看空" if total_flow < -100 else
            "偏空" if total_flow < -30 else
            "中性震荡"
        )
        json_data = {
            "report_date": datetime.now().strftime('%Y-%m-%d'),
            "generated_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "market_stance": market_stance,
            "total_flow_yi": total_flow,
            "signal_count": len(signals),
            "signals": signals,
            "flow_data": flow_data,
            "source_stats": self.get_source_stats(),
            "primary_source": self.primary_source(),
        }
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(json_data, f, ensure_ascii=False, indent=2)

        logger.info(f"JSON数据已保存: {json_path}")
        logger.info(f"数据源统计: {self.get_source_stats()} | primary_source={self.primary_source()}")

        return full_report

    def _save_intraday_decision(self, decision: Dict):
        """保存盘中决策到文件"""
        decisions_dir = os.path.join(_BASE_DIR, 'v7.5_institutional', 'intraday_decisions')
        os.makedirs(decisions_dir, exist_ok=True)
        decision_path = os.path.join(decisions_dir, f"etf_flow_decision_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")

        with open(decision_path, 'w', encoding='utf-8') as f:
            json.dump(decision, f, ensure_ascii=False, indent=2)

        logger.info(f"盘中决策已保存: {decision_path}")

    def run(self, mode: str = "eod"):
        """
        统一运行入口

        Args:
            mode: "intraday" 盘中决策 | "eod" 盘后报告
        """
        if mode == "intraday":
            return self.run_intraday()
        else:
            return self.run_eod()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description='ETF资金流向监控')
    parser.add_argument('--mode', choices=['intraday', 'eod'], default='eod', help='运行模式')
    args = parser.parse_args()

    tracker = ETFRealTimeTracker()
    tracker.run(mode=args.mode)
