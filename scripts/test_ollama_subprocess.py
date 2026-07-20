# -*- coding: utf-8 -*-
"""
通过 subprocess 调用 ollama run 验证本地 Qwen2.5:1.5b 是否可用。
"""
import subprocess
import sys

OLLAMA_BIN = r"C:\Users\Administrator\AppData\Local\Programs\Ollama\ollama.exe"
MODEL = "qwen2.5:1.5b"
PROMPT = "请用一句话总结当前A股市场特征。"


def main():
    print("ollama_bin=", OLLAMA_BIN)
    print("model=", MODEL)
    print("prompt=", PROMPT)
    cmd = [OLLAMA_BIN, "run", MODEL, "--nowordwrap", PROMPT]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
    except Exception as e:
        print("exception=", repr(e))
        sys.exit(1)

    print("returncode=", proc.returncode)
    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    if stdout:
        print("stdout=", stdout)
    if stderr:
        print("stderr=", stderr)


if __name__ == "__main__":
    main()
