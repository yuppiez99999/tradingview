import sys
sys.path.insert(0, r'e:\各种PY程序\28-终极量化交易系统7.1')
import importlib.util
spec = importlib.util.spec_from_file_location('llm_client', r'e:\各种PY程序\28-终极量化交易系统7.1\15_每日工作流\llm_client.py')
llm_client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(llm_client)
print(llm_client.test_connection())