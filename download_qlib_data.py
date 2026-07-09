# -*- coding: utf-8 -*-
"""
QLib 中国 A 股数据下载脚本（独立版，不依赖 qlib 编译）

直接从 GitHub Releases 下载预构建的 bin 格式数据并解压。
数据源: https://github.com/SunsetWolf/qlib_dataset/releases
"""
import os
import sys
import zipfile
import datetime
import shutil
from pathlib import Path

import requests
from tqdm import tqdm

# ============================================================
# 配置
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
TARGET_DIR = BASE_DIR / "qlib_data" / "cn_data"

REMOTE_URL = "https://github.com/SunsetWolf/qlib_dataset/releases/download"

# 下载文件名: v2/qlib_data_cn_1d_latest.zip
FILE_NAME = "v2/qlib_data_cn_1d_latest.zip"


def merge_url(file_name: str) -> str:
    """构造完整下载 URL"""
    return f"{REMOTE_URL}/{file_name}"


def download(url: str, target_path: Path):
    """下载文件到指定路径"""
    print(f"[下载] 正在从 {url} 下载...")
    resp = requests.get(url, stream=True, timeout=120, allow_redirects=True)
    resp.raise_for_status()

    total_size = int(resp.headers.get("Content-Length", 0))
    print(f"[下载] 文件大小: {total_size / 1024 / 1024:.1f} MB")

    chunk_size = 1024 * 64  # 64KB
    with tqdm(total=total_size, unit="B", unit_scale=True, desc="下载进度") as pbar:
        with open(target_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=chunk_size):
                if chunk:
                    f.write(chunk)
                    pbar.update(len(chunk))

    print(f"[下载] 完成: {target_path}")


def unzip(zip_path: Path, target_dir: Path, delete_old: bool = False):
    """解压 ZIP 文件"""
    target_dir.mkdir(parents=True, exist_ok=True)

    if delete_old:
        for name in ["features", "calendars", "instruments"]:
            p = target_dir / name
            if p.exists():
                print(f"[解压] 删除旧数据: {p}")
                shutil.rmtree(p)

    print(f"[解压] 正在解压 {zip_path.name} 到 {target_dir} ...")
    with zipfile.ZipFile(str(zip_path), "r") as zp:
        file_list = zp.namelist()
        for f in tqdm(file_list, desc="解压进度"):
            zp.extract(f, str(target_dir))

    print(f"[解压] 完成")


def main():
    print("=" * 70)
    print("QLib 中国 A 股数据下载 (独立版)")
    print("=" * 70)
    print(f"数据保存目录: {TARGET_DIR}")
    print(f"数据源: {REMOTE_URL}/{FILE_NAME}")
    print(f"注意: 下载文件较大 (~500MB+), 请耐心等待")
    print()

    # 检查是否已有数据
    if (TARGET_DIR / "features").exists():
        print(f"[跳过] 数据已存在: {TARGET_DIR}/features")
        print("       如需重新下载, 请先删除该目录")
        return

    TARGET_DIR.mkdir(parents=True, exist_ok=True)

    # 下载
    timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    zip_name = f"{timestamp}_qlib_data_cn_1d.zip"
    zip_path = TARGET_DIR / zip_name

    url = merge_url(FILE_NAME)

    try:
        download(url, zip_path)
    except requests.exceptions.HTTPError as e:
        print(f"[错误] 下载失败 (HTTP): {e}")
        # 尝试备用 URL (不带 v2 前缀)
        alt_url = merge_url("qlib_data_cn_1d_latest.zip")
        print(f"[重试] 尝试备用 URL: {alt_url}")
        download(alt_url, zip_path)
    except Exception as e:
        print(f"[错误] 下载失败: {e}")
        sys.exit(1)

    # 解压
    unzip(zip_path, TARGET_DIR, delete_old=False)

    # 删除 ZIP
    if zip_path.exists():
        zip_path.unlink()
        print(f"[清理] 已删除 ZIP: {zip_path.name}")

    print()
    print("=" * 70)
    print("下载完成!")
    print(f"  数据目录: {TARGET_DIR}")
    print(f"  包含: features/, calendars/, instruments/")
    print()
    print("  初始化代码:")
    print(f"    import qlib")
    print(f"    qlib.init(provider_uri=r'{TARGET_DIR}', region='cn')")
    print("=" * 70)


if __name__ == "__main__":
    main()
