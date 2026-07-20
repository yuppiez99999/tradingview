import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import importlib.util
spec = importlib.util.spec_from_file_location("llm_client", os.path.join(os.path.dirname(os.path.abspath(__file__)), "15_每日工作流", "llm_client.py"))
llm_client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(llm_client)

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