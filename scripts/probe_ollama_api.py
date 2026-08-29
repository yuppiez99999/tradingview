import json
import urllib.error
import urllib.request

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
    with urllib.request.urlopen(req1, timeout=120) as resp:
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
    with urllib.request.urlopen(req2, timeout=120) as resp:
        body = json.loads(resp.read().decode("utf-8"))
        print("native_api=", body.get("response"))
except urllib.error.HTTPError as e:
    print("native_api_error=", e.code, e.reason)
    print("native_api_body=", e.read().decode("utf-8", errors="replace"))
