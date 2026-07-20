import subprocess
import os
import sys

env = os.environ.copy()
env["OLLAMA_NUM_GPUS"] = "0"

result = subprocess.run(
    ["ollama", "run", "qwen2.5:7b", "ping"],
    env=env,
    capture_output=True,
    text=True,
    encoding="utf-8",
    timeout=120,
)

print(f"Return code: {result.returncode}")
print(f"Stdout: {result.stdout[:500] if result.stdout else 'None'}")
print(f"Stderr: {result.stderr[:500] if result.stderr else 'None'}")