"""
统一 LLM 客户端 — Thin Wrapper (B3.4.4)
========================================
本文件已迁移为 utils.alpha.llm_router.LLMRouter 的 thin wrapper。

历史: 原实现包含 DeepSeek + 豆包 Speed + Ollama 三级降级链 (requests 直调),
与 15_每日工作流/llm_client.py 和 generate_daily_report.py 重复。
B3.4.4 统一调用层后, 所有 LLM 调用走 LLMRouter:
  - USE_LLM_REPORT_ANALYZER=True: 走新路由 (多 provider fallback + 审计日志)
  - USE_LLM_REPORT_ANALYZER=False: 透传到旧 llm_client.chat (7 级降级链)

用法 (向后兼容):
    from llm_client import chat, generate_analysis, test_connection

    # 简单对话
    reply = chat("你好")

    # 带系统提示词的分析
    result = generate_analysis(
        system_prompt="你是金融分析师...",
        user_prompt="请分析以下数据...",
        report_type="coal"
    )

    # 测试连接
    status = test_connection()
"""

import os
import sys
from pathlib import Path
from typing import Any, Dict


# ── 自动加载 .env ────────────────────────────────────────
def _load_dotenv():
    """从项目根目录加载 .env 文件"""
    try:
        from dotenv import load_dotenv

        root = Path(__file__).resolve().parent.parent.parent.parent
        env_path = root / ".env"
        if env_path.exists():
            load_dotenv(env_path)
    except ImportError:
        pass


_load_dotenv()

# 修复 Windows GBK 编码
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# ── 导入统一 LLMRouter ────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# 添加 15_每日工作流 到 sys.path, 支持 flag=False 时透传到旧 llm_client
_LLM_WORKFLOW_DIR = _PROJECT_ROOT / "15_每日工作流"
if _LLM_WORKFLOW_DIR.exists() and str(_LLM_WORKFLOW_DIR) not in sys.path:
    sys.path.insert(0, str(_LLM_WORKFLOW_DIR))

_LLM_ROUTER_AVAILABLE = False
_router_chat = None
_router_chat_deep = None
_router_test_connection = None

try:
    from utils.alpha.llm_router import (
        chat as _router_chat,
    )
    from utils.alpha.llm_router import (
        test_connection as _router_test_connection,
    )
    _LLM_ROUTER_AVAILABLE = True
except Exception as _e:  # P2 模块 fail-safe
    print(f"  ⚠️ LLMRouter 导入失败, LLM 调用将不可用: {_e}")


# ── 向后兼容的配置常量 (deprecated, 实际调用走 LLMRouter) ──
DOUBAO_API_KEY = os.getenv("VOLCENGINE_API_KEY", "")
DOUBAO_BASE_URL = os.getenv("DOUBAO_SPEED_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
DOUBAO_ENDPOINT_ID = os.getenv("DOUBAO_ENDPOINT_ID", "")
DOUBAO_MODEL = os.getenv("DOUBAO_MODEL", os.getenv("DOUBAO_SPEED_MODEL", "doubao-1-5-pro-32k-250115"))

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

OLLAMA_BASE = "http://localhost:11434/v1"
OLLAMA_MODELS = ["qwen2.5:7b", "qwen2.5:14b"]
OLLAMA_TIMEOUT = 180
OLLAMA_CHECK_TIMEOUT = 120
OLLAMA_FORCE_CPU = False

DEFAULT_TIMEOUT = 90
MAX_RETRIES = 2
RETRY_DELAY = 3


def chat(
    prompt: str, system: str = "你是专业金融投研分析师。", temperature: float = 0.5, max_tokens: int = 2000
) -> str:
    """
    三级降级对话 (B3.4.4: 委托给 LLMRouter)

    返回: AI 回复文本，全部不可用时返回兜底信息
    """
    if not _LLM_ROUTER_AVAILABLE or _router_chat is None:
        return "⚠️ AI 分析不可用：LLMRouter 未加载。请检查 utils.alpha.llm_router 模块。"

    try:
        result = _router_chat(prompt, system=system, temperature=temperature, max_tokens=max_tokens)
        if result:
            return result
    except Exception as e:
        print(f"  ⚠️ LLMRouter.chat 异常: {str(e)[:200]}")

    return "⚠️ AI 分析不可用：所有 LLM provider 失败。请检查 API Key 和网络连接。"


def generate_analysis(
    system_prompt: str, user_prompt: str, report_type: str = "general", temperature: float = 0.5, max_tokens: int = 2000
) -> str:
    """
    生成结构化分析（与 deepseek_investment_summary 兼容的接口）

    返回: 纯文本分析结果（不含标签前缀）
    """
    return chat(prompt=user_prompt, system=system_prompt, temperature=temperature, max_tokens=max_tokens)


def test_connection() -> Dict[str, Any]:
    """
    测试各级连接状态 (B3.4.4: 委托给 LLMRouter.test_connection)。

    返回:
        {
            "doubao":   {"ok": bool, "model": str, "latency_ms": int, "error": str},
            "deepseek": {"ok": bool, "model": str, "latency_ms": int, "error": str},
            "ollama":   {"ok": bool, "model": str, "latency_ms": int, "error": str},
            "any_ok":   bool,
        }
    """
    if not _LLM_ROUTER_AVAILABLE or _router_test_connection is None:
        return {
            "doubao": {"ok": False, "model": "", "latency_ms": 0, "error": "LLMRouter 未加载"},
            "deepseek": {"ok": False, "model": "", "latency_ms": 0, "error": "LLMRouter 未加载"},
            "ollama": {"ok": False, "model": "", "latency_ms": 0, "error": "LLMRouter 未加载"},
            "any_ok": False,
        }

    try:
        router_status = _router_test_connection()
        # 转换 LLMRouter 返回格式为旧格式 (向后兼容)
        providers = router_status.get("providers", {})
        result = {}
        for name in ["doubao", "deepseek", "ollama", "glm", "siliconflow", "omniroute"]:
            info = providers.get(name, {})
            result[name] = {
                "ok": info.get("ok", False),
                "model": info.get("model", ""),
                "latency_ms": info.get("latency_ms", 0),
                "error": info.get("error", ""),
            }
        result["any_ok"] = any(v["ok"] for v in result.values() if isinstance(v, dict))
        return result
    except Exception as e:
        return {
            "doubao": {"ok": False, "model": "", "latency_ms": 0, "error": str(e)[:200]},
            "deepseek": {"ok": False, "model": "", "latency_ms": 0, "error": str(e)[:200]},
            "ollama": {"ok": False, "model": "", "latency_ms": 0, "error": str(e)[:200]},
            "any_ok": False,
        }


def get_status_summary() -> str:
    """获取一行连接状态摘要"""
    status = test_connection()
    parts = []
    emoji = {True: "✅", False: "❌"}
    for name in ["doubao", "deepseek", "ollama"]:
        info = status.get(name, {})
        parts.append(f"{emoji[info['ok']]} {name}")
    return "  ".join(parts)


# ── 命令行测试入口 ────────────────────────────────────
if __name__ == "__main__":
    print("=" * 60)
    print("统一 LLM 客户端 — 连接测试 (B3.4.4: Thin Wrapper)")
    print(f"🕒 {os.popen('echo %DATE% %TIME%').read().strip()}")
    print("=" * 60)

    print("\nLLMRouter 状态:")
    print(f"  可用: {'✅' if _LLM_ROUTER_AVAILABLE else '❌'}")

    print("\n配置常量 (deprecated, 仅供向后兼容):")
    print(f"  豆包 API Key:  {'已配置' if DOUBAO_API_KEY else '❌ 未配置'}")
    print(f"  豆包 Endpoint: {DOUBAO_ENDPOINT_ID or DOUBAO_MODEL}")
    print(f"  DeepSeek API Key:  {'已配置' if DEEPSEEK_API_KEY else '❌ 未配置'}")
    print(f"  DeepSeek Model:    {DEEPSEEK_MODEL}")

    print("\n--- 连接测试 ---")
    status = test_connection()
    for name, info in status.items():
        if name == "any_ok":
            continue
        if not isinstance(info, dict):
            continue
        ok_str = "✅" if info["ok"] else "❌"
        print(f"  {ok_str} {name}: {'OK' if info['ok'] else info['error']} ({info['latency_ms']}ms)")

    print(f"\n总体状态: {'✅ 至少一个可用' if status['any_ok'] else '❌ 全部不可用'}")

    # 如果 DeepSeek 可用，做一次完整对话测试
    if status.get("deepseek", {}).get("ok"):
        print("\n--- 对话测试 ---")
        reply = chat("用一句话概括今天的宏观经济形势")
        print(f"  回复: {reply[:200]}...")
