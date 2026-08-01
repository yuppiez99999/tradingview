# -*- coding: utf-8 -*-
"""
v8.5 模块集成状态验证脚本

快速检查9个v8.5新增模块是否存在并可导入。
"""
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("v85_module_check")


def check_module_import(module_path: str, class_name: Optional[str] = None):
    """检查模块是否可以导入"""
    try:
        if class_name:
            parts = module_path.split('.')
            module = __import__(module_path)
            for part in parts[1:]:
                module = getattr(module, part)
            getattr(module, class_name)
            return True, None
        else:
            __import__(module_path)
            return True, None
    except ImportError as e:
        return False, f"ImportError: {e}"
    except AttributeError as e:
        return False, f"AttributeError: {class_name} 不存在 - {e}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def main():
    logger.info("="*70)
    logger.info("v8.5 模块集成状态检查")
    logger.info("="*70)
    
    # v8.5模块列表 (基于实际文件路径)
    modules = [
        {"path": "src.utils.environment_isolation", "class": "EnvironmentIsolation", "name": "环境隔离管理器"},
        {"path": "src.execution.ntp_sync", "class": "NTPSync", "name": "时间同步(PTP)"},
        {"path": "src.risk.vega_monitor", "class": "VegaMonitor", "name": "Vega监控"},
        {"path": "src.risk.liquidity_monitor", "class": "LiquidityMonitor", "name": "流动性监控"},
        {"path": "src.risk.evt_tail_risk", "class": "ExtremeValueAnalyzer", "name": "EVT肥尾建模"},
    ]
    
    results = []
    passed = 0
    failed = 0
    
    for i, mod in enumerate(modules, 1):
        logger.info(f"[{i}/{len(modules)}] 检查 {mod['name']}...")
        success, error = check_module_import(mod['path'], mod['class'])
        
        if success:
            passed += 1
            status = "可用"
        else:
            failed += 1
            status = f"不可用: {error}"
        
        results.append({
            "index": i,
            "name": mod['name'],
            "module_path": mod['path'],
            "class_name": mod['class'],
            "status": status
        })
        
        icon = "[OK]" if success else "[XX]"
        logger.info(f"  {icon} {mod['name']}: {status}")
    
    # 打印总结
    print("\n" + "="*70)
    print("v8.5 模块集成状态总结")
    print("="*70)
    print(f"总模块数: {len(modules)}")
    print(f"  [OK] 可用: {passed}")
    print(f"  [XX] 不可用: {failed}")
    if len(modules) > 0:
        print(f"集成率: {passed/len(modules)*100:.1f}%")
    print("="*70)
    
    # 保存结果
    output_dir = Path(__file__).parent / "reports"
    output_dir.mkdir(exist_ok=True)
    
    output_file = output_dir / f"v85_module_status_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump({
            "total": len(modules),
            "available": passed,
            "unavailable": failed,
            "integration_rate": f"{passed/len(modules)*100:.1f}%",
            "modules": results,
            "timestamp": datetime.now().isoformat()
        }, f, ensure_ascii=False, indent=2)
    
    logger.info(f"检查结果已保存至: {output_file}")
    
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
