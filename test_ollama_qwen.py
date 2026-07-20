# -*- coding: utf-8 -*-
"""验证本地 Ollama + Qwen2.5 是否可用"""
import os
import sys
import urllib.request
import json

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:1.5b")


def chat(prompt: str, system: str = "", temperature: float = 0.3, max_tokens: int = 200) -> str:
    url = OLLAMA_BASE_URL.rstrip("/") + "/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer ollama",
    }
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=120) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    message = body.get("choices", [{}])[0].get("message", {})
    return message.get("content") or message.get("reasoning_content") or ""


def main():
    print("OLLAMA_BASE_URL=", OLLAMA_BASE_URL)
    print("OLLAMA_MODEL=", OLLAMA_MODEL)
    prompt = "请用一句话总结当前A股市场特征。"
    print("prompt=", prompt)
    answer = chat(prompt, system="你是一个专业的金融分析助手。", temperature=0.3, max_tokens=200)
    print("answer=", answer)


if __name__ == "__main__":
    main()
