#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
量化交易系统 - 高价值技能集成验证脚本
生成时间: 2026-07-23
目的: 验证TOP 8技能是否正确安装并可用
"""

import os
import sys
from pathlib import Path

def check_skill_installed(skill_name, skill_path):
    """检查技能是否已安装"""
    exists = os.path.exists(skill_path)
    status = "[OK] 已安装" if exists else "[NO] 未安装"
    print(f"{status:12} | {skill_name}")
    return exists

def main():
    print("=" * 80)
    print("量化交易系统 - 高价值技能安装验证")
    print("=" * 80)
    print()
    
    # 技能路径配置
    skills = [
        ("dcf-model", r"C:\Users\Administrator\.codebuddy\skills\dcf-model\SKILL.md"),
        ("sector_rotation_radar_skill", r"C:\Users\Administrator\.codebuddy\skills\sector_rotation_radar_skill\SKILL.md"),
        ("earnings-analysis", r"C:\Users\Administrator\.codebuddy\skills\earnings-analysis\SKILL.md"),
        ("breakout_candidate_finder_skill", r"C:\Users\Administrator\.codebuddy\skills\breakout_candidate_finder_skill\SKILL.md"),
        ("market_regime_switch_skill", r"C:\Users\Administrator\.codebuddy\skills\market_regime_switch_skill\SKILL.md"),
        ("trade_plan_builder_skill", r"C:\Users\Administrator\.codebuddy\skills\trade_plan_builder_skill\SKILL.md"),
        ("institutional_position_shift_skill", r"C:\Users\Administrator\.codebuddy\skills\institutional_position_shift_skill\SKILL.md"),
        ("bull_bear_case_builder_skill", r"C:\Users\Administrator\.codebuddy\skills\bull_bear_case_builder_skill\SKILL.md"),
    ]
    
    installed_count = 0
    for name, path in skills:
        if check_skill_installed(name, path):
            installed_count += 1
    
    print()
    print("=" * 80)
    print("安装统计: %d/%d 个技能已安装" % (installed_count, len(skills)))
    print("=" * 80)
    
    if installed_count == len(skills):
        print("\n恭喜!所有技能已成功安装!")
        return 0
    else:
        print("\n注意: 有 %d 个技能未安装,请检查" % (len(skills) - installed_count))
        return 1

if __name__ == "__main__":
    sys.exit(main())
