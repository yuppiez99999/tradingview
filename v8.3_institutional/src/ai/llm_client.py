# -*- coding: utf-8 -*-
"""
统一 LLM 客户端 — 豆包 Speed 主 + DeepSeek 备 + Ollama 兜底
===========================================================
将 AI 分析调用统一为三级降级链：
    豆包 Speed (Ark/火山引擎) → DeepSeek → Ollama 本地

用法:
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

环境变量:
    VOLCENGINE_API_KEY  — 豆包/火山引擎 API Key (必须)
    DOUBAO_ENDPOINT_ID  — Ark 推理端点 ID (如 ep-20240615023252-xxxxx)
    DOUBAO_MODEL        — 模型名称回退 (默认 doubao-1-5-pro-32k-250115)
    DEEPSEEK_API_KEY    — DeepSeek API Key (备用)
    DEEPSEEK_BASE_URL   — DeepSeek API 地址 (默认 https://api.deepseek.com)
    DEEPSEEK_MODEL      — DeepSeek 模型名 (默认 deepseek-chat)
"""

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List

# ── 自动加载 .env ────────────────────────────────────────
def _load_dotenv():
    """从项目根目录加载 .env 文件"""
    try:
        from dotenv import load_dotenv
        # 向上找项目根目录的 .env (llm_client.py 在 15_每日工作流/ 下)
        root = Path(__file__).resolve().parent.parent
        env_path = root / ".env"
        if env_path.exists():
            load_dotenv(env_path)
    except ImportError:
        pass

_load_dotenv()

# 修复 Windows GBK 编码
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# ── 豆包 Speed 配置 ────────────────────────────────────
DOUBAO_API_KEY = os.getenv("VOLCENGINE_API_KEY", "")
DOUBAO_BASE_URL = os.getenv("DOUBAO_SPEED_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
DOUBAO_ENDPOINT_ID = os.getenv("DOUBAO_ENDPOINT_ID", "")
DOUBAO_MODEL = os.getenv("DOUBAO_MODEL", os.getenv("DOUBAO_SPEED_MODEL", "doubao-1-5-pro-32k-250115"))

# ── DeepSeek 配置（备用）────────────────────────────────
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

# ── 从 config.yaml 回退 DeepSeek ──────────────────────
_CONFIG_YAML = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "02_舆情与竞品监控", "舆情监控", "config.yaml"
)
if not DEEPSEEK_API_KEY and os.path.isfile(_CONFIG_YAML):
    try:
        import yaml
        with open(_CONFIG_YAML, 'r', encoding='utf-8') as f:
            cfg = yaml.safe_load(f)
        ai_cfg = cfg.get('ai', {}) if cfg else {}
        DEEPSEEK_API_KEY = ai_cfg.get('api_key', '')
        DEEPSEEK_BASE_URL = ai_cfg.get('base_url', DEEPSEEK_BASE_URL)
        DEEPSEEK_MODEL = ai_cfg.get('model', DEEPSEEK_MODEL)
    except Exception:
        pass

# ── Ollama 配置（最终兜底）──────────────────────────────
OLLAMA_BASE = "http://localhost:11434/v1"
OLLAMA_MODELS = ["qwen2.5:7b", "qwen2.5:14b"]
OLLAMA_TIMEOUT = 180           # Ollama 推理超时（本地模型首次加载慢，需较大值）
OLLAMA_CHECK_TIMEOUT = 120     # 连接探测超时
OLLAMA_FORCE_CPU = False       # 强制 CPU 推理（GPU 显存不足/驱动异常时设为 True）

# ── 通用参数 ────────────────────────────────────────────
DEFAULT_TIMEOUT = 90
MAX_RETRIES = 2
RETRY_DELAY = 3


def _post_chat(base_url: str, api_key: str, model: str,
               messages: List[Dict], temperature: float = 0.5,
               max_tokens: int = 2000, timeout: int = DEFAULT_TIMEOUT,
               provider: str = "", extra_body: Optional[Dict] = None) -> Optional[str]:
    """
    通用 OpenAI 兼容 chat/completions 调用。
    返回: 模型回复文本，失败返回 None
    """
    import requests

    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if extra_body:
        payload.update(extra_body)

    last_error = None
    for attempt in range(1 + MAX_RETRIES):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
            if resp.status_code == 200:
                data = resp.json()
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                return content.strip() if content else None
            elif resp.status_code == 429:
                last_error = f"Rate limited (HTTP 429)"
                time.sleep(RETRY_DELAY * (attempt + 1))
            elif resp.status_code >= 500:
                last_error = f"Server error (HTTP {resp.status_code})"
                time.sleep(RETRY_DELAY)
            elif resp.status_code in (401, 403):
                last_error = f"Auth error (HTTP {resp.status_code}): {resp.text[:150]}"
                break  # 不重试认证错误
            else:
                last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                break
        except Exception as e:
            last_error = str(e)
            break

    if last_error:
        print(f"  [WARN] {provider}: {last_error}")
    return None


def _check_ollama() -> Optional[str]:
    """检测 Ollama 可用模型"""
    try:
        import requests
        r = requests.get(f"{OLLAMA_BASE}/models", timeout=5)
        if r.status_code == 200:
            available = set()
            for m in r.json().get('data', []):
                name = m.get('id', m.get('name', str(m)))
                available.add(name)
            for model in OLLAMA_MODELS:
                if model in available:
                    return model
    except Exception:
        pass
    return None


def chat(prompt: str, system: str = "你是专业金融投研分析师。",
         temperature: float = 0.5, max_tokens: int = 2000) -> str:
    """
    三级降级对话：豆包 Speed → DeepSeek → Ollama

    返回: AI 回复文本，全部不可用时返回兜底信息
    """
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": prompt},
    ]

    # ── 第 1 级: 豆包 Speed ──
    if DOUBAO_API_KEY:
        model = DOUBAO_ENDPOINT_ID or DOUBAO_MODEL
        print(f"  🟢 豆包 Speed ({model})...")
        result = _post_chat(
            DOUBAO_BASE_URL, DOUBAO_API_KEY, model,
            messages, temperature, max_tokens, provider="豆包 Speed"
        )
        if result:
            return f"[豆包 Speed] {result}"
        print(f"  🔄 豆包不可用，降级 DeepSeek...")

    # ── 第 2 级: DeepSeek ──
    if DEEPSEEK_API_KEY:
        print(f"  🟡 DeepSeek ({DEEPSEEK_MODEL})...")
        result = _post_chat(
            DEEPSEEK_BASE_URL, DEEPSEEK_API_KEY, DEEPSEEK_MODEL,
            messages, temperature, max_tokens, provider="DeepSeek"
        )
        if result:
            return f"[DeepSeek] {result}"
        print(f"  🔄 DeepSeek 不可用，降级 Ollama...")

    # ── 第 3 级: Ollama ──
    ollama_model = _check_ollama()
    if ollama_model:
        mode = "CPU" if OLLAMA_FORCE_CPU else "GPU"
        extra_body = {"num_gpu": 0} if OLLAMA_FORCE_CPU else None
        print(f"  🟠 Ollama ({ollama_model}, {mode})...")
        try:
            result = _post_chat(
                OLLAMA_BASE, "ollama", ollama_model,
                messages, temperature, max_tokens,
                timeout=OLLAMA_TIMEOUT, provider=f"Ollama({mode})",
                extra_body=extra_body
            )
            if result:
                return f"[Ollama {ollama_model}] {result}"
        except Exception as e:
            print(f"  ⚠️ Ollama {mode} 失败: {str(e)[:150]}")

    return "⚠️ AI 分析不可用：豆包 Speed / DeepSeek / Ollama 全部失败。请检查 API Key 和网络连接。"


def generate_analysis(system_prompt: str, user_prompt: str,
                      report_type: str = "general",
                      temperature: float = 0.5,
                      max_tokens: int = 2000) -> str:
    """
    生成结构化分析（与 deepseek_investment_summary 兼容的接口）

    返回: 纯文本分析结果（不含标签前缀）
    """
    full_result = chat(
        prompt=user_prompt,
        system=system_prompt,
        temperature=temperature,
        max_tokens=max_tokens
    )
    # 去掉前缀标签 [豆包 Speed] / [DeepSeek] / [Ollama ...]
    if full_result.startswith("[") and "] " in full_result[:40]:
        full_result = full_result.split("] ", 1)[1]
    return full_result


def test_connection() -> Dict[str, Any]:
    """
    测试各级连接状态。
    返回:
        {
            "doubao":   {"ok": bool, "model": str, "latency_ms": int, "error": str},
            "deepseek": {"ok": bool, "model": str, "latency_ms": int, "error": str},
            "ollama":   {"ok": bool, "model": str, "latency_ms": int, "error": str},
            "any_ok":   bool,
        }
    """
    results = {}

    # 豆包
    if DOUBAO_API_KEY:
        model = DOUBAO_ENDPOINT_ID or DOUBAO_MODEL
        t0 = time.time()
        reply = _post_chat(
            DOUBAO_BASE_URL, DOUBAO_API_KEY, model,
            [{"role": "user", "content": "回复OK即可"}],
            temperature=0.0, max_tokens=10, provider="豆包"
        )
        latency = int((time.time() - t0) * 1000)
        results["doubao"] = {
            "ok": bool(reply and "OK" in reply),
            "model": model,
            "latency_ms": latency,
            "error": "" if reply else "无响应"
        }
    else:
        results["doubao"] = {
            "ok": False, "model": "", "latency_ms": 0,
            "error": "VOLCENGINE_API_KEY 未配置"
        }

    # DeepSeek
    if DEEPSEEK_API_KEY:
        t0 = time.time()
        reply = _post_chat(
            DEEPSEEK_BASE_URL, DEEPSEEK_API_KEY, DEEPSEEK_MODEL,
            [{"role": "user", "content": "回复OK即可"}],
            temperature=0.0, max_tokens=10, provider="DeepSeek"
        )
        latency = int((time.time() - t0) * 1000)
        results["deepseek"] = {
            "ok": bool(reply and "OK" in reply),
            "model": DEEPSEEK_MODEL,
            "latency_ms": latency,
            "error": "" if reply else "无响应"
        }
    else:
        results["deepseek"] = {
            "ok": False, "model": "", "latency_ms": 0,
            "error": "DEEPSEEK_API_KEY 未配置"
        }

    # Ollama
    ollama_model = _check_ollama()
    if ollama_model:
        t0 = time.time()
        reply = _post_chat(
            OLLAMA_BASE, "ollama", ollama_model,
            [{"role": "user", "content": "回复OK即可"}],
            temperature=0.0, max_tokens=10, timeout=OLLAMA_CHECK_TIMEOUT, provider="Ollama"
        )
        latency = int((time.time() - t0) * 1000)
        results["ollama"] = {
            "ok": bool(reply and "OK" in reply),
            "model": ollama_model,
            "latency_ms": latency,
            "error": "" if reply else "无响应"
        }
    else:
        results["ollama"] = {
            "ok": False, "model": "", "latency_ms": 0,
            "error": "Ollama 未启动或模型不存在"
        }

    results["any_ok"] = any(v["ok"] for v in results.values() if isinstance(v, dict))
    return results


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
if __name__ == '__main__':
    print("=" * 60)
    print("统一 LLM 客户端 — 连接测试")
    print(f"🕒 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    print(f"\n豆包 Speed:")
    print(f"  API Key:  {'已配置' if DOUBAO_API_KEY else '❌ 未配置'}")
    print(f"  Endpoint: {DOUBAO_ENDPOINT_ID or DOUBAO_MODEL}")
    print(f"  Base URL: {DOUBAO_BASE_URL}")

    print(f"\nDeepSeek (备用):")
    print(f"  API Key:  {'已配置' if DEEPSEEK_API_KEY else '❌ 未配置'}")
    print(f"  Model:    {DEEPSEEK_MODEL}")
    print(f"  Base URL: {DEEPSEEK_BASE_URL}")

    print(f"\nOllama (兜底):")
    ollama_model = _check_ollama()
    print(f"  可用模型: {ollama_model or '❌ 未检测到'}")

    print(f"\n--- 连接测试 ---")
    status = test_connection()
    for name, info in status.items():
        if name == "any_ok":
            continue
        ok_str = "✅" if info["ok"] else "❌"
        print(f"  {ok_str} {name}: {'OK' if info['ok'] else info['error']} ({info['latency_ms']}ms)")

    print(f"\n总体状态: {'✅ 至少一个可用' if status['any_ok'] else '❌ 全部不可用'}")

    # 如果豆包可用，做一次完整对话测试
    if status.get("doubao", {}).get("ok"):
        print(f"\n--- 对话测试 ---")
        reply = chat("用一句话概括今天的宏观经济形势")
        print(f"  回复: {reply[:200]}...")
