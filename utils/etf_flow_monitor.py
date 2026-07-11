# -*- coding: utf-8 -*-
"""
实时ETF资金流向监控模块

数据源优先级: Wind MCP fund_data > iFinD MCP > 新浪财经 > 本地缓存

功能:
- 获取ETF资金流向数据
- 检测国家队加仓信号
- 更新 positions.json 中的 etf_flow_signal 和 etf_inflow 字段
- 集成到每日交易执行流程
"""

import os
import json
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from utils.logger import get_logger

logger = get_logger('etf_flow_monitor')

SIGNAL_THRESHOLDS = {
    "high": 50,
    "medium": 10,
    "low": 2,
}

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

ETF_TO_STOCKS = {
    "510050": {"板块": "上证50大盘蓝筹", "个股票池": ["600036", "601318", "600519", "600276", "601166"]},
    "510300": {"板块": "沪深300核心资产", "个股票池": ["600036", "600276", "601088", "002648", "600346"]},
    "510500": {"板块": "中盘成长", "个股票池": ["002493", "000301", "601233", "603225", "000059"]},
    "588000": {"板块": "科创板科技", "个股票池": ["688041", "300308", "688017", "002371"]},
    "588080": {"板块": "科创板科技", "个股票池": ["688041", "300308", "688017", "002371"]},
    "512760": {"板块": "半导体", "个股票池": ["688041", "002371", "300308"]},
    "512880": {"板块": "券商", "个股票池": ["600030", "601211", "600837"]},
    "512800": {"板块": "银行", "个股票池": ["600036", "601166", "000001"]},
    "518880": {"板块": "黄金避险", "个股票池": ["600489", "601899", "600547"]},
    "512170": {"板块": "医疗医药", "个股票池": ["600276", "300760", "603259"]},
    "515030": {"板块": "新能源", "个股票池": ["300274", "600875", "600089"]},
    "159915": {"板块": "创业板成长", "个股票池": ["300274", "300308", "300760"]},
    "512100": {"板块": "小盘风格", "个股票池": ["688017", "603225", "000425"]},
}


class ETFRealTimeTracker:
    """实时ETF资金流向追踪器"""

    def __init__(self):
        self.wind_mcp_available = False
        self.ifind_mcp_available = False
        self._wind_mcp_client = None
        self._ifind_client = None
        self._init_data_sources()

    def _init_data_sources(self):
        try:
            wind_path = os.path.join(os.path.dirname(__file__), '..', 'wind_mcp_fetcher.py')
            wind_path = os.path.normpath(wind_path)
            if os.path.isfile(wind_path):
                import importlib.util
                spec = importlib.util.spec_from_file_location('wind_mcp_fetcher', wind_path)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                self._wind_mcp_client = {
                    'quote': mod.wind_get_quote,
                    'batch_quotes': mod.wind_get_batch_quotes,
                }
                self.wind_mcp_available = True
                logger.info("Wind MCP 客户端已加载 (ETF资金流数据源 P0)")
        except Exception as e:
            logger.warning(f"Wind MCP 客户端加载失败: {e}")

        try:
            from utils.ifind_client import IFindClient
            auth_token = os.environ.get('IFIND_TOKEN', '')
            if auth_token:
                self._ifind_client = IFindClient(auth_token=auth_token)
                self.ifind_mcp_available = True
                logger.info("iFinD MCP 客户端已加载 (ETF资金流数据源 P1)")
        except Exception as e:
            logger.warning(f"iFinD MCP 客户端加载失败: {e}")

    def _to_wind_code(self, etf_code: str) -> str:
        s = str(etf_code).strip()
        if s.startswith(("51", "58")):
            return f"{s}.SH"
        if s.startswith(("15", "16")):
            return f"{s}.SZ"
        return f"{s}.SH"

    def _fetch_wind_fund_flow(self, etf_code: str) -> Optional[Dict]:
        if not self._wind_mcp_client or not self.wind_mcp_available:
            return None
        try:
            windcode = self._to_wind_code(etf_code)
            quote = self._wind_mcp_client['quote'](windcode, is_fund=True)
            if not quote or quote.get('price', 0) <= 0:
                return None

            amount = quote.get('amount')
            volume = quote.get('volume')
            change = quote.get('change', 0)

            result = {
                "code": etf_code,
                "name": "",
                "net_flow_yi": round(amount * 1e-8, 2) if amount else 0.0,
                "change_pct": change,
                "volume": volume if volume else 0,
                "amount_yi": round(amount * 1e-8, 2) if amount else 0.0,
                "trend": "流入" if (amount or 0) > 0 else "流出" if (amount or 0) < 0 else "中性",
                "source": "wind_mcp",
            }

            for etf in NATIONAL_TEAM_ETFS:
                if etf["code"] == etf_code:
                    result["name"] = etf["name"]
                    result["category"] = etf["category"]
                    break

            return result
        except Exception as e:
            logger.error(f"Wind MCP 获取ETF资金流失败 ({etf_code}): {e}")
            return None

    def _fetch_ifind_fund_flow(self, etf_code: str) -> Optional[Dict]:
        if not self._ifind_client or not self.ifind_mcp_available:
            return None
        try:
            quotes = self._ifind_client.get_etf_quotes([etf_code])
            if etf_code not in quotes:
                return None

            quote = quotes[etf_code]
            price = quote.get('price', 0)
            change_pct = quote.get('change_pct', 0)
            volume = quote.get('volume', 0)

            result = {
                "code": etf_code,
                "name": "",
                "net_flow_yi": 0.0,
                "change_pct": change_pct * 100 if change_pct else 0,
                "volume": volume,
                "amount_yi": 0.0,
                "trend": "中性",
                "source": "ifind_mcp",
            }

            for etf in NATIONAL_TEAM_ETFS:
                if etf["code"] == etf_code:
                    result["name"] = etf["name"]
                    result["category"] = etf["category"]
                    break

            return result
        except Exception as e:
            logger.error(f"iFinD MCP 获取ETF资金流失败 ({etf_code}): {e}")
            return None

    def _fetch_sina_fund_flow(self, etf_code: str) -> Optional[Dict]:
        try:
            import requests as _requests
            session = _requests.Session()
            session.trust_env = False
            session.proxies = {"http": None, "https": None}

            s = str(etf_code).strip()
            if s.startswith(("51", "58")):
                sina_code = f"sh{s}"
            elif s.startswith(("15", "16")):
                sina_code = f"sz{s}"
            else:
                return None

            url = f"https://hq.sinajs.cn/list={sina_code}"
            resp = session.get(url, timeout=15)
            if resp.status_code != 200:
                return None

            text = resp.text.strip()
            prefix = f'hq_str_{sina_code}="'
            idx = text.find(prefix)
            if idx < 0:
                return None

            start = idx + len(prefix)
            end = text.find('"', start)
            if end < 0:
                return None

            content = text[start:end]
            fields = content.split(',')
            if len(fields) < 10:
                return None

            try:
                name = fields[0]
                open_price = float(fields[1])
                prev_close = float(fields[2])
                current = float(fields[3])
                high = float(fields[4])
                low = float(fields[5])
                volume = float(fields[8]) if fields[8] else 0
                amount = float(fields[9]) if fields[9] else 0
            except (ValueError, IndexError):
                return None

            if current <= 0 or prev_close <= 0:
                return None

            change_pct = (current - prev_close) / prev_close * 100

            result = {
                "code": etf_code,
                "name": name,
                "net_flow_yi": round(amount * 1e-8, 2),
                "change_pct": round(change_pct, 2),
                "volume": int(volume),
                "amount_yi": round(amount * 1e-8, 2),
                "trend": "流入" if amount > 0 else "流出" if amount < 0 else "中性",
                "source": "sina_http",
            }

            for etf in NATIONAL_TEAM_ETFS:
                if etf["code"] == etf_code:
                    result["category"] = etf["category"]
                    break

            return result
        except Exception as e:
            logger.error(f"新浪财经获取ETF资金流失败 ({etf_code}): {e}")
            return None

    def get_etf_fund_flow(self, etf_code: str) -> Optional[Dict]:
        flow_data = self._fetch_wind_fund_flow(etf_code)
        if flow_data:
            return flow_data

        flow_data = self._fetch_ifind_fund_flow(etf_code)
        if flow_data:
            return flow_data

        flow_data = self._fetch_sina_fund_flow(etf_code)
        if flow_data:
            return flow_data

        return None

    def get_all_etf_fund_flows(self) -> Dict[str, Dict]:
        flow_data = {}
        logger.info(f"正在获取 {len(NATIONAL_TEAM_ETFS)} 只ETF资金流向数据...")

        for etf in NATIONAL_TEAM_ETFS:
            logger.info(f"  获取 {etf['code']} {etf['name']}...")
            data = self.get_etf_fund_flow(etf["code"])
            if data:
                flow_data[etf["code"]] = data
                logger.info(f"  净流入: {data['net_flow_yi']:+.2f}亿")
            else:
                flow_data[etf["code"]] = {
                    "code": etf["code"],
                    "name": etf["name"],
                    "category": etf["category"],
                    "net_flow_yi": 0.0,
                    "change_pct": 0.0,
                    "volume": 0,
                    "amount_yi": 0.0,
                    "trend": "中性",
                    "source": "none",
                }
                logger.warning(f"  获取失败: {etf['code']} {etf['name']}")

        return flow_data

    def detect_signals(self, flow_data: Dict) -> List[Dict]:
        signals = []

        for code, data in flow_data.items():
            net_flow = data.get("net_flow_yi", 0)

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

        signals.sort(key=lambda x: (
            0 if x["confidence"] == "高" else 1 if x["confidence"] == "中" else 2,
            -abs(x["net_flow_yi"])
        ))

        return signals

    def get_signal_summary(self, flow_data: Dict) -> Dict:
        signals = self.detect_signals(flow_data)
        total_flow = sum(d.get("net_flow_yi", 0) for d in flow_data.values())
        overall_trend = "净流入" if total_flow > 0 else "净流出" if total_flow < 0 else "平衡"

        return {
            "total_flow_yi": round(total_flow, 2),
            "overall_trend": overall_trend,
            "signal_count": len(signals),
            "strong_signals": len([s for s in signals if s["confidence"] == "高"]),
            "medium_signals": len([s for s in signals if s["confidence"] == "中"]),
            "low_signals": len([s for s in signals if s["confidence"] == "低"]),
            "signals": signals,
            "flow_data": flow_data,
        }

    def update_positions_json(self, positions_file: str) -> Dict:
        try:
            with open(positions_file, 'r', encoding='utf-8') as f:
                positions_data = json.load(f)
        except Exception as e:
            logger.error(f"加载 positions.json 失败: {e}")
            return {"status": "error", "message": str(e)}

        flow_data = self.get_all_etf_fund_flows()
        signals = self.detect_signals(flow_data)

        signal_map = {}
        for s in signals:
            signal_map[s["code"]] = s

        updated_count = 0
        for key, pos in positions_data.get('positions', {}).items():
            code = pos.get('code', '')
            if not code:
                continue

            code_num = code.split('.')[0]

            if code_num in flow_data:
                flow = flow_data[code_num]
                pos['etf_inflow'] = flow.get('net_flow_yi', 0)

                if code_num in signal_map:
                    sig = signal_map[code_num]
                    if sig['confidence'] == '高':
                        pos['etf_flow_signal'] = '强加仓' if '加仓' in sig['signal_type'] else '强减仓'
                    elif sig['confidence'] == '中':
                        pos['etf_flow_signal'] = '加仓' if '加仓' in sig['signal_type'] else '减仓'
                    elif sig['confidence'] == '低':
                        pos['etf_flow_signal'] = '关注'
                    updated_count += 1
                else:
                    pos['etf_flow_signal'] = '中性'
                    updated_count += 1
            elif pos.get('etf_flow_signal') and pos.get('etf_inflow') is not None:
                related_found = False
                for etf_code, stock_info in ETF_TO_STOCKS.items():
                    stocks = stock_info.get('个股票池', [])
                    if code_num in stocks:
                        if etf_code in flow_data:
                            flow = flow_data[etf_code]
                            pos['etf_inflow'] = flow.get('net_flow_yi', 0)
                            if etf_code in signal_map:
                                sig = signal_map[etf_code]
                                pos['etf_flow_signal'] = f"关联{sig['name']}"
                            else:
                                pos['etf_flow_signal'] = f"关联{etf_code}"
                            updated_count += 1
                            related_found = True
                            break
                if not related_found:
                    pos['etf_flow_signal'] = ''
                    pos['etf_inflow'] = 0

        positions_data['meta']['last_etf_update'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        try:
            with open(positions_file, 'w', encoding='utf-8') as f:
                json.dump(positions_data, f, ensure_ascii=False, indent=2)
            logger.info(f"已更新 positions.json: {updated_count} 个标的的ETF资金流信号")
            return {
                "status": "success",
                "updated_count": updated_count,
                "total_flow_yi": sum(d.get("net_flow_yi", 0) for d in flow_data.values()),
                "signal_count": len(signals),
            }
        except Exception as e:
            logger.error(f"保存 positions.json 失败: {e}")
            return {"status": "error", "message": str(e)}


def refresh_etf_flow_signals(positions_file: str = None) -> Dict:
    if positions_file is None:
        positions_file = os.path.join(os.path.dirname(__file__), '..', 'config', 'positions.json')
        positions_file = os.path.normpath(positions_file)

    tracker = ETFRealTimeTracker()
    result = tracker.update_positions_json(positions_file)

    if result.get('status') == 'success':
        summary = tracker.get_signal_summary(tracker.get_all_etf_fund_flows())
        result['summary'] = summary

    return result


def get_etf_flow_summary() -> Dict:
    tracker = ETFRealTimeTracker()
    flow_data = tracker.get_all_etf_fund_flows()
    return tracker.get_signal_summary(flow_data)


if __name__ == "__main__":
    logger.info("===== 实时ETF资金流向监控 =====")

    result = refresh_etf_flow_signals()
    logger.info(f"更新结果: {result}")

    if result.get('summary'):
        summary = result['summary']
        logger.info(f"整体态势: {summary['overall_trend']}")
        logger.info(f"今日净流入: {summary['total_flow_yi']:+.2f} 亿元")
        logger.info(f"强信号数量: {summary['strong_signals']} 条")

        if summary['signals']:
            logger.info("检测到的信号:")
            for s in summary['signals'][:5]:
                arrow = '+' if '加仓' in s['signal_type'] else '-'
                logger.info(f"  {arrow} {s['name']}: {s['signal_type']} (净流入{s['net_flow_yi']:+.2f}亿)")