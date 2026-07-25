import subprocess
import os
import time
import sys
import json
import urllib.request

env = os.environ.copy()
env["OLLAMA_NUM_GPUS"] = "0"
env["OLLAMA_DEBUG"] = "INFO"

print("Killing existing ollama processes...")
subprocess.run(["taskkill", "/F", "/IM", "ollama.exe"], capture_output=True)
subprocess.run(["taskkill", "/F", "/IM", "llama-server.exe"], capture_output=True)
time.sleep(3)

print("Starting Ollama with OLLAMA_NUM_GPUS=0...")
proc = subprocess.Popen(
    ["C:\\Users\\Administrator\\AppData\\Local\\Programs\\Ollama\\ollama.exe", "serve"],
    env=env,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    encoding="utf-8",
    creationflags=subprocess.CREATE_NEW_CONSOLE if sys.platform == "win32" else 0,
)

time.sleep(8)

try:
    req = urllib.request.Request("http://localhost:11434/api/tags", method="GET", timeout=10)
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        print("Ollama API is accessible!")
        print("Models:", [m["name"] for m in data.get("models", [])])
except Exception as e:
    print(f"API not ready yet: {e}")

time.sleep(20)

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

print("\nTesting chat completion...")
try:
    with urllib.request.urlopen(req, timeout=120) as resp:
        result = json.loads(resp.read().decode("utf-8"))
        content = result.get("choices", [{}])[0].get("message", {}).get("content")
        print("SUCCESS!")
        print("Response:", content[:200] if content else "None")
        print("\nLocal LLM is working!")
except Exception as e:
    print(f"Failed: {e}")
    proc.terminate()
    sys.exit(1)