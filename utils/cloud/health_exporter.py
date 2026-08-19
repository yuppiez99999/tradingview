"""
health_exporter — Prometheus 指标导出 (云端可观测性)

暴露 /metrics, 含:
- quant_broker_connected (gauge, 0/1)
- quant_positions_count (gauge)
- quant_account_available (gauge, 元)
- quant_last_job_success_timestamp (gauge, unix ts)
- quant_rpc_latency_seconds (histogram, 云→Win 网关延迟)

部署: 作为 sidecar 或独立 Deployment, Prometheus scrape。
用法: python utils/cloud/health_exporter.py --port 9090
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

try:
    from prometheus_client import Gauge, Histogram, start_http_server
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False
    logger.warning("prometheus_client 未安装, health_exporter 不可用")


# ============================================================
# 指标定义
# ============================================================

if PROMETHEUS_AVAILABLE:
    m_broker_connected = Gauge("quant_broker_connected", "Broker 连接状态 (1=连接, 0=断开)")
    m_positions_count = Gauge("quant_positions_count", "当前持仓标的数")
    m_account_available = Gauge("quant_account_available", "可用资金 (元)")
    m_account_total = Gauge("quant_account_total", "总资产 (元)")
    m_rpc_latency = Histogram("quant_rpc_latency_seconds", "云→Win 网关 RPC 延迟", buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5))
    m_last_job_success = Gauge("quant_last_job_success_timestamp", "最近一次任务成功时间 (unix ts)")


def collect_once() -> None:
    """采集一次指标 (调 broker_factory + RemoteQmtBroker)."""
    if not PROMETHEUS_AVAILABLE:
        return
    try:
        from utils.execution.broker_factory import get_broker
        broker = get_broker()
        connected = 1.0 if getattr(broker, "is_connected", False) else 0.0
        m_broker_connected.set(connected)

        if connected:
            t0 = time.time()
            try:
                positions = broker.get_positions()
                m_positions_count.set(len(positions) if positions else 0)
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError):
                m_positions_count.set(0)
            m_rpc_latency.observe(time.time() - t0)

            try:
                account = broker.get_account_info()
                m_account_available.set(float(account.get("available", 0)))
                m_account_total.set(float(account.get("total", 0)))
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError):
                pass
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, ImportError) as exc:
        logger.debug("指标采集失败 (fail-open): %s", exc)


def main() -> None:
    if not PROMETHEUS_AVAILABLE:
        sys.exit(1)
    parser = argparse.ArgumentParser(description="Quant Prometheus health exporter")
    parser.add_argument("--port", type=int, default=9090)
    parser.add_argument("--interval", type=int, default=30, help="采集间隔秒")
    args = parser.parse_args()

    start_http_server(args.port)
    logger.info("health_exporter 监听 :%d/metrics (间隔 %ds)", args.port, args.interval)

    import threading
    def loop() -> None:
        while True:
            collect_once()
            time.sleep(args.interval)
    threading.Thread(target=loop, daemon=True).start()

    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
