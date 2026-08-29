#!/usr/bin/env python
"""
一键完成: 安装 scipy → 全量强制重训 LGB Enhanced 模型

执行方式: python scripts/install_scipy_and_train.py
用时估算: 下载 2-3 分钟(scipy 32MB) + 训练 5-15 分钟(26 个标的)
"""

import os
import subprocess
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(BASE_DIR, "qlib_env", "Scripts", "python.exe")
SCRIPTS = os.path.dirname(os.path.abspath(__file__))

# ===================================================================
# STEP 1: 安装 scipy
# ===================================================================
print("=" * 60)
print("STEP 1: 安装 scipy 1.10.1 (Python 3.8 兼容)")
print("=" * 60)

# 检查是否已正常
r = subprocess.run(
    [PY, "-X", "utf8", "-c", 'import scipy.sparse; print("scipy OK")'],
    capture_output=True,
    text=True,
    timeout=10,
)
if r.returncode == 0:
    print("scipy 已正常, 跳过安装")
else:
    print("scipy 不可用, 开始下载安装...")
    print("(如果卡住超过 5 分钟, 请 Ctrl+C 后手动执行: ")
    print(" pip install --no-cache-dir scipy==1.10.1 --only-binary scipy)")
    print()

    # 方法 1: pip 清华镜像
    print(">>> 尝试方法1: pip 清华镜像...")
    env = os.environ.copy()
    for k in list(env.keys()):
        if "proxy" in k.lower():
            del env[k]

    r = subprocess.run(
        [
            PY,
            "-X",
            "utf8",
            "-m",
            "pip",
            "install",
            "scipy==1.10.1",
            "--no-cache-dir",
            "--force-reinstall",
            "--only-binary",
            "scipy",
            "-i",
            "https://pypi.tuna.tsinghua.edu.cn/simple",
            "--trusted-host",
            "pypi.tuna.tsinghua.edu.cn",
            "--proxy=",
        ],
        capture_output=False,
        env=env,
        timeout=600,
    )

    if r.returncode != 0:
        print("\n>>> 尝试方法2: pip 官方 PyPI...")
        r = subprocess.run(
            [
                PY,
                "-X",
                "utf8",
                "-m",
                "pip",
                "install",
                "scipy==1.10.1",
                "--no-cache-dir",
                "--force-reinstall",
                "--only-binary",
                "scipy",
                "-i",
                "https://pypi.org/simple",
                "--proxy=",
            ],
            capture_output=False,
            env=env,
            timeout=600,
        )

    if r.returncode != 0:
        print("\n>>> 尝试方法3: requests 直连下载...")
        try:
            import requests

            s = requests.Session()
            s.trust_env = False

            resp = s.get("https://pypi.org/pypi/scipy/1.10.1/json")
            data = resp.json()
            url = None
            for f in data["urls"]:
                if "cp38" in f["filename"] and "win_amd64" in f["filename"]:
                    url = f["url"]
                    break
            if url:
                print(f"  下载: {os.path.basename(url)}")
                dl = s.get(url, stream=True)
                whl = os.path.join(SCRIPTS, os.path.basename(url))
                with open(whl, "wb") as fh:
                    for chunk in dl.iter_content(65536):
                        fh.write(chunk)
                print(f"  {os.path.getsize(whl)//1024//1024}MB 已完成")

                r = subprocess.run(
                    [
                        PY,
                        "-X",
                        "utf8",
                        "-m",
                        "pip",
                        "install",
                        whl,
                        "--no-deps",
                        "--force-reinstall",
                    ],
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=60,
                )
                os.unlink(whl)
                print(f"  安装: {'成功' if r.returncode==0 else '失败'}")
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ) as e:
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            print(f"  方法3 失败: {e}")

# 最终验证
r = subprocess.run(
    [
        PY,
        "-X",
        "utf8",
        "-c",
        'import scipy.sparse; print("scipy.sparse OK"); '
        'from lightgbm import LGBMRegressor; print("LGBM OK"); '
        'import sklearn; print(f"sklearn {sklearn.__version__} OK")',
    ],
    capture_output=True,
    text=True,
    timeout=10,
)
if r.returncode == 0:
    print(f"\n{'='*60}")
    print("ALL DEPS OK - scipy + lightgbm + sklearn 就绪!")
    print(f"{'='*60}\n")
else:
    print(f"\n{'='*60}")
    print("DEPS STILL BROKEN - 请手动安装:")
    print("  cd e:\\各种PY程序\\28-终极量化交易系统8.4")
    print(
        "  qlib_env\\Scripts\\pip install scipy==1.10.1 --force-reinstall --no-cache-dir"
    )
    print(f"{'='*60}\n")
    sys.exit(1)

# ===================================================================
# STEP 2: 执行全量强制重训
# ===================================================================
print("=" * 60)
print("STEP 2: LGB Enhanced 全量强制重训 (26 个持仓标的)")
print("=" * 60)

os.chdir(BASE_DIR)
r = subprocess.run(
    [PY, "-X", "utf8", "lgb_enhanced_trainer.py", "--force-retrain"],
    env=env,
    timeout=3600,  # 最多1小时
)

print(f"\n训练完成, RC={r.returncode}")

# ===================================================================
# STEP 3: 验证
# ===================================================================
print("\n" + "=" * 60)
print("STEP 3: 验证模型")
print("=" * 60)

r = subprocess.run(
    [PY, "-X", "utf8", "15_每日工作流/run_auto_retrain.py", "--dry-run"],
    capture_output=False,
    env=env,
    timeout=120,
)

print("\n全部完成!")
