import sys

sys.path.insert(0, r"e:\各种PY程序\28-终极量化交易系统7.1\15_每日工作流")
from llm_client import chat, test_connection

print("=== LLM 连通性探测 ===")
print(test_connection())
print("\n=== 本地 Qwen 测试 ===")
reply = chat("请用一句话介绍A股开盘时间", system="你是金融助手", temperature=0.3, max_tokens=200)
print(reply)
