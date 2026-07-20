import os, sys, json, urllib.request
from pathlib import Path

api_key = "sk-DiljrtNjfeQhpXjO9yLSlHqwN3SQ9WCBWCnTkxagzsBjelJu"

providers = [
    {"name": "DeepSeek", "url": "https://api.deepseek.com/chat/completions", "model": "deepseek-chat"},
    {"name": "OpenAI", "url": "https://api.openai.com/v1/chat/completions", "model": "gpt-3.5-turbo"},
    {"name": "Moonshot", "url": "https://api.moonshot.cn/v1/chat/completions", "model": "moonshot-v1-8k"},
    {"name": "GLM-ZAI", "url": "https://api.z.ai/v4/chat/completions", "model": "glm-5.2"},
    {"name": "GLM-Coding", "url": "https://open.bigmodel.cn/api/coding/v4/chat/completions", "model": "glm-5.2"},
    {"name": "GLM-Anthropic", "url": "https://open.bigmodel.cn/api/anthropic/v1/messages", "model": "claude-3-opus-20240229"},
]

for provider in providers:
    print(f"\n=== Testing {provider['name']} ===")
    print(f"URL: {provider['url']}")
    print(f"Model: {provider['model']}")
    
    try:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        
        if "anthropic" in provider['url']:
            payload = json.dumps({
                "model": provider['model'],
                "max_tokens": 50,
                "messages": [{"role": "user", "content": "hello"}],
            }).encode("utf-8")
        else:
            payload = json.dumps({
                "model": provider['model'],
                "messages": [{"role": "user", "content": "hello"}],
                "temperature": 0.3,
                "max_tokens": 50,
            }).encode("utf-8")
        
        req = urllib.request.Request(provider['url'], data=payload, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                print(f"\n✓ SUCCESS!")
                print(f"Response: {json.dumps(body, ensure_ascii=False)[:500]}")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8")[:200]
            print(f"✗ HTTP {e.code}: {body}")
        except urllib.error.URLError as e:
            print(f"✗ Connection error: {e.reason}")
    except Exception as e:
        print(f"✗ Error: {type(e).__name__}: {e}")