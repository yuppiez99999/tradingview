# -*- coding: utf-8 -*-
"""验证本地 Ollama + Qwen2.5 是否可用"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from utils.llm_client import chat, test_connection


def main():
    print("llm_connection_test=start")
    status = test_connection()
    print("connection_status=", status)

    prompt = "请用一句话总结当前A股市场特征。"
    print("prompt=", prompt)
    answer = chat(prompt, system="你是一个专业的金融分析助手。", temperature=0.3, max_tokens=200)
    print("answer=", answer)


if __name__ == "__main__":
    main()
