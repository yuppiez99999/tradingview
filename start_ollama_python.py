import os
import subprocess
import time

env = os.environ.copy()
env["OLLAMA_NUM_GPUS"] = "0"

proc = subprocess.Popen(
    ["ollama", "serve"],
    env=env,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    encoding="utf-8",
)

print("Ollama server starting...")
time.sleep(5)

import json  # noqa: E402
import urllib.request  # noqa: E402

try:
    req = urllib.request.Request("http://localhost:11434/api/tags", method="GET")
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        print("Ollama started successfully!")
        print("Models:", [m["name"] for m in data.get("models", [])])
except Exception as e:
    print(f"Failed to connect: {e}")
    proc.terminate()
