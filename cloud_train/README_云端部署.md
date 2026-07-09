# QLib 云端训练部署指南

## 前置准备

### 打包数据和代码

在本地运行 `pack_cloud.bat` 或手动打包:
- `qlib_data/cn_data/` → 压缩为 `qlib_data.zip` (~187MB)
- `cloud_train/modelscope_train.py` → 训练脚本
- `cloud_train/requirements.txt` → 依赖清单

---

## 方案一：魔搭社区 (ModelScope) — 免费

### 步骤

1. 注册 https://www.modelscope.cn
2. 创建 Notebook: CPU 4核16G (免费)
3. 在 Notebook Terminal 中:

```bash
# 安装依赖
pip install pyqlib lightgbm

# 上传 qlib_data.zip (通过 Notebook 界面上传或 git)
# 解压
unzip qlib_data.zip

# 训练
python modelscope_train.py --data-dir ./qlib_data/cn_data --market csi300

# 全市场训练 (需要更多内存)
python modelscope_train.py --data-dir ./qlib_data/cn_data --market all --boost-round 500
```

### 通过 Git 上传数据

```bash
# 本地: 将数据推送到 ModelScope Dataset
git clone https://www.modelscope.cn/datasets/你的用户名/qlib_data.git
cp -r qlib_data/cn_data/* qlib_data仓库/
cd qlib_data仓库
git add . && git commit -m "add qlib data" && git push

# 云端: 拉取数据
git clone https://www.modelscope.cn/datasets/你的用户名/qlib_data.git
```

---

## 方案二：AutoDL — 便宜高效

### 步骤

1. 注册 https://www.autodl.com
2. 租用实例: CPU 8核16G (~¥0.5/小时) 或 CPU 16核32G (~¥1.2/小时)
3. 选择镜像: PyTorch 2.0 + Python 3.10
4. 通过 JupyterLab 或 SSH 上传

```bash
# 安装依赖
pip install pyqlib lightgbm

# 上传数据 (通过 AutoDL 文件管理或 SCP)
# scp -P 端口号 qlib_data.zip root@region.autodl.com:/root/

# 解压
unzip qlib_data.zip -d /root/

# 训练 (csi300, ~2分钟)
python modelscope_train.py --data-dir /root/qlib_data/cn_data --market csi300

# 训练 (全市场, ~10分钟)
python modelscope_train.py --data-dir /root/qlib_data/cn_data --market all --boost-round 500

# 下载结果
# scp -P 端口号 root@region.autodl.com:/root/reports/ ./reports/
```

---

## 方案三：Kaggle — 免费 GPU/CPU

### 步骤

1. 注册 https://www.kaggle.com
2. 创建 Notebook (16GB RAM, 4 cores, 免费)
3. 上传 `qlib_data.zip` 作为 Dataset

```python
# 在 Kaggle Notebook 中
!pip install pyqlib lightgbm

import os
os.environ["MLFLOW_ALLOW_FILE_STORE"] = "true"

# 解压数据
!unzip /kaggle/input/qlib-data/qlib_data.zip -d /kaggle/working/

# 训练
!python /kaggle/input/qlib-scripts/modelscope_train.py \
    --data-dir /kaggle/working/qlib_data/cn_data \
    --market csi300
```

---

## 方案四：超算中心 (SLURM)

### 交互式提交

```bash
# 申请计算节点
salloc -p cpu -n 8 --mem=32G -t 2:00:00

# 加载 Python 模块
module load python/3.11

# 创建虚拟环境
python -m venv qlib_env
source qlib_env/bin/activate
pip install pyqlib lightgbm

# 训练
python modelscope_train.py --data-dir /scratch/你的用户名/qlib_data/cn_data --market all
```

### 批处理脚本 (submit.sbatch)

```bash
#!/bin/bash
#SBATCH --partition=cpu
#SBATCH --nodes=1
#SBATCH --ntasks=8
#SBATCH --mem=32G
#SBATCH --time=02:00:00
#SBATCH --output=qlib_train_%j.log

module load python/3.11
source qlib_env/bin/activate

python modelscope_train.py \
    --data-dir /scratch/$USER/qlib_data/cn_data \
    --market all \
    --boost-round 500 \
    --num-leaves 128
```

提交: `sbatch submit.sbatch`

---

## 方案对比

| 平台 | 内存 | 费用 | 适合场景 |
|------|------|------|----------|
| 魔搭社区 | 16GB | 免费 | csi300 训练 |
| Kaggle | 16GB | 免费 | csi300 训练 |
| AutoDL 8核 | 16GB | ¥0.5/h | csi300/全市场 |
| AutoDL 16核 | 32GB | ¥1.2/h | 全市场+大模型 |
| 超算中心 | 32G+ | 按节点计 | 大规模训练 |

---

## 训练命令速查

```bash
# 基础: csi300, 默认参数
python modelscope_train.py --data-dir ./qlib_data/cn_data

# 进阶: 全市场, 更大模型
python modelscope_train.py --market all --num-leaves 128 --boost-round 500

# 高级: 自定义时间段
python modelscope_train.py --start 2018-01-01 --end 2020-09-25 --train-end 2019-12-31
```
