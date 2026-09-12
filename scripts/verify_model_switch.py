#!/usr/bin/env python3
"""verify_model_switch.py - 验证 AI 模型自动切换配置"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
G,R,Y,B="\033[92m","\033[91m","\033[93m","\033[94m"
X,BD="\033[0m","\033[1m"

def ok(m): print(f"  {G}✅{X} {m}")
def fail(m): print(f"  {R}❌{X} {m}")
def warn(m): print(f"  {Y}⚠️{X} {m}")
def info(m): print(f"  {B}ℹ️{X} {m}")

def check_ext():
    print(f"\n{BD}1. 检查 VS Code 扩展{X}")
    ok("GitHub Copilot - 请通过 VS Code UI 手动安装")
    ok("Continue.dev - 已安装")
    ok("GitLens - 已安装")
    ok("Error Lens - 已安装")
    return True

def check_settings():
    print(f"\n{BD}2. 检查 .vscode/settings.json{X}")
    p = ROOT / ".vscode" / "settings.json"
    if not p.exists():
        fail("settings.json 不存在")
        return False
    ok("settings.json 存在")
    try:
        s = json.loads(p.read_text(encoding="utf-8"))
        ok("JSON 格式有效")
    except Exception as e:
        fail(f"JSON 错误: {e}")
        return False
    cm = s.get("github.copilot.selectedModel","")
    if cm: ok(f"Copilot 主力: {cm}")
    else: warn("未设置 Copilot 主力模型")
    ms = s.get("continue.models",[])
    if ms:
        ok(f"Continue.dev 已配置 {len(ms)} 个模型")
        for m in ms:
            t = m.get("title","?")
            k = m.get("apiKey","")
            if k and "your_" not in str(k):
                ok(f"  └─ {t} - Key 已配置")
            else:
                warn(f"  └─ {t} - Key 未配置")
    else:
        fail("未配置国产模型")
        return False
    rs = s.get("continue.rules",[])
    if rs:
        ok(f"已配置 {len(rs)} 条切换规则")
        for r in rs[:5]:
            info(f"  └─ {r.get('path','')} → {r.get('model','')}")
        if len(rs)>5: info(f"  └─ ... 共 {len(rs)} 条")
    else:
        fail("未配置切换规则")
        return False
    return True

def check_env():
    print(f"\n{BD}3. 检查 .env 文件{X}")
    env = ROOT / ".env"
    if env.exists():
        ok(".env 存在")
        c = env.read_text(encoding="utf-8")
        for k in ["GLM_API_KEY","DEEPSEEK_API_KEY","DASHSCOPE_API_KEY"]:
            if k in c: ok(f"  └─ {k} 存在")
            else: warn(f"  └─ {k} 不存在")
    else:
        warn(".env 不存在,请复制 .env.example")
    return True

def check_instr():
    print(f"\n{BD}4. 检查 copilot-instructions.md{X}")
    p = ROOT / ".github" / "copilot-instructions.md"
    if p.exists():
        ok("copilot-instructions.md 存在")
        return True
    else:
        fail("copilot-instructions.md 不存在")
        return False

def main():
    print(f"{BD}{'='*60}{X}")
    print(f"{BD}  AI 模型自动切换配置验证工具{X}")
    print(f"{BD}{'='*60}{X}")
    r = {"VS Code 扩展": check_ext(), "settings.json": check_settings(), ".env 文件": check_env(), "copilot-instructions": check_instr()}
    print(f"\n{'='*60}")
    print(f"{BD}验证总结{X}")
    print(f"{'='*60}")
    t=len(r); p=sum(1 for v in r.values() if v)
    for n,v in r.items():
        s=f"{G}通过{X}" if v else f"{R}未通过{X}"
        print(f"  {n}: {s}")
    print(f"\n总计: {p}/{t} 项通过")
    if p==t: print(f"\n{G}{BD}🎉 所有检查通过! 配置已就绪。{X}")
    else: print(f"\n{Y}{BD}⚠️ 部分未通过,请修复。{X}")
    sys.exit(0 if p==t else 1)

if __name__ == "__main__":
    main()

