import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def _get_ollama_path() -> str:
    return os.environ.get(
        "OLLAMA_PATH",
        str(Path.home() / "AppData" / "Local" / "Programs" / "Ollama" / "ollama.exe"),
    )


env = os.environ.copy()
env["OLLAMA_NUM_GPUS"] = "0"
env["OLLAMA_DEBUG"] = "INFO"

print("Killing existing ollama processes...")
subprocess.run(["taskkill", "/F", "/IM", "ollama.exe"], capture_output=True)
subprocess.run(["taskkill", "/F", "/IM", "llama-server.exe"], capture_output=True)
time.sleep(3)

print("Starting Ollama with OLLAMA_NUM_GPUS=0...")
proc = subprocess.Popen(
    [_get_ollama_path(), "serve"],
    env=env,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    encoding="utf-8",
)

time.sleep(10)

try:
    req = urllib.request.Request("http://localhost:11434/api/tags", method="GET")
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        print("Ollama API is accessible!")
        print("Models:", [m["name"] for m in data.get("models", [])])
except Exception as e:
    print(f"API not ready: {e}")

time.sleep(30)

body = json.dumps(
    {"model": "qwen2.5:7b", "messages": [{"role": "user", "content": "ping"}]}
).encode("utf-8")

req = urllib.request.Request(
    "http://localhost:11434/v1/chat/completions",
    data=body,
    headers={"Content-Type": "application/json"},
    method="POST",
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
