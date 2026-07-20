# -*- coding: utf-8 -*-
"""系统健康检查 — 模块可用性 + 自动交易任务诊断"""
import os, sys, json, subprocess, importlib.util
from datetime import datetime
from pathlib import Path

BASE = Path(r"e:\各种PY程序\28-终极量化交易系统7.1")
PY = r"C:\Program Files\Python38\python.exe"

sys.stdout.reconfigure(encoding='utf-8')

# ---------- 1. 关键模块文件 ----------
print("=" * 60)
print("[1] 关键模块文件存在性检查")
print("=" * 60)
critical_files = {
    "主系统入口": "comprehensive_quant_system_v7.py",
    "自动执行系统": "automated_execution_system.py",
    "系统集成 (P0)": "system_integration.py",
    "止损监控 (步骤4)": "stop_loss_monitor.py",
    "波动率止损规则": "config/stop_loss_vol_adjusted.yaml",
    "持仓文件": "config/positions.json",
    "系统配置": "system_config.json",
    "v7.5 信号融合": "v7.5_institutional/src/alpha/signal_fusion.py",
    "v7.5 漂移检测": "v7.5_institutional/src/ml/drift_detector.py",
    "v7.5 成本感知回测": "v7.5_institutional/src/backtest/cost_aware_backtest.py",
    "v7.5 成本模型": "v7.5_institutional/src/backtest/cost_model.py",
}
missing = []
for name, rel in critical_files.items():
    p = BASE / rel
    ok = p.exists()
    mark = "[OK]" if ok else "[MISSING]"
    print(f"  {mark} {name}: {rel}")
    if not ok:
        missing.append(rel)

# ---------- 2. 关键 Python 依赖 ----------
print()
print("=" * 60)
print("[2] Python 依赖检查")
print("=" * 60)
deps = ["pandas", "numpy", "yaml", "scipy", "qlib", "lightgbm", "sklearn"]
for dep in deps:
    try:
        mod = importlib.import_module(dep)
        ver = getattr(mod, "__version__", "unknown")
        print(f"  [OK] {dep} ({ver})")
    except ImportError as e:
        print(f"  [FAIL] {dep}: {e}")

# ---------- 3. 关键 Python 模块导入测试 ----------
print()
print("=" * 60)
print("[3] Python 模块导入测试")
print("=" * 60)
sys.path.insert(0, str(BASE))
import_tests = [
    ("automated_execution_system", "AutomatedExecutionSystem"),
    ("system_integration", "IntegratedExecutionSystem"),
    ("stop_loss_monitor", "StopLossMonitor"),
]
for mod_name, cls_name in import_tests:
    try:
        mod = importlib.import_module(mod_name)
        cls = getattr(mod, cls_name, None)
        if cls:
            print(f"  [OK] {mod_name}.{cls_name}")
        else:
            print(f"  [FAIL] {mod_name}.{cls_name} 不存在")
    except Exception as e:
        print(f"  [FAIL] {mod_name}: {type(e).__name__}: {e}")

# ---------- 4. v7.5 子模块导入 ----------
print()
print("=" * 60)
print("[4] v7.5 子模块导入测试")
print("=" * 60)
v75_src = BASE / "v7.5_institutional" / "src"
sys.path.insert(0, str(v75_src.parent))
v75_tests = [
    ("src.alpha.signal_fusion", "SignalFusion"),
    ("src.ml.drift_detector", "ModelDriftDetector"),
    ("src.backtest.cost_aware_backtest", "CostAwareBacktest"),
]
for mod_name, cls_name in v75_tests:
    try:
        mod = importlib.import_module(mod_name)
        cls = getattr(mod, cls_name, None)
        if cls:
            print(f"  [OK] {mod_name}.{cls_name}")
        else:
            print(f"  [FAIL] {mod_name}.{cls_name} 不存在")
    except Exception as e:
        print(f"  [FAIL] {mod_name}: {type(e).__name__}: {e}")

# ---------- 5. 报告生成情况 ----------
print()
print("=" * 60)
print("[5] 最近报告生成情况")
print("=" * 60)
reports_dir = BASE / "reports"
if reports_dir.exists():
    today = datetime.now().strftime("%Y%m%d")
    yesterday = "20260708"
    files = sorted(reports_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    today_files = [f for f in files if today in f.name]
    yesterday_files = [f for f in files if yesterday in f.name]
    print(f"  今天 ({today}) 生成: {len(today_files)} 个报告")
    for f in today_files[:5]:
        print(f"    - {f.name}")
    print(f"  昨天 ({yesterday}) 生成: {len(yesterday_files)} 个报告")
    for f in yesterday_files[:3]:
        print(f"    - {f.name}")
else:
    print("  [FAIL] reports 目录不存在")

# ---------- 6. 定时任务批处理文件 ----------
print()
print("=" * 60)
print("[6] 失败任务批处理文件检查")
print("=" * 60)
bat_files = [
    "run_pre_market.bat",      # 盘前
    "trading_scheduler.bat",   # 交易调度
    "run_daily_report.bat",     # 日报
    "run_hn_daily.bat",        # HN 日报
    "run_start_live.bat",      # 启动实盘
]
for bat in bat_files:
    p = BASE / bat
    if p.exists():
        size = p.stat().st_size
        print(f"  [OK] {bat} ({size} bytes)")
        # 读取前几行查看内容
        try:
            content = p.read_text(encoding='utf-8', errors='ignore')
            for line in content.split('\n')[:3]:
                if line.strip() and not line.startswith('@'):
                    print(f"       | {line.strip()[:80]}")
        except Exception:
            pass
    else:
        print(f"  [MISSING] {bat}")

# ---------- 7. 服务进程状态 ----------
print()
print("=" * 60)
print("[7] 服务进程状态")
print("=" * 60)
pid_file = BASE / "service.pid"
if pid_file.exists():
    content = pid_file.read_text().strip()
    print(f"  service.pid 内容: {content}")
    # 检查进程是否存活
    try:
        pid = int(content.split('\n')[0].strip())
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}"],
            capture_output=True, text=True, timeout=5
        )
        if str(pid) in result.stdout:
            print(f"  [OK] 进程 {pid} 存活")
        else:
            print(f"  [FAIL] 进程 {pid} 已退出 (service.pid 已过期, 建议删除)")
    except Exception as e:
        print(f"  [WARN] 无法检查进程: {e}")
else:
    print("  [INFO] 无 service.pid")
    print("  [INFO] 服务模式已迁移至 Windows 任务计划程序 (v75_PreMarket 等)")

# 检查关键 Windows 任务计划任务状态
try:
    result = subprocess.run(
        ["schtasks", "/Query", "/TN", "v75_PreMarket", "/FO", "LIST"],
        capture_output=True, text=True, timeout=5
    )
    if result.returncode == 0:
        # 提取 NextRun 和 LastRun
        for line in result.stdout.split('\n'):
            line = line.strip()
            if line.startswith(('NextRun:', 'LastRun:', 'LastResult:')):
                print(f"  [TASK] v75_PreMarket {line}")
except Exception as e:
    print(f"  [WARN] 无法查询任务计划: {e}")

# ---------- 8. Wind MCP 连接测试 ----------
print()
print("=" * 60)
print("[8] Wind MCP 连接测试")
print("=" * 60)
try:
    # 直接调用 wind_mcp_fetcher
    sys.path.insert(0, str(BASE))
    from wind_mcp_fetcher import fetch_realtime_price
    price = fetch_realtime_price("600036.SH")  # 招商银行
    if price and price > 0:
        print(f"  [OK] Wind MCP 连接正常, 600036.SH = {price}")
    else:
        print(f"  [FAIL] Wind MCP 返回无效价格: {price}")
except Exception as e:
    print(f"  [FAIL] Wind MCP 连接失败: {type(e).__name__}: {e}")

# ---------- 9. 持仓文件状态 ----------
print()
print("=" * 60)
print("[9] 持仓文件状态")
print("=" * 60)
pos_file = BASE / "config" / "positions.json"
if pos_file.exists():
    data = json.loads(pos_file.read_text(encoding='utf-8'))
    positions = data.get("positions")
    if isinstance(positions, dict):
        print(f"  [OK] positions.json 是 dict 格式, {len(positions)} 个持仓")
        # 检查每个持仓是否有必要字段
        if positions:
            sample = next(iter(positions.values()))
            required = ["code", "shares", "est_price"]
            missing_fields = [f for f in required if f not in sample]
            if missing_fields:
                print(f"  [WARN] 持仓缺少字段: {missing_fields}")
            else:
                print(f"  [OK] 持仓字段完整 (code/shares/est_price)")
    elif isinstance(positions, list):
        print(f"  [WARN] positions.json 是 list 格式, {len(positions)} 个代码 (需要规范化)")
    else:
        print(f"  [FAIL] positions.json 格式未知: {type(positions)}")
else:
    print("  [FAIL] positions.json 不存在")

# ---------- 总结 ----------
print()
print("=" * 60)
print("[总结]")
print("=" * 60)
print(f"  关键文件: {len(critical_files) - len(missing)}/{len(critical_files)} 存在")
if missing:
    print(f"  缺失文件: {missing}")
print(f"  检查时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
