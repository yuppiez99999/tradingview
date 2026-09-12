import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

# CLI 直跑时 sys.path[0] 为脚本目录, 顶层 utils 不可见 → 显式补项目根
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from utils.safe_url import safe_urlopen  # noqa: E402  (bandit B310: 全项目 urlopen 收口)

base = "http://localhost:11434"
model = "qwen2.5:1.5b"
prompt = "你好，请用一句话回复。"

# 方式1：OpenAI 兼容
url1 = base.rstrip("/") + "/v1/chat/completions"
payload1 = json.dumps(
    {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": 200,
    }
).encode("utf-8")
req1 = urllib.request.Request(
    url1,
    data=payload1,
    headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer ollama",
    },
)
try:
    with safe_urlopen(req1, timeout=120) as resp:
        body = json.loads(resp.read().decode("utf-8"))
        print(
            "openai_compat=",
            body.get("choices", [{}])[0].get("message", {}).get("content"),
        )
except urllib.error.HTTPError as e:
    print("openai_compat_error=", e.code, e.reason)
    print("openai_compat_body=", e.read().decode("utf-8", errors="replace"))

# 方式2：Ollama 原生
url2 = base.rstrip("/") + "/api/generate"
payload2 = json.dumps(
    {
        "model": model,
        "prompt": prompt,
        "stream": False,
    }
).encode("utf-8")
req2 = urllib.request.Request(
    url2, data=payload2, headers={"Content-Type": "application/json"}
)
try:
    with safe_urlopen(req2, timeout=120) as resp:
        body = json.loads(resp.read().decode("utf-8"))
        print("native_api=", body.get("response"))
except urllib.error.HTTPError as e:
    print("native_api_error=", e.code, e.reason)
    print("native_api_body=", e.read().decode("utf-8", errors="replace"))
