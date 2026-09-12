"""
本地 Docker 回测脚本
用训练好的模型 + TopkDropoutStrategy 做回测
"""
import datetime
import glob
import subprocess
import sys
from pathlib import Path

from utils.datetime_utils import now_bj

PROJECT_DIR = Path(r"E:\各种PY程序\28-终极量化交易系统8.4")
IMAGE = "swr.cn-east-3.myhuaweicloud.com/qt1/qt-qlib-trainer:v7"
DATA_DIR = PROJECT_DIR / "qlib_data" / "cn_data"
SCRIPT = "ms_strategy/cloud_train/backtest.py"

# 自动找最新的模型文件
model_files = sorted(glob.glob(str(PROJECT_DIR / "reports" / "qlib_model_*.pkl")))
if not model_files:
    print("错误: 找不到模型文件 reports/qlib_model_*.pkl")
    sys.exit(1)
model_file = model_files[-1]
model_in_container = f"/app/reports/{Path(model_file).name}"

cmd = [
    "docker", "run", "--rm",
    f"-v={PROJECT_DIR}:/app",
    f"-v={DATA_DIR}:/data",
    "-w=/app",
    IMAGE,
    "python", SCRIPT,
    "--data-dir", "/data",
    "--market", "csi300",
    "--start", "2025-06-30", "--end", "2026-07-08",
    "--model-path", model_in_container,
    "--topk", "10", "--n-drop", "2",
    "--benchmark", "SH000300",
]

print(f"[{now_bj()}] 开始本地回测")
print(f"模型: {model_file}")
print(f"命令: {' '.join(cmd)}\n")

result = subprocess.run(cmd, text=True)
print(f"\n[{now_bj()}] 回测结束，返回码: {result.returncode}")
sys.exit(result.returncode)
