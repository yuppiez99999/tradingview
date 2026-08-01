"""
free-stockdb 数据同步进度监控

用法:
    python check_fs_sync.py          # 检查一次
    python check_fs_sync.py --watch  # 持续监控 (每 30 秒刷新)
"""

import os
import sys
import time
import requests


FS_DIR = r"D:\free-stockdb\stockdb"
FS_HTTP = "http://127.0.0.1:7899"
TEST_CODES = ["600519", "601318", "000001", "600036", "688041"]


def check_sync_status():
    print("=" * 60)
    print("free-stockdb 数据同步状态")
    print("=" * 60)

    # 1. 数据目录
    data_dir = os.path.join(FS_DIR, "data")
    total_size = 0
    file_count = 0
    writing_count = 0
    if os.path.exists(data_dir):
        for f in os.listdir(data_dir):
            fp = os.path.join(data_dir, f)
            if os.path.isfile(fp):
                total_size += os.path.getsize(fp)
                file_count += 1
                if f.endswith(".part"):
                    writing_count += 1
    size_mb = total_size / (1024 * 1024)
    print(f"\n[数据目录] {file_count} 个文件, {size_mb:.2f} MB (写入中: {writing_count})")

    # 2. 进程检查
    try:
        import psutil

        procs = []
        for p in psutil.process_iter(["name", "pid", "cpu_percent"]):
            if p.info["name"] in ("数据更新.exe", "stockdb.exe"):
                procs.append(p.info)
        if procs:
            print("\n[运行进程]")
            for p in procs:
                print(f"  - {p['name']} (PID:{p['pid']})")
        else:
            print("\n[运行进程] ❌ 数据更新.exe 或 stockdb.exe 未运行")
    except ImportError:
        pass

    # 3. HTTP API 检查
    try:
        r = requests.get(FS_HTTP, timeout=2)
        http_ok = r.status_code in (200, 400)
        print(f"\n[HTTP API] {'✅ 在线' if http_ok else '❌ 离线'} ({FS_HTTP})")
    except Exception as e:
        print(f"\n[HTTP API] ❌ 离线 - {e}")
        http_ok = False

    # 4. 测试标的数据查询
    if http_ok:
        print("\n[标的数据]")
        ready_count = 0
        for code in TEST_CODES:
            try:
                r = requests.get(f"{FS_HTTP}/?cmd=get&t=复权:{code}:2024*", timeout=3)
                data = r.json()
                count = len(data) if isinstance(data, list) else 0
                if count > 0:
                    ready_count += 1
                    status = f"✅ {count} 行"
                else:
                    status = "⏳ 同步中"
                print(f"  {code}: {status}")
            except Exception:
                print(f"  {code}: ❌ 查询失败")

        if ready_count == len(TEST_CODES):
            print("\n🎉 所有测试标的数据已就绪，可以运行验证脚本!")
            print("   python verify_free_stockdb.py")
        elif ready_count > 0:
            print(f"\n📊 部分标的已就绪 ({ready_count}/{len(TEST_CODES)}), 继续等待...")
        else:
            print(f"\n⏳ 数据同步进行中，请耐心等待... (已同步 {size_mb:.2f} MB)")

    print()
    return http_ok and ready_count == len(TEST_CODES)


if __name__ == "__main__":
    if "--watch" in sys.argv:
        while True:
            ready = check_sync_status()
            if ready:
                break
            time.sleep(30)
    else:
        check_sync_status()
