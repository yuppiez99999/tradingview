# 导入依赖
import os
import json
import urllib.request
import urllib.error
import subprocess
import time
import threading
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

# 本地 Ollama（默认使用本机已有的 Qwen2.5:7b）
OLLAMA_BASE_URL: str = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL: str = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")
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

        ollama_exe = Path(os.environ.get("OLLAMA_PATH", "C:\\Users\\Administrator\\AppData\\Local\\Programs\\Ollama\\ollama.exe"))
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
                             timeout: int = 60) -> Optional[str]:
    try:
        url = base_url.rstrip("/") + "/v1/chat/completions"
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
        with urllib.request.urlopen(req, timeout=timeout) as resp:
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
    return _request_chat_completion(
        base_url=HY3_BASE_URL,
        api_key=HY3_API_KEY,
        model=HY3_MODEL,
        prompt=prompt,
        system=system,
        temperature=temperature,
        max_tokens=max_tokens,
    )


def _chat_glm(prompt: str, system: str = "",
              temperature: float = 0.3, max_tokens: int = 2000) -> Optional[str]:
    if not GLM_API_KEY:
        return None
    return _request_chat_completion(
        base_url=GLM_BASE_URL,
        api_key=GLM_API_KEY,
        model=GLM_MODEL,
        prompt=prompt,
        system=system,
        temperature=temperature,
        max_tokens=max_tokens,
    )


def _chat_doubao(prompt: str, system: str = "",
                 temperature: float = 0.3, max_tokens: int = 2000) -> Optional[str]:
    try:
        return _request_chat_completion(
            base_url=DOUBAO_SPEED_BASE_URL,
            api_key=VOLCENGINE_API_KEY,
            model=DOUBAO_SPEED_MODEL,
            prompt=prompt,
            system=system,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    except Exception:
        return None


def _chat_deepseek(prompt: str, system: str = "",
                    temperature: float = 0.3, max_tokens: int = 2000) -> Optional[str]:
    if not DEEPSEEK_API_KEY:
        return None
    return _request_chat_completion(
        base_url=DEEPSEEK_BASE_URL,
        api_key=DEEPSEEK_API_KEY,
        model="deepseek-chat",
        prompt=prompt,
        system=system,
        temperature=temperature,
        max_tokens=max_tokens,
    )


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
    """六级降级聊天调用"""
    # ★ 优先使用本地 Ollama API（HTTP方式更可靠）
    result = _chat_ollama_api(prompt, system, temperature, max_tokens)
    if result:
        return result
    # 备用：CLI方式
    result = _chat_ollama(prompt, system, temperature, max_tokens)
    if result:
        return result
    result = _chat_hy3(prompt, system, temperature, max_tokens)
    if result:
        return result
    result = _chat_qianfan(prompt, system, temperature, max_tokens)
    if result:
        return result
    result = _chat_glm(prompt, system, temperature, max_tokens)
    if result:
        return result
    result = _chat_doubao(prompt, system, temperature, max_tokens)
    if result:
        return result
    return _chat_deepseek(prompt, system, temperature, max_tokens)


def generate_analysis(prompt: str, temperature: float = 0.3,
                      max_tokens: int = 2000) -> Optional[str]:
    """生成分析文本（兼容旧接口）"""
    return chat(prompt=prompt, system="你是一个专业的金融分析助手。",
                temperature=temperature, max_tokens=max_tokens)


def chat_deep(prompt: str, system: str = "",
              temperature: float = 0.3, max_tokens: int = 4000) -> Optional[str]:
    """深度思考模式：使用 deepseek-r1:14b 推理模型进行复杂决策分析

    适用于：
    - 复杂交易决策（对冲、仓位调整、多标的联动）
    - 多维度风险评估
    - 长周期趋势研判
    - 复杂逻辑推导

    速度较慢（CPU 模式约 1-3 分钟），但推理质量更高。
    """
    deep_system = system or (
        "你是一位资深的量化交易专家，擅长深度推理和复杂决策。"
        "请先进行严谨的分析推理，再给出最终结论。"
        "结论部分请用清晰的结构呈现。"
    )
    # 优先 Ollama API 方式（支持 reasoning_content）
    result = _chat_ollama_deep_api(prompt, deep_system, temperature, max_tokens)
    if result:
        return result
    # 备用 CLI 方式
    result = _chat_ollama(prompt, deep_system, temperature, max_tokens, model=OLLAMA_DEEP_MODEL)
    if result:
        return result
    # 兜底降级到云 API 的 chat（质量稍差但能返回）
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
    """连通性探测"""
    providers = {
        "hy3": bool(HY3_API_KEY),
        "qianfan": bool(QIANFAN_API_KEY),
        "glm": bool(GLM_API_KEY),
        "doubao": bool(VOLCENGINE_API_KEY),
        "deepseek": bool(DEEPSEEK_API_KEY),
        "ollama": True,
    }
    available = None
    for name in ("hy3", "qianfan", "glm", "doubao", "deepseek", "ollama"):
        fn = {
            "hy3": _chat_hy3,
            "qianfan": _chat_qianfan,
            "glm": _chat_glm,
            "doubao": _chat_doubao,
            "deepseek": _chat_deepseek,
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
