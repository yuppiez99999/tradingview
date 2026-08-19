# 导入依赖
import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

# ============================================================
# 项目根目录与 .env 加载
# ============================================================
_ENV_PATH = Path(__file__).resolve().parent.parent.parent / ".env"

def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ[key.strip()] = value.strip()

_load_env_file(_ENV_PATH)

# ============================================================
# LLM 提供商配置
# ============================================================

# 豆包 Speed（火山引擎 Ark）
VOLCENGINE_API_KEY: str = os.environ.get("VOLCENGINE_API_KEY", "")
DEEPSEEK_API_KEY: str = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL: str = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL: str = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
DEEPSEEK_REASONER_MODEL: str = os.environ.get("DEEPSEEK_REASONER_MODEL", "deepseek-reasoner")

# 智谱 GLM
GLM_API_KEY: str = os.environ.get("GLM_API_KEY", "")
GLM_BASE_URL: str = os.environ.get("GLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
GLM_MODEL: str = os.environ.get("GLM_MODEL", "glm-5.2")

# 腾讯混元 Hy3 Preview
HY3_API_KEY: str = os.environ.get("HY3_API_KEY", "")
HY3_BASE_URL: str = os.environ.get("HY3_BASE_URL", "https://tokenhub.tencentmaas.com/v1")
HY3_MODEL: str = os.environ.get("HY3_MODEL", "hy3-preview")

# 百度智能云千帆
QIANFAN_API_KEY: str = os.environ.get("QIANFAN_API_KEY", "")
QIANFAN_SECRET_KEY: str = os.environ.get("QIANFAN_SECRET_KEY", "")
QIANFAN_BASE_URL: str = os.environ.get(
    "QIANFAN_BASE_URL",
    "https://aip.baidubce.com/rpc/2.0/ai_custom/v1/wenxinworkshop/chat",
)
QIANFAN_MODEL: str = os.environ.get("QIANFAN_MODEL", "ERNIE-4.0-Turbo")

# 豆包 Speed 默认 endpoint（可按实际部署替换）
DOUBAO_SPEED_BASE_URL: str = os.environ.get(
    "DOUBAO_SPEED_BASE_URL",
    "https://ark.cn-beijing.volces.com/api/v3",
)
DOUBAO_SPEED_MODEL: str = os.environ.get("DOUBAO_SPEED_MODEL", "doubao-speed")

# 本地 Ollama（默认使用 Qwen2.5:3b 小模型, 低内存环境稳定可用; 7b/14b 在内存不足时会 OOM）
OLLAMA_BASE_URL: str = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL: str = os.environ.get("OLLAMA_MODEL", "qwen2.5:3b")
OLLAMA_DEEP_MODEL: str = os.environ.get("OLLAMA_DEEP_MODEL", "deepseek-r1:14b")

# GPU 配置：优先 GPU，显存不足自动回退 CPU
# 可通过环境变量 OLLAMA_NUM_GPUS 手动指定显卡数量，0=纯CPU
if "OLLAMA_NUM_GPUS" not in os.environ:
    try:
        import subprocess as _sp
        _r = _sp.run(["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                     capture_output=True, text=True, timeout=5)
        if _r.returncode == 0 and _r.stdout.strip():
            _vram_mb = float(_r.stdout.strip().split("\n")[0].strip())
            if _vram_mb >= 8000:
                os.environ["OLLAMA_NUM_GPUS"] = "1"
            else:
                os.environ["OLLAMA_NUM_GPUS"] = "0"
        else:
            os.environ["OLLAMA_NUM_GPUS"] = "0"
    except Exception:
        os.environ["OLLAMA_NUM_GPUS"] = "0"

# Ollama 服务进程
_ollama_process = None
_ollama_lock = threading.Lock()

# MC2 修复: Provider 熔断器 — 记录失败时间, 冷却期内跳过 (避免每次重试不可用 provider 浪费 60s)
# 冷却时间 300 秒 (5 分钟), 无 Key 的 provider 不计入熔断 (直接跳过)
_PROVIDER_FAILURE_TIME: Dict[str, float] = {}
_PROVIDER_COOLDOWN_SEC = 300  # 5 分钟冷却


def _provider_available(name: str, has_key: bool) -> bool:
    """检查 provider 是否可用 (有 Key 且不在熔断冷却期)"""
    if not has_key:
        return False
    last_fail = _PROVIDER_FAILURE_TIME.get(name)
    if last_fail is not None:
        elapsed = time.time() - last_fail
        if elapsed < _PROVIDER_COOLDOWN_SEC:
            return False  # 冷却期内, 跳过
    return True


def _record_provider_failure(name: str) -> None:
    """记录 provider 失败, 启动冷却期"""
    _PROVIDER_FAILURE_TIME[name] = time.time()


def _record_provider_success(name: str) -> None:
    """记录 provider 成功, 清除冷却"""
    _PROVIDER_FAILURE_TIME.pop(name, None)


# ============================================================
# Ollama 服务管理
# ============================================================

def _is_ollama_running() -> bool:
    """检查 Ollama 服务是否正在运行"""
    try:
        req = urllib.request.Request(f"{OLLAMA_BASE_URL.rstrip('/')}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=5):
            return True
    except Exception:
        return False


def _start_ollama_server() -> bool:
    """启动 Ollama 服务（自动 GPU/CPU 切换）"""
    global _ollama_process
    with _ollama_lock:
        if _is_ollama_running():
            return True
        if _ollama_process is not None and _ollama_process.poll() is None:
            return True

        env = os.environ.copy()
        num_gpus = os.environ.get("OLLAMA_NUM_GPUS", "")
        if num_gpus:
            env["OLLAMA_NUM_GPUS"] = num_gpus

        _ollama_default = os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe")
        ollama_exe = Path(os.environ.get("OLLAMA_PATH", _ollama_default))
        if not ollama_exe.exists():
            ollama_exe = Path("ollama")

        try:
            _ollama_process = subprocess.Popen(
                [str(ollama_exe), "serve"],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
            )
            for _ in range(20):
                time.sleep(2)
                if _is_ollama_running():
                    return True
            _ollama_process.terminate()
            _ollama_process = None
            return False
        except Exception:
            return False


# ============================================================
# 底层 HTTP 调用
# ============================================================

def _request_chat_completion(base_url: str, api_key: str, model: str,
                             prompt: str, system: str = "",
                             temperature: float = 0.3,
                             max_tokens: int = 2000,
                             timeout: int = 60,
                             endpoint_path: str = "/v1/chat/completions") -> Optional[str]:
    # MC1 修复: 各 provider 的 base_url 已含版本路径时, endpoint_path 应为 "/chat/completions"
    # - DeepSeek: base_url=https://api.deepseek.com → endpoint=/v1/chat/completions (默认)
    # - 豆包: base_url=https://ark.cn-beijing.volces.com/api/v3 → endpoint=/chat/completions
    # - GLM: base_url=https://open.bigmodel.cn/api/paas/v4 → endpoint=/chat/completions
    # - HY3: base_url=https://tokenhub.tencentmaas.com/v1 → endpoint=/chat/completions
    try:
        url = base_url.rstrip("/") + endpoint_path
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = json.dumps({
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }).encode("utf-8")

        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        # MC5 修复: 使用无代理 opener, 避免系统代理拒绝转发国内金融 API
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))

        message = body.get("choices", [{}])[0].get("message", {})
        content = message.get("content")
        if not content:
            content = message.get("reasoning_content")
        return content if isinstance(content, str) else None
    except Exception:
        return None


def _request_qianfan_chat(base_url: str, api_key: str, secret_key: str,
                          model: str, prompt: str, system: str = "",
                          temperature: float = 0.3, max_tokens: int = 2000,
                          timeout: int = 60) -> Optional[str]:
    try:
        url = base_url.rstrip("/")
        headers = {
            "Content-Type": "application/json",
        }
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = json.dumps({
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }).encode("utf-8")

        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))

        content = body.get("choices", [{}])[0].get("message", {}).get("content")
        return content if isinstance(content, str) else None
    except Exception:
        return None


# ============================================================
# 各提供商适配
# ============================================================

def _chat_qianfan(prompt: str, system: str = "",
                  temperature: float = 0.3, max_tokens: int = 2000) -> Optional[str]:
    if not QIANFAN_API_KEY:
        return None
    return _request_qianfan_chat(
        base_url=QIANFAN_BASE_URL,
        api_key=QIANFAN_API_KEY,
        secret_key=QIANFAN_SECRET_KEY,
        model=QIANFAN_MODEL,
        prompt=prompt,
        system=system,
        temperature=temperature,
        max_tokens=max_tokens,
    )


def _chat_hy3(prompt: str, system: str = "",
              temperature: float = 0.3, max_tokens: int = 2000) -> Optional[str]:
    if not HY3_API_KEY:
        return None
    # MC1 修复: HY3 base_url 已含 /v1, endpoint 应为 /chat/completions
    return _request_chat_completion(
        base_url=HY3_BASE_URL,
        api_key=HY3_API_KEY,
        model=HY3_MODEL,
        prompt=prompt,
        system=system,
        temperature=temperature,
        max_tokens=max_tokens,
        endpoint_path="/chat/completions",
    )


def _chat_glm(prompt: str, system: str = "",
              temperature: float = 0.3, max_tokens: int = 2000) -> Optional[str]:
    if not GLM_API_KEY:
        return None
    # MC1 修复: GLM base_url 已含 /api/paas/v4, endpoint 应为 /chat/completions
    return _request_chat_completion(
        base_url=GLM_BASE_URL,
        api_key=GLM_API_KEY,
        model=GLM_MODEL,
        prompt=prompt,
        system=system,
        temperature=temperature,
        max_tokens=max_tokens,
        endpoint_path="/chat/completions",
    )


def _chat_doubao(prompt: str, system: str = "",
                 temperature: float = 0.3, max_tokens: int = 2000) -> Optional[str]:
    # MC1 修复: 豆包 base_url 已含 /api/v3, endpoint 应为 /chat/completions
    # 原代码拼接 /api/v3 + /v1/chat/completions = /api/v3/v1/chat/completions (404)
    try:
        return _request_chat_completion(
            base_url=DOUBAO_SPEED_BASE_URL,
            api_key=VOLCENGINE_API_KEY,
            model=DOUBAO_SPEED_MODEL,
            prompt=prompt,
            system=system,
            temperature=temperature,
            max_tokens=max_tokens,
            endpoint_path="/chat/completions",
        )
    except Exception:
        return None


def _chat_deepseek(prompt: str, system: str = "",
                    temperature: float = 0.3, max_tokens: int = 2000) -> Optional[str]:
    """DeepSeek V3 对话 (主 LLM)"""
    if not DEEPSEEK_API_KEY:
        return None
    return _request_chat_completion(
        base_url=DEEPSEEK_BASE_URL,
        api_key=DEEPSEEK_API_KEY,
        model=DEEPSEEK_MODEL,
        prompt=prompt,
        system=system,
        temperature=temperature,
        max_tokens=max_tokens,
    )


def _chat_deepseek_reasoner(prompt: str, system: str = "",
                             temperature: float = 0.3, max_tokens: int = 4000) -> Optional[str]:
    """DeepSeek R1 推理模型 (深度思考, 主 deep 模型)

    适用于复杂交易决策 (对冲/仓位/多标的联动)、多维度风险评估、长周期趋势研判。
    返回 content + reasoning_content (思考过程)。
    """
    if not DEEPSEEK_API_KEY:
        return None
    try:
        url = DEEPSEEK_BASE_URL.rstrip("/") + "/v1/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        }
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = json.dumps({
            "model": DEEPSEEK_REASONER_MODEL,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }).encode("utf-8")

        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = json.loads(resp.read().decode("utf-8"))

        message = body.get("choices", [{}])[0].get("message", {})
        content = message.get("content")
        if not content:
            content = message.get("reasoning_content")
        if isinstance(content, str) and content:
            # 附带思考过程摘要 (如有)
            reasoning = message.get("reasoning_content", "")
            if reasoning and len(reasoning) > 50 and reasoning != content:
                return f"{content.strip()}\n\n---\n_思考过程：{reasoning.strip()[:500]}_"
            return content.strip()
        return None
    except Exception:
        return None


def _chat_ollama(prompt: str, system: str = "",
                 temperature: float = 0.3, max_tokens: int = 2000,
                 model: Optional[str] = None) -> Optional[str]:
    try:
        _start_ollama_server()
        env = os.environ.copy()
        num_gpus = os.environ.get("OLLAMA_NUM_GPUS", "")
        if num_gpus:
            env["OLLAMA_NUM_GPUS"] = num_gpus
        full_prompt = f"{system}\n\n{prompt}" if system else prompt
        use_model = model or OLLAMA_MODEL
        proc = subprocess.run(
            ["ollama", "run", use_model, full_prompt],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=300,
        )
        if proc.returncode == 0 and proc.stdout:
            return proc.stdout.strip()
        elif proc.returncode != 0 and proc.stderr:
            if "out of memory" in proc.stderr or "cudaMalloc failed" in proc.stderr:
                pass
        return None
    except Exception:
        return None


def _chat_ollama_api(prompt: str, system: str = "",
                     temperature: float = 0.3, max_tokens: int = 2000,
                     model: Optional[str] = None) -> Optional[str]:
    try:
        _start_ollama_server()
        use_model = model or OLLAMA_MODEL
        return _request_chat_completion(
            base_url=OLLAMA_BASE_URL,
            api_key="ollama",
            model=use_model,
            prompt=prompt,
            system=system,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    except Exception:
        return None


# ============================================================
# 公开 API
# ============================================================

def chat(prompt: str, system: str = "",
         temperature: float = 0.3, max_tokens: int = 2000) -> Optional[str]:
    """多级降级聊天调用 (DeepSeek 优先 + MC2 熔断器)

    MC2 修复:
      1. 添加 Provider 熔断器: 失败 provider 5 分钟内不再重试, 避免每次 60s 超时
      2. 降级链对齐 AGENTS.md: DeepSeek (主) → GLM → Ollama (2026-08-18 剔除豆包/HY3/千帆)
      3. 无 Key 的 provider 直接跳过, 不触发超时
    """
    # 2026-08-18 调整: 用户指定降级链 DeepSeek → GLM → Ollama (剔除豆包/HY3/千帆)
    providers = [
        ("deepseek", _chat_deepseek, bool(DEEPSEEK_API_KEY)),
        ("glm", _chat_glm, bool(GLM_API_KEY)),
        ("ollama_api", _chat_ollama_api, True),  # 本地服务始终尝试
        ("ollama_cli", _chat_ollama, True),
    ]

    for name, chat_fn, has_key in providers:
        # 熔断器: 无 Key 或冷却期内直接跳过
        if not _provider_available(name, has_key):
            continue
        try:
            result = chat_fn(prompt, system, temperature, max_tokens)
            if result:
                _record_provider_success(name)
                return result
            else:
                _record_provider_failure(name)
        except Exception:
            _record_provider_failure(name)

    return None


def generate_analysis(prompt: str, temperature: float = 0.3,
                      max_tokens: int = 2000) -> Optional[str]:
    """生成分析文本（兼容旧接口）"""
    return chat(prompt=prompt, system="你是一个专业的金融分析助手。",
                temperature=temperature, max_tokens=max_tokens)


def chat_deep(prompt: str, system: str = "",
              temperature: float = 0.3, max_tokens: int = 4000) -> Optional[str]:
    """深度思考模式：使用 DeepSeek R1 (deepseek-reasoner) 推理模型进行复杂决策分析

    适用于：
    - 复杂交易决策（对冲、仓位调整、多标的联动）
    - 多维度风险评估
    - 长周期趋势研判
    - 复杂逻辑推导

    主路径: DeepSeek R1 云端推理 (质量最高)
    备用: Ollama deepseek-r1:14b 本地推理 (离线兜底)
    """
    deep_system = system or (
        "你是一位资深的量化交易专家，擅长深度推理和复杂决策。"
        "请先进行严谨的分析推理，再给出最终结论。"
        "结论部分请用清晰的结构呈现。"
    )
    # ★ 主路径: DeepSeek R1 云端推理模型 (deepseek-reasoner)
    result = _chat_deepseek_reasoner(prompt, deep_system, temperature, max_tokens)
    if result:
        return result
    # 备用 1: Ollama API 方式（本地 deepseek-r1:14b, 支持 reasoning_content）
    result = _chat_ollama_deep_api(prompt, deep_system, temperature, max_tokens)
    if result:
        return result
    # 备用 2: Ollama CLI 方式
    result = _chat_ollama(prompt, deep_system, temperature, max_tokens, model=OLLAMA_DEEP_MODEL)
    if result:
        return result
    # 兜底降级到普通 chat（质量稍差但能返回）
    return chat(prompt, system, temperature, max_tokens)


def _chat_ollama_deep_api(prompt: str, system: str = "",
                          temperature: float = 0.3,
                          max_tokens: int = 4000) -> Optional[str]:
    """深度推理模型的 API 调用，支持提取 reasoning_content"""
    try:
        _start_ollama_server()
        url = OLLAMA_BASE_URL.rstrip("/") + "/api/chat"
        headers = {"Content-Type": "application/json"}
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = json.dumps({
            "model": OLLAMA_DEEP_MODEL,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }).encode("utf-8")

        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=300) as resp:
            body = json.loads(resp.read().decode("utf-8"))

        message = body.get("message", {})
        content = message.get("content", "")
        reasoning = message.get("reasoning_content", "")
        if content:
            if reasoning and len(reasoning) > 50:
                return f"{content.strip()}\n\n---\n_思考过程：{reasoning.strip()[:500]}_"
            return content.strip()
        return None
    except Exception:
        return None


def test_connection() -> Dict[str, Any]:
    """连通性探测 (DeepSeek 优先)"""
    providers = {
        "deepseek": bool(DEEPSEEK_API_KEY),
        "hy3": bool(HY3_API_KEY),
        "qianfan": bool(QIANFAN_API_KEY),
        "glm": bool(GLM_API_KEY),
        "doubao": bool(VOLCENGINE_API_KEY),
        "ollama": True,
    }
    available = None
    # DeepSeek 优先探测
    for name in ("deepseek", "hy3", "qianfan", "glm", "doubao", "ollama"):
        fn = {
            "deepseek": _chat_deepseek,
            "hy3": _chat_hy3,
            "qianfan": _chat_qianfan,
            "glm": _chat_glm,
            "doubao": _chat_doubao,
            "ollama": _chat_ollama,
        }[name]
        result = fn("ping", system="", temperature=0.1, max_tokens=10)
        if result is not None:
            available = name
            break
    return {
        "providers": providers,
        "available": available,
        "status": "ok" if available else "degraded",
    }
