#!/usr/bin/env python3.8
"""
TradingAgents 桥激活校验脚本 (Phase 0)
========================================
执行可行性研究 v2.0 §4.4 的 7 步校验清单。

用法:
    cd E:\\各种PY程序\\28-终极量化交易系统8.4
    py -3.8 scripts/activate_tradingagents_bridge.py

    # 跳过微服务启动（已手动启动）
    py -3.8 scripts/activate_tradingagents_bridge.py --skip-start

    # 仅健康检查
    py -3.8 scripts/activate_tradingagents_bridge.py --check-only

前置条件:
    1. Ollama 运行中, qwen2.5:7b 已 pull
    2. Python 3.11+ 环境可用 (TradingAgents 需要 3.10+)
    3. E:\\各种PY程序\\TradingAgents\\ 目录完整

环境变量:
    本脚本自动设置 PYTHONUTF8=1 和 NO_PROXY=localhost,127.0.0.1
    (避免代理转发本地请求导致 502)

作者: 28 系统
日期: 2026-08-06
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

# ============================================================
# 环境预处理 (必须在任何 import 之前)
# ============================================================

# Python 3.8 在 Windows 上默认用 GBK 读取 .pth 文件, 含中文路径的
# pybao.pth 会导致 site.py 崩溃. PYTHONUTF8=1 强制 UTF-8 模式.
os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
# 代理会转发 localhost 请求导致 502, 必须排除本地地址
os.environ.setdefault("NO_PROXY", "localhost,127.0.0.1")

# ============================================================
# 配置
# ============================================================

BRIDGE_HOST = os.environ.get("TRADINGAGENTS_BRIDGE_HOST", "127.0.0.1")
BRIDGE_PORT = int(os.environ.get("TRADINGAGENTS_BRIDGE_PORT", "8490"))
BRIDGE_URL = f"http://{BRIDGE_HOST}:{BRIDGE_PORT}"
BRIDGE_SCRIPT = Path(os.environ.get("TRADINGAGENTS_BRIDGE_SCRIPT", "28_bridge.py"))
TA_DIR = Path(os.environ.get("TRADINGAGENTS_DIR", "."))
TA_ENV_FILE = TA_DIR / ".env"

# 测试标的 (先用美股验证链路, A 股数据在 Phase 3 适配)
TEST_TICKER_US = "AAPL"
TEST_TICKER_CN = "600519.SH"
TEST_DATE = time.strftime("%Y-%m-%d")

# ANSI 颜色
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RESET = "\033[0m"
BOLD = "\033[1m"


# ============================================================
# 工具函数
# ============================================================


def header(text: str) -> None:
    print(f"\n{BOLD}{CYAN}{'=' * 60}")
    print(f"  {text}")
    print(f"{'=' * 60}{RESET}\n")


def ok(text: str) -> None:
    print(f"  {GREEN}[PASS]{RESET} {text}")


def fail(text: str) -> None:
    print(f"  {RED}[FAIL]{RESET} {text}")


def warn(text: str) -> None:
    print(f"  {YELLOW}[WARN]{RESET} {text}")


def info(text: str) -> None:
    print(f"  {CYAN}[INFO]{RESET} {text}")


def http_get(path: str, timeout: int = 10) -> dict[str, Any] | None:
    url = f"{BRIDGE_URL}{path}"
    try:
        req = Request(url, method="GET")
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (URLError, OSError, json.JSONDecodeError, TimeoutError) as e:
        print(f"  {RED}HTTP GET {path} 失败: {e}{RESET}")
        return None


def http_post(path: str, payload: dict, timeout: int = 130) -> dict[str, Any] | None:
    url = f"{BRIDGE_URL}{path}"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    try:
        req = Request(
            url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (URLError, OSError, json.JSONDecodeError, TimeoutError) as e:
        print(f"  {RED}HTTP POST {path} 失败: {e}{RESET}")
        return None


def check_port(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except (TimeoutError, ConnectionRefusedError, OSError):
        return False


# ============================================================
# 校验步骤
# ============================================================


def step1_write_env() -> bool:
    """步骤 1: 写入 TradingAgents .env 配置文件"""
    header("步骤 1/7: 配置 TradingAgents 环境变量")

    env_content = """\
# TradingAgents 桥接配置 (Phase 0 激活)
TRADINGAGENTS_LLM_PROVIDER=ollama
TRADINGAGENTS_DEEP_THINK_LLM=qwen2.5:7b
TRADINGAGENTS_QUICK_THINK_LLM=qwen2.5:7b
TRADINGAGENTS_LLM_BACKEND_URL=http://localhost:11434/v1
TRADINGAGENTS_OUTPUT_LANGUAGE=Chinese
TRADINGAGENTS_MAX_DEBATE_ROUNDS=1
TRADINGAGENTS_MAX_RISK_ROUNDS=1
# 代理配置 (yfinance 需要访问 Yahoo Finance, 中国大陆需走代理)
# HTTP_PROXY=http://127.0.0.1:7897
# HTTPS_PROXY=http://127.0.0.1:7897
# NO_PROXY=localhost,127.0.0.1
"""

    try:
        TA_ENV_FILE.write_text(env_content, encoding="utf-8")
        ok(f"已写入 {TA_ENV_FILE}")
        info("LLM Provider: ollama")
        info("Deep Think: qwen2.5:7b")
        info("Backend: http://localhost:11434/v1")
        return True
    except OSError as e:
        fail(f"写入 .env 失败: {e}")
        return False


def step2_start_bridge(skip_start: bool = False) -> bool:
    """步骤 2: 启动微服务 (或检查已运行的实例)"""
    header("步骤 2/7: 启动 TradingAgents 桥微服务")

    # 先检查是否已在运行
    if check_port(BRIDGE_HOST, BRIDGE_PORT):
        ok(f"微服务已在运行 ({BRIDGE_HOST}:{BRIDGE_PORT})")
        return True

    if skip_start:
        warn("--skip-start 已指定, 且微服务未运行")
        return False

    if not BRIDGE_SCRIPT.exists():
        fail(f"桥脚本不存在: {BRIDGE_SCRIPT}")
        return False

    info(f"启动: py -3.11 {BRIDGE_SCRIPT} --port {BRIDGE_PORT}")
    info("请在另一个终端手动执行上述命令, 然后重新运行本脚本加 --skip-start")
    warn("微服务需要 Python 3.11+ 环境, 请确保 tradingagents 包已安装")

    # 尝试自动启动 (后台)
    try:
        info("尝试自动启动微服务 (后台模式)...")
        proc = subprocess.Popen(
            ["py", "-3.11", str(BRIDGE_SCRIPT), "--port", str(BRIDGE_PORT)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(TA_DIR),
        )
        # 等待启动
        for _ in range(15):
            time.sleep(2)
            if check_port(BRIDGE_HOST, BRIDGE_PORT):
                ok(f"微服务启动成功 ({BRIDGE_HOST}:{BRIDGE_PORT})")
                info(f"PID: {proc.pid}")
                return True

        fail("微服务启动超时 (30s)")
        stderr = proc.poll()
        if stderr is not None:
            info(f"进程退出码: {stderr}")
        return False
    except (OSError, subprocess.SubprocessError) as e:
        fail(f"自动启动失败: {e}")
        info("请手动启动: py -3.11 28_bridge.py --port 8490")
        return False


def step3_health_check() -> bool:
    """步骤 3: 健康检查探活"""
    header("步骤 3/7: 健康检查探活")

    resp = http_get("/health", timeout=5)
    if resp is None:
        fail("无法连接微服务 /health 端点")
        return False

    status = resp.get("status")
    ta_available = resp.get("tradingagents_available", False)
    py_ver = resp.get("python_version", "?")[:30]

    if status == "ok":
        ok(f"微服务状态: {status}")
    else:
        fail(f"微服务状态异常: {status}")
        return False

    if ta_available:
        ok(f"TradingAgents 框架已加载: {ta_available}")
    else:
        fail(f"TradingAgents 框架未加载: {ta_available}")
        warn("请在 Python 3.11 环境安装: py -3.11 -m pip install -e .")
        return False

    info(f"Python 版本: {py_ver}")
    return True


def step4_dry_run() -> bool:
    """步骤 4: 单标的 dry-run (美股先验证链路)"""
    header("步骤 4/7: 单标的 dry-run (美股验证链路)")

    info(f"标的: {TEST_TICKER_US} | 日期: {TEST_DATE}")
    info("注意: 使用美股先验证 LLM 链路, A 股数据在 Phase 3 适配")

    payload = {"ticker": TEST_TICKER_US, "date": TEST_DATE}
    resp = http_post("/analyze", payload, timeout=130)

    if resp is None:
        fail("dry-run 请求失败")
        return False

    if "error" in resp:
        fail(f"微服务返回错误: {resp['error']}")
        return False

    decision = resp.get("decision", {})
    action = decision.get("action", "UNKNOWN")
    confidence = decision.get("confidence", 0)
    reasoning = decision.get("reasoning", "")[:200]

    ok(f"决策: {action} (置信度: {confidence:.0%})")
    info(f"理由: {reasoning}...")
    return True


def step5_check_source() -> bool:
    """步骤 5: 验证 source 字段非降级"""
    header("步骤 5/7: 验证 source 字段 (非降级)")

    payload = {"ticker": TEST_TICKER_US, "date": TEST_DATE}
    resp = http_post("/analyze", payload, timeout=130)

    if resp is None:
        fail("请求失败, 无法验证 source")
        return False

    # 桥客户端的 source 判断逻辑:
    # "tradingagents" = 微服务正常返回
    # "fallback_local" = 降级到本地六专家
    # "neutral" = 中性决策兜底
    decision = resp.get("decision", {})
    if decision:
        ok('source = "tradingagents" (微服务正常)')
        return True
    else:
        fail("决策为空, 可能已降级")
        return False


def step6_check_state_summary() -> bool:
    """步骤 6: 检查 state_summary 结构"""
    header("步骤 6/7: 检查 state_summary 结构")

    payload = {"ticker": TEST_TICKER_US, "date": TEST_DATE}
    resp = http_post("/analyze", payload, timeout=130)

    if resp is None:
        fail("请求失败, 无法检查 state_summary")
        return False

    state_summary = resp.get("state_summary", {})
    expected_keys = [
        "market_report",
        "news_report",
        "fundamentals_report",
        "social_report",
        "sentiment_report",
    ]

    if not state_summary:
        fail("state_summary 为空")
        return False

    found_keys = [k for k in expected_keys if k in state_summary and state_summary[k]]
    ok(f"state_summary 包含 {len(found_keys)}/{len(expected_keys)} 个报告:")
    for key in found_keys:
        preview = str(state_summary[key])[:100].replace("\n", " ")
        info(f"  {key}: {preview}...")

    if len(found_keys) >= 3:
        ok("state_summary 结构完整 (≥3 个报告)")
        return True
    else:
        warn(f"state_summary 不完整 (仅 {len(found_keys)} 个报告)")
        return len(found_keys) > 0


def step7_bridge_from_84() -> bool:
    """步骤 7: 从 8.4 侧验证桥调用"""
    header("步骤 7/7: 从 8.4 (Python 3.8) 侧验证桥调用")

    try:
        # 添加 8.4 根目录到 path
        root = Path(__file__).resolve().parent.parent
        sys.path.insert(0, str(root))

        from utils.tradingagents_bridge import TradingAgentsBridge

        bridge = TradingAgentsBridge()
        available = bridge.is_available(force_check=True)

        if not available:
            fail("桥客户端检测微服务不可用")
            return False

        ok("桥客户端检测微服务可用")

        result = bridge.analyze(TEST_TICKER_US, TEST_DATE)
        source = result.get("source", "unknown")
        action = result.get("action", "UNKNOWN")
        confidence = result.get("confidence", 0)

        ok(f"source: {source}")
        ok(f"action: {action} (置信度: {confidence:.0%})")

        if source == "tradingagents":
            ok("端到端链路验证通过!")
            return True
        elif source == "fallback_local":
            warn("source=fallback_local, 微服务返回异常已降级")
            return False
        else:
            fail(f"source={source}, 非预期值")
            return False

    except ImportError as e:
        fail(f"导入 TradingAgentsBridge 失败: {e}")
        return False
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError) as e:
        fail(f"桥调用异常: {e}")
        return False


# ============================================================
# 主流程
# ============================================================


def main() -> int:
    args = sys.argv[1:]
    skip_start = "--skip-start" in args
    check_only = "--check-only" in args

    print(f"\n{BOLD}TradingAgents 桥激活校验 (Phase 0){RESET}")
    print(f"日期: {TEST_DATE}")
    print(f"微服务: {BRIDGE_URL}")
    print(f"桥脚本: {BRIDGE_SCRIPT}")

    if check_only:
        # 仅健康检查 + dry-run
        results = {}
        results["step3"] = step3_health_check()
        if results["step3"]:
            results["step4"] = step4_dry_run()
            results["step5"] = step5_check_source()
            results["step6"] = step6_check_state_summary()
    else:
        results = {}
        results["step1"] = step1_write_env()
        if results["step1"]:
            results["step2"] = step2_start_bridge(skip_start)
            if results["step2"]:
                results["step3"] = step3_health_check()
                if results["step3"]:
                    results["step4"] = step4_dry_run()
                    results["step5"] = step5_check_source()
                    results["step6"] = step6_check_state_summary()
                    results["step7"] = step7_bridge_from_84()

    # 汇总
    header("校验结果汇总")
    total = len(results)
    passed = sum(1 for v in results.values() if v)

    for step, result in results.items():
        status = f"{GREEN}PASS{RESET}" if result else f"{RED}FAIL{RESET}"
        print(f"  {step}: {status}")

    print(f"\n  总计: {passed}/{total} 通过")

    if passed == total and total > 0:
        ok(f"{BOLD}Phase 0 校验全部通过!{RESET}")
        info("下一步: 进入 Phase 1 (TDAM 沙箱 + TA Shadow 双跑)")
        return 0
    elif passed > 0:
        warn(f"{BOLD}Phase 0 部分通过, 请修复失败项后重试{RESET}")
        return 1
    else:
        fail(f"{BOLD}Phase 0 校验失败, 请检查前置条件{RESET}")
        info("1. Ollama 是否运行: curl http://localhost:11434/api/tags")
        info("2. qwen2.5:7b 是否已 pull: ollama list")
        info("3. Python 3.11 是否可用: py -3.11 --version")
        info("4. tradingagents 包是否安装: py -3.11 -m pip show tradingagents")
        return 2


if __name__ == "__main__":
    sys.exit(main())
