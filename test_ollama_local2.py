import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fifteen_每日工作流 import llm_client

print("Testing local LLM with CPU mode...")
print(f"OLLAMA_NUM_GPUS: {os.environ.get('OLLAMA_NUM_GPUS')}")
print(f"OLLAMA_MODEL: {llm_client.OLLAMA_MODEL}")

result = llm_client.chat("ping", system="", temperature=0.1, max_tokens=50)
if result:
    print("SUCCESS!")
    print(f"Response: {result}")
else:
    print("FAILED - trying backup method...")
    result = llm_client._chat_ollama("ping", system="", temperature=0.1, max_tokens=50)
    if result:
        print("SUCCESS with CLI method!")
        print(f"Response: {result}")
    else:
        print("ALL METHODS FAILED")