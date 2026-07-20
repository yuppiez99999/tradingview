import subprocess
import os
import time
import sys

env = os.environ.copy()
env["OLLAMA_NUM_GPUS"] = "0"
env["OLLAMA_DEBUG"] = "INFO"

print("Starting Ollama with OLLAMA_NUM_GPUS=0...")
proc = subprocess.Popen(
    ["C:\\Users\\Administrator\\AppData\\Local\\Programs\\Ollama\\ollama.exe", "serve"],
    env=env,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    encoding="utf-8",
)

time.sleep(30)

import urllib.request
import json

try:
    req = urllib.request.Request("http://localhost:11434/api/tags", method="GET")
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        print("Ollama started successfully!")
        print("Models:", [m["name"] for m in data.get("models", [])])
except Exception as e:
    print(f"Failed to connect: {e}")
    proc.terminate()
    sys.exit(1)

body = json.dumps({
    "model": "qwen2.5:7b",
    "messages": [{"role": "user", "content": "ping"}]
}).encode("utf-8")

req = urllib.request.Request(
    "http://localhost:11434/v1/chat/completions",
    data=body,
    headers={"Content-Type": "application/json"},
    method="POST"
)

print("Testing chat completion...")
try:
    with urllib.request.urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read().decode("utf-8"))
        content = result.get("choices", [{}])[0].get("message", {}).get("content")
        print("Response:", content[:200] if content else "None")
        print("SUCCESS: Local LLM is working!")
except Exception as e:
    print(f"Failed: {e}")
    proc.terminate()
    sys.exit(1)