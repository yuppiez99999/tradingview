# -*- coding: utf-8 -*-
"""调试 Wind MCP 新闻接口"""
import sys, os, json, subprocess
sys.path.insert(0, r'e:\各种PY程序\28-终极量化交易系统7.1')

SKILL_DIR = r'e:\各种PY程序\28-终极量化交易系统7.1\.agents\skills\wind-mcp-skill'
CLI_PATH = os.path.join(SKILL_DIR, 'scripts', 'cli.mjs')
node = r'C:\Program Files\nodejs\node.exe'

# 调用 CLI: node scripts/cli.mjs call financial_docs get_financial_news '{"query":"中国神华","top_k":3}'
args = [node, CLI_PATH, 'call', 'financial_docs', 'get_financial_news', json.dumps({"query": "中国神华", "top_k": 3})]
print('CMD:', ' '.join(args))
print()

proc = subprocess.run(args, cwd=SKILL_DIR, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60)
print('STDOUT:')
print(proc.stdout[:3000])
print()
print('STDERR:')
print(proc.stderr[:1000])
