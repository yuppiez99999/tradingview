"""
Gamma/Vega 引擎 - 尾部危机防御 (Tail Risk Insurance)
====================================================

对冲基金视角核心模块:
    - 监控大盘60日均线 + IV历史分位
    - 触发条件满足时自动加仓 Deep OTM Put
    - 黑天鹅时 Gamma 爆炸, 对冲现货浮亏, 锁死15%回撤红线

触发条件 (满足任一即执行):
    1. 大盘跌破60日均线 → 加仓 Deep OTM Put (资产1%)
    2. 市场 IV < 10% 历史分位 → 批量买入 Deep OTM Put (资产1-2%)

用法:
    from utils.gamma_engine import GammaEngine
    engine = GammaEngine()
    status = engine.monitor()
    if status["triggered"]:
        engine.execute_tail_hedge(status["trigger_type"], status["budget"])
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

import yaml

logger = logging.getLogger("gamma_engine")

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "configs" / "portfolio.yaml"
TRIGGER_LOG = BASE_DIR / "logs" / "gamma_triggers.jsonl"


class GammaEngine:
    """Gamma/Vega 引擎 - 尾部危机防御"""

    def __init__(self, config_path: Path | None = None):
        self.config_path = config_path or CONFIG_PATH
        self.config = self._load_config()
        TRIGGER_LOG.parent.mkdir(parents=True, exist_ok=True)

    def _load_config(self) -> dict:
        """加载 Gamma/Vega 引擎配置 (P1-Q8: 通过 ConfigManager 统一加载)

        优先级:
            1. 显式传入的 config_path (向后兼容测试场景)
            2. ConfigManager 自动解析 (v8.3 唯一事实源 > configs/ 历史回退)

        Returns:
            hedge.gamma_vega_engine 配置字典, 加载失败返回空 dict (fail-safe)
        """
        # 路径 1: 调用方显式指定了 config_path (测试场景, 向后兼容)
        if self.config_path != CONFIG_PATH:
            try:
                with open(self.config_path, encoding="utf-8") as f:
                    cfg = yaml.safe_load(f)
                return cfg.get("hedge", {}).get("gamma_vega_engine", {}) if isinstance(cfg, dict) else {}
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # P2 模块 fail-safe, 待后续精确化
                logger.error(f"加载配置失败 (显式路径 {self.config_path}): {e}")
                return {}

        # 路径 2: 通过 ConfigManager 统一加载 (P1-Q8, 生产路径)
        try:
            from utils.config_manager import get_config

            portfolio_cfg = get_config("portfolio")
            cfg = portfolio_cfg.get("hedge", {}).get("gamma_vega_engine", {})
            if cfg:
                return cfg  # type: ignore
                # ConfigManager 全部失败, 回退到旧路径 (保底)
            with open(self.config_path, encoding="utf-8") as f:
                fallback_cfg = yaml.safe_load(f)
            return fallback_cfg.get("hedge", {}).get("gamma_vega_engine", {}) if isinstance(fallback_cfg, dict) else {}
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # P2 模块 fail-safe, 待后续精确化
            logger.error(f"ConfigManager 加载失败, 回退到旧路径: {e}", exc_info=True)
            try:
                with open(self.config_path, encoding="utf-8") as f:
                    cfg = yaml.safe_load(f)
                return cfg.get("hedge", {}).get("gamma_vega_engine", {}) if isinstance(cfg, dict) else {}
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e2: # P2 模块 fail-safe, 待后续精确化
                logger.error(f"全部加载路径失败: {e2}")
                return {}

    def _get_market_ma60(self) -> float | None:
        """获取大盘60日均线 (沪深300)

        优先级: Wind MCP > 新浪 HTTP
        """
        # 尝试 Wind MCP
        try:
            from wind_mcp_fetcher import wind_get_index_data

            df = wind_get_index_data("000300.SH", days=70)
            if df is not None and len(df) >= 60:
                return float(df["close"].tail(60).mean())
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError): # P2 模块 fail-safe, 待后续精确化
            pass

        # 回退: 新浪 HTTP
        try:
            import requests

            url = "https://hq.sinajs.cn/list=sh000300"
            headers = {"Referer": "https://finance.sina.com.cn"}
            r = requests.get(url, headers=headers, timeout=5)
            if r.status_code == 200:
                parts = r.text.split(",")
                if len(parts) > 3:
                    return float(parts[3])
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError): # P2 模块 fail-safe, 待后续精确化
            pass

        return None

    def _get_market_iv_percentile(self) -> float | None:
        """获取市场IV历史分位 (近1年)

        优先级: Wind MCP option_data > 估算
        """
        try:
            from wind_mcp_fetcher import wind_get_option_iv

            iv_data = wind_get_option_iv("510050.SH")
            if iv_data and "iv_percentile" in iv_data:
                return float(iv_data["iv_percentile"])
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError): # P2 模块 fail-safe, 待后续精确化
            pass

        # 回退: 用 VIX 代理 (中国波指 iVIX 已停用, 用 510050 Put/Call 估算)
        logger.warning("无法获取真实 IV 分位, 返回 None")
        return None

    def monitor(self) -> dict:
        """监控触发条件

        Returns:
            {
                "timestamp": "2026-07-12T15:30:00",
                "market_price": 4250.5,
                "ma60": 4200.0,
                "ma60_broken": false,
                "iv_percentile": 0.15,
                "iv_low": false,
                "triggered": false,
                "trigger_type": null,
                "budget": 0,
                "action": null
            }
        """
        # 触发条件1: 大盘跌破60日均线
        # 注: 移除死代码 (self.config.get 结果丢弃 + _get_market_ma60() 结果丢弃后 L162 重新计算)
        ma60_value = None
        ma60_broken = False

        try:
            from wind_mcp_fetcher import wind_get_index_data

            df = wind_get_index_data("000300.SH", days=70)
            if df is not None and len(df) >= 60:
                ma60_value = float(df["close"].tail(60).mean())
                current_price = float(df["close"].iloc[-1])
                ma60_broken = current_price < ma60_value
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError): # P2 模块 fail-safe, 待后续精确化
            pass

        # 触发条件2: IV < 10% 历史分位
        iv_percentile = self._get_market_iv_percentile()
        iv_low = iv_percentile is not None and iv_percentile < 0.10

        # 判断触发
        triggered = False
        trigger_type = None
        budget = 0
        action = None

        if ma60_broken:
            triggered = True
            trigger_type = "ma60_breakdown"
            budget = int(5000000 * 0.01)  # 总资产1%
            action = "自动加仓Deep OTM Put (行权价低于现价10%)"

        if iv_low:
            triggered = True
            trigger_type = "iv_low_percentile"
            budget = int(5000000 * 0.02)  # 总资产2%
            action = "批量买入Deep OTM Put (权利金1-2%资产)"

        # 若两个条件同时触发, 取较大预算
        if ma60_broken and iv_low:
            budget = max(int(5000000 * 0.01), int(5000000 * 0.02))

        result = {
            "timestamp": datetime.now().isoformat(),
            "ma60_value": ma60_value,
            "ma60_broken": ma60_broken,
            "iv_percentile": iv_percentile,
            "iv_low": iv_low,
            "triggered": triggered,
            "trigger_type": trigger_type,
            "budget": budget,
            "action": action,
        }

        # 记录触发日志
        if triggered:
            self._log_trigger(result)

        return result

    def _log_trigger(self, trigger_info: dict) -> None:
        """记录触发日志"""
        try:
            with open(TRIGGER_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(trigger_info, ensure_ascii=False) + "\n")
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # P2 模块 fail-safe, 待后续精确化
            logger.error(f"写入触发日志失败: {e}")

    def execute_tail_hedge(self, trigger_type: str, budget: int) -> dict:
        """执行尾部对冲 (买入 Deep OTM Put)

        Args:
            trigger_type: 触发类型 (ma60_breakdown / iv_low_percentile)
            budget: 权利金预算 (RMB)

        Returns:
            {
                "executed": true,
                "trigger_type": "iv_low_percentile",
                "budget": 50000,
                "orders": [...]
            }
        """
        if budget <= 0:
            return {"executed": False, "reason": "budget_invalid"}

        # 生成买入计划
        put_options = self.config.get("put_options", [])
        orders = []
        used_budget = 0

        for opt in put_options:
            if used_budget >= budget:
                break

            # 预算分配 (按比例)
            total_budget_cfg = sum(p.get("premium_budget", 0) for p in put_options)
            if total_budget_cfg <= 0:
                break

            allocation = (opt.get("premium_budget", 0) / total_budget_cfg) * budget
            allocation = min(allocation, budget - used_budget)

            orders.append(
                {
                    "instrument": opt["instrument"],
                    "direction": "BUY",
                    "strike": "OTM_10%",  # Deep OTM (比常态OTM_5%更深)
                    "budget": int(allocation),
                    "contracts_est": int(allocation / 0.05),  # 简化估算
                }
            )
            used_budget += allocation

        result = {
            "executed": True,
            "timestamp": datetime.now().isoformat(),
            "trigger_type": trigger_type,
            "budget": budget,
            "orders": orders,
            "note": "实际执行需对接 QMT/券商 API",
        }

        logger.info(f"尾部对冲执行: 触发={trigger_type}, 预算={budget}, 订单数={len(orders)}")

        return result

    def get_trigger_history(self, days: int = 30) -> list[dict]:
        """获取最近 N 天的触发历史

        Args:
            days: 回看天数

        Returns:
            触发记录列表
        """
        if not TRIGGER_LOG.exists():
            return []

        records = []
        cutoff = datetime.now().timestamp() - days * 86400

        try:
            with open(TRIGGER_LOG, encoding="utf-8") as f:
                for line in f:
                    try:
                        record = json.loads(line.strip())
                        ts = record.get("timestamp", "")
                        if ts:
                            dt = datetime.fromisoformat(ts)
                            if dt.timestamp() >= cutoff:
                                records.append(record)
                    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError): # P2 模块 fail-safe, 待后续精确化
                        continue
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError): # P2 模块 fail-safe, 待后续精确化
            pass

        return records


# ============================================================
# CLI 入口
# ============================================================
if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(description="Gamma 引擎 - 尾部危机防御")
    parser.add_argument("--monitor", action="store_true", help="监控触发条件")
    parser.add_argument("--execute", type=str, help="执行尾部对冲 (trigger_type)")
    parser.add_argument("--budget", type=int, default=50000, help="权利金预算")
    parser.add_argument("--history", type=int, default=30, help="查看历史(天)")
    args = parser.parse_args()

    engine = GammaEngine()

    if args.monitor or (not args.execute and not args.history):
        status = engine.monitor()
        logger.info(json.dumps(status, ensure_ascii=False, indent=2))
        if status["triggered"]:
            logger.info(f"\n⚠️ 触发: {status['trigger_type']}")
            logger.info(f"预算: {status['budget']}")
            logger.info(f"动作: {status['action']}")

    if args.execute:
        result = engine.execute_tail_hedge(args.execute, args.budget)
        logger.info(json.dumps(result, ensure_ascii=False, indent=2))

    if args.history:
        history = engine.get_trigger_history(args.history)
        logger.info(f"\n最近 {args.history} 天触发历史: {len(history)} 次")
        for r in history:
            logger.info(f"  {r.get('timestamp', 'N/A')} - {r.get('trigger_type', 'N/A')} (预算 {r.get('budget', 0)})")
