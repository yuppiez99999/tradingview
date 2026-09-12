import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path

try:  # 仓库内运行时
    from utils.safe_url import safe_urlopen
except ImportError:  # 独立脚本运行
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))
    from utils.safe_url import safe_urlopen


def _get_ollama_path() -> str:
    return os.environ.get(
        "OLLAMA_PATH",
        str(Path.home() / "AppData" / "Local" / "Programs" / "Ollama" / "ollama.exe"),
    )


subprocess.run(["taskkill", "/F", "/IM", "ollama.exe"], capture_output=True)
subprocess.run(["taskkill", "/F", "/IM", "llama-server.exe"], capture_output=True)
time.sleep(3)

print("Starting Ollama with GPU...")
proc = subprocess.Popen(
    [_get_ollama_path(), "serve"],
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    encoding="utf-8",
)

time.sleep(10)

try:
    req = urllib.request.Request("http://localhost:11434/api/tags", method="GET")
    with safe_urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        print("Ollama API is accessible!")
        print("Models:", [m["name"] for m in data.get("models", [])])
except Exception as e:
    print(f"API not ready: {e}")

time.sleep(30)

body = json.dumps(
    {
        "model": "qwen2.5:7b",
        "messages": [{"role": "user", "content": "ping"}],
        "stream": False,
    }
).encode("utf-8")

req = urllib.request.Request(
    "http://localhost:11434/v1/chat/completions",
    data=body,
    headers={"Content-Type": "application/json"},
    method="POST",
)

print("\nTesting chat completion...")
try:
    with safe_urlopen(req, timeout=120) as resp:
        result = json.loads(resp.read().decode("utf-8"))
        content = result.get("choices", [{}])[0].get("message", {}).get("content")
        print("SUCCESS!")
        print("Response:", content[:200] if content else "None")
        print("\nLocal LLM is working!")
except Exception as e:
    print(f"Failed: {e}")
    proc.terminate()
