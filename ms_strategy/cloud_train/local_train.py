"""
本地 Docker 训练脚本
用 v7 镜像 + 挂载本地代码和数据，直接在本地跑训练
模型文件直接保存到本地，不用上传/下载
"""
import datetime
import subprocess
import sys
from pathlib import Path

from utils.datetime_utils import now_bj

PROJECT_DIR = Path(r"E:\各种PY程序\28-终极量化交易系统8.4")
IMAGE = "swr.cn-east-3.myhuaweicloud.com/qt1/qt-qlib-trainer:v7"
DATA_DIR = PROJECT_DIR / "qlib_data" / "cn_data"
SCRIPT = "ms_strategy/cloud_train/modelscope_train.py"
REPORT_DIR = PROJECT_DIR / "reports"

cmd = [
    "docker", "run", "--rm",
    f"-v={PROJECT_DIR}:/app",
    f"-v={DATA_DIR}:/data",
    "-w=/app",
    IMAGE,
    "python", SCRIPT,
    "--data-dir", "/data",
    "--market", "csi300",
    "--start", "2015-01-01", "--end", "2026-07-08",
    "--train-end", "2024-12-31", "--valid-end", "2025-06-30",
    "--num-leaves", "256", "--boost-round", "800",
    "--learning-rate", "0.01", "--max-depth", "10",
    "--label-days", "1",
    "--output", "/app/reports",
]

print(f"[{now_bj()}] 开始本地训练")
print(f"镜像: {IMAGE}")
print(f"数据: {DATA_DIR}")
print(f"输出: {REPORT_DIR}")
print(f"命令: {' '.join(cmd)}\n")

result = subprocess.run(cmd, text=True)
print(f"\n[{now_bj()}] 训练结束，返回码: {result.returncode}")
sys.exit(result.returncode)
