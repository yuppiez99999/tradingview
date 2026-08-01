# -*- coding: utf-8 -*-
import sys

sys.path.insert(0, r"E:\各种PY程序\15_每日工作流")

from llm_client import chat, test_connection

print("=== 测试 LLM 连接 ===")
status = test_connection()
for name, info in status.items():
    if name == "any_ok":
        continue
    ok_str = "✅" if info["ok"] else "❌"
    print(f"  {ok_str} {name}: {'OK' if info['ok'] else info['error']} ({info['latency_ms']}ms)")

print(f"\n总体状态: {'✅ 至少一个可用' if status['any_ok'] else '❌ 全部不可用'}")

if status.get("ollama", {}).get("ok"):
    print("\n=== 测试 Ollama 对话 ===")
    reply = chat("你好，请用一句话回复")
    print(f"  回复: {reply[:200]}")
