# 云端训练方案 — 华为云 ModelArts 专项

| 项 | 值 |
|---|---|
| 文档类型 | 技术方案(云端训练专项) |
| 版本 | v1.0 |
| 日期 | 2026-08-15 |
| 适用项目 | 终极量化交易系统 v8.6.15 |
| 云服务商 | 华为云 ModelArts + OBS + CCE |
| 本地设备 | MacBook Pro M5 Max 顶配 |
| 上游手册 | `云部署协同本地量化交易实施手册_M5Max_20260815.md`(P4 扩展) |
| 已有基础 | `ms_strategy/cloud_train/modelscope_train.py` + `README_云端部署.md` |
| 状态 | 草案待评审 |

> **本方案基于项目已有的 `ms_strategy/cloud_train/` 云训练代码扩展到华为云 ModelArts。** 项目已具备 ModelScope/Kaggle/AutoDL/超算中心 4 平台训练能力,本方案新增华为云 ModelArts 适配,并统一所有训练入口的云端化。

---

## 1. 训练入口盘点与云端化判定

基于代码探索,项目有 10 个训练相关入口,云端化判定如下:

| # | 入口 | 类型 | 本地 M5 Max | 云端化判定 | 云端收益 |
|---|---|---|---|---|---|
| 1 | `lgb_enhanced_trainer.py` | LightGBM 增强(主力) | ✅ 5–15 分钟 | **可选弹性峰值** | 大规模参数搜索时上云 |
| 2 | `lgb_tscv_trainer.py` | LightGBM + TSCV | ✅ 20–40 分钟 | **可选弹性峰值** | Purged K-Fold 并行 |
| 3 | `run_auto_retrain.py` | 自动重训编排 | ✅ | **本地为主** | 编排留本地,训练可弹云 |
| 4 | `train_qlib_models.py` | Qlib 批量训练 | ✅ | **可选** | 批量标的并行 |
| 5 | `qlib_improved_train.py` | Qlib Alpha158 改进 | ⚠️ 全市场慢 | **上云** | 全市场 Alpha158 计算量大 |
| 6 | `feature_importance_analysis.py` | 特征重要性 | ✅ | **本地** | 一次性,本地够 |
| 7 | `qlib_train_test.py` | Qlib 横截面测试 | ✅ | **本地** | 测试用 |
| 8 | **`modelscope_train.py`** | **已有云训练** | — | **上云(已有基础)** | 直接适配 ModelArts |
| 9 | `qlora.py`(AirLLM) | LLM QLoRA 微调 | ❌ 跑不动 | **上云 GPU** | 与量化无关,独立 |
| 10 | `gat_factor_torch.py` | GAT 图注意力 | ⚠️ 中小图可 | **上云(大图)** | 研究资产,大图上云 |

**云端训练优先级:**
1. **P1 必上云**:#8 `modelscope_train.py`(已有基础,直接适配)、#5 `qlib_improved_train.py`(全市场 Alpha158)
2. **P2 按需上云**:#1 `lgb_enhanced_trainer.py`、#2 `lgb_tscv_trainer.py`(参数搜索/大规模时)
3. **P3 独立上云**:#9 `qlora.py`(LLM,独立 GPU 作业)
4. **保留本地**:#3 #6 #7 #10(编排/一次性/测试/研究)

---

## 2. 华为云 ModelArts 训练架构

### 2.1 总体架构

```
┌─────────────────────────────────────────────────────┐
│              华为云 ModelArts                         │
│                                                       │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  │
│  │ CPU 训练池   │  │ GPU V100 池 │  │ GPU A100 池 │  │
│  │ (LightGBM/  │  │ (Qlib torch │  │ (AirLLM/    │  │
│  │  Qlib LGB)  │  │  /GAT)      │  │  QLoRA)     │  │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  │
│         └────────────────┴────────────────┘          │
│                        │                              │
│              ┌─────────┴─────────┐                   │
│              │  OBS(数据/模型)    │                   │
│              │  qt-data(输入)    │                   │
│              │  qt-models(输出)  │                   │
│              └─────────┬─────────┘                   │
│                        │                              │
│              ┌─────────┴─────────┐                   │
│              │ SWR(镜像仓库)     │                   │
│              │ qt-lgb-trainer    │                   │
│              │ qt-qlib-trainer   │                   │
│              │ qt-airllm         │                   │
│              └───────────────────┘                   │
└────────────────────────┬────────────────────────────┘
                         │ 模型回传
┌────────────────────────┴────────────────────────────┐
│              MacBook Pro M5 Max(本地)                │
│  • 触发训练(qt.cloud_task → cloud-runner)           │
│  • 接收模型(OBS → ~/QuantData/models/)             │
│  • 本地日常训练(LightGBM 5-15min)                   │
└──────────────────────────────────────────────────────┘
```

### 2.2 资源池规划

| 资源池 | 规格 | 用途 | 计费 | 启停策略 |
|---|---|---|---|---|
| CPU 训练池 | 8 核 32G(通用计算增强型) | LightGBM、Qlib LGB | 按需 | 训练作业自动申请,完即释放 |
| GPU V100 池 | V100(8 核 32G + 1×V100) | Qlib torch、GAT | 按需 | 同上 |
| GPU A100 池 | A100(12 核 64G + 1×A100) | AirLLM QLoRA | 按需 | 同上 |

> ModelArts 训练作业为**按需申请**模式:提交作业时自动分配资源,作业完成自动释放,**无需常驻集群**,成本最优。

---

## 3. 镜像构建

### 3.1 镜像清单

| 镜像 | 基础 | 包含模块 | 架构 | 用途 |
|---|---|---|---|---|
| `qt-lgb-trainer` | python:3.11-slim + lightgbm | `lgb_trainer/`、`lgb_enhanced_trainer.py`、`lgb_tscv_trainer.py`、`autolearn_trainer.py` | amd64 | LightGBM 训练 |
| `qt-qlib-trainer` | python:3.11-slim + pyqlib + lightgbm | `ms_strategy/training/`、`ms_strategy/cloud_train/modelscope_train.py`、`qlib/` | amd64 | Qlib 训练(含已有云训练代码) |
| `qt-airllm` | python:3.11 + torch + transformers + peft | `external/airllm_src/` | amd64 | AirLLM QLoRA(独立) |
| `qt-gat-trainer` | python:3.11 + torch | `utils/alpha_factor/gat_factor_torch.py` | amd64 | GAT 研究(可选) |

### 3.2 Dockerfile 示例(qt-qlib-trainer)

`cloud/docker/Dockerfile.qlib-trainer`:

    FROM python:3.11-slim
    RUN pip install uv
    WORKDIR /app

    # 装依赖(用 ms_strategy/cloud_train/requirements.txt + 主项目)
    COPY pyproject.toml uv.lock ./
    RUN uv sync --frozen --no-dev
    COPY ms_strategy/cloud_train/requirements.txt ./
    RUN pip install -r requirements.txt

    # 拷贝训练模块
    COPY ms_strategy/training/ ./ms_strategy/training/
    COPY ms_strategy/cloud_train/ ./ms_strategy/cloud_train/
    COPY qlib/ ./qlib/
    COPY utils/ ./utils/
    COPY config/ ./config/
    COPY configs/ ./configs/

    ENV QT_PLATFORM=cloud
    ENV QLIB_DATA_DIR=/cache/data/qlib_data/cn_data

    # 入口由 ModelArts 启动命令指定
    CMD ["uv", "run", "python", "ms_strategy/cloud_train/modelscope_train.py"]

构建推送:

    docker build --platform linux/amd64 -t swr.cn-east-3.myhuaweicloud.com/qt/qt-qlib-trainer:latest \
      -f cloud/docker/Dockerfile.qlib-trainer .
    docker push swr.cn-east-3.myhuaweicloud.com/qt/qt-qlib-trainer:latest

---

## 4. 数据准备(OBS)

### 4.1 数据分层

| OBS 路径 | 内容 | 来源 | 大小 |
|---|---|---|---|
| `obs://qt-data/qlib_data/cn_data/` | Qlib 全 A 股 bin 格式 | 本地 `qlib_data/cn_data/` | 270MB |
| `obs://qt-data/data_cache/` | parquet 行情片段 | 本地 `data_cache/` | 40MB |
| `obs://qt-data/config/` | 训练配置(positions.json 等) | 本地 `config/`、`configs/` | <10MB |
| `obs://qt-models/lgb_enhanced/` | LightGBM 增强模型输出 | 训练作业输出 | 增量 |
| `obs://qt-models/qlib/` | Qlib 模型输出 | 训练作业输出 | 增量 |
| `obs://qt-models/airllm/` | AirLLM 模型输出 | 训练作业输出 | 增量 |

### 4.2 上传命令(Mac 执行)

    # 配置 obsutil
    obsutil config -ak=xxx -sk=xxx -endpoint=obs.cn-east-3.myhuaweicloud.com

    # 上传训练数据(一次性,730MB)
    obsutil cp -r -f ~/QuantData/qlib_data/  obs://qt-data/qlib_data/
    obsutil cp -r -f ~/QuantData/data_cache/ obs://qt-data/data_cache/
    obsutil cp -r -f ~/28-终极量化交易系统8.4/config/  obs://qt-data/config/
    obsutil cp -r -f ~/28-终极量化交易系统8.4/configs/ obs://qt-data/configs/

### 4.3 数据更新(增量)

盘后采集的新数据,每日增量上传:

    # Mac launchd 每日 16:00 触发
    obsutil sync ~/QuantData/data_cache/ obs://qt-data/data_cache/ -f -u

---

## 5. 训练作业配置(逐入口)

### 5.1 入口 #8:`modelscope_train.py`(已有云训练,直接适配)

**这是项目已有的云训练代码,已有完整 CLI 参数,直接用于 ModelArts。**

**ModelArts 训练作业配置:**

| 项 | 值 |
|---|---|
| 算法来源 | 自定义镜像 `swr.../qt/qt-qlib-trainer:latest` |
| 资源池 | CPU 训练池(8 核 32G) |
| 数据来源 | OBS `qt-data/` → 挂载 `/cache/data` |
| 输出 | OBS `qt-models/qlib/` ← 挂载 `/cache/output` |
| 启动命令 | 见下 |

**启动命令(csi300 全市场 LightGBM):**

    uv run python ms_strategy/cloud_train/modelscope_train.py \
      --data-dir /cache/data/qlib_data/cn_data \
      --market csi300 \
      --start 2015-01-01 --end 2026-08-14 \
      --train-end 2024-12-31 --valid-end 2025-06-30 \
      --model lightgbm \
      --num-leaves 128 --boost-round 500 --learning-rate 0.02 --max-depth 8 \
      --output /cache/output

**启动命令(csi500 扩展池):**

    uv run python ms_strategy/cloud_train/modelscope_train.py \
      --data-dir /cache/data/qlib_data/cn_data \
      --market csi500 \
      --start 2015-01-01 --end 2026-08-14 \
      --train-end 2024-12-31 --valid-end 2025-06-30 \
      --model lightgbm --num-leaves 64 --boost-round 200 \
      --output /cache/output

**保存为作业模板** `cloud/modelarts/modelscope_csi300.json`,便于重复提交。

### 5.2 入口 #5:`qlib_improved_train.py`(全市场 Alpha158)

**特点:** 硬编码配置(无 CLI 参数),用 Alpha158 特征,数据 `qlib_data/cn_data/`,日期 2015–2026。

**适配方式:** 该脚本硬编码路径 `QLIB_DATA_DIR`(第 30-33 行,读环境变量或默认项目路径),云端用环境变量覆盖:

**ModelArts 启动命令:**

    # 环境变量覆盖数据路径
    export QLIB_DATA_DIR=/cache/data/qlib_data/cn_data
    uv run python ms_strategy/training/qlib_improved_train.py

**注意:** 该脚本输出报告到 `reports/qlib_improved_train_*.json`,需把 `reports/` 软链到 `/cache/output/`:

    ln -s /cache/output reports
    export QLIB_DATA_DIR=/cache/data/qlib_data/cn_data
    uv run python ms_strategy/training/qlib_improved_train.py

**资源池:** CPU 训练池(8 核 32G),Alpha158 特征工程 CPU 密集,预计 30–60 分钟。

### 5.3 入口 #1:`lgb_enhanced_trainer.py`(LightGBM 增强,按需上云)

**特点:** 主力训练器,本地 M5 Max 5–15 分钟即够,**仅大规模参数搜索时上云**。

**本地优先,云端用于参数网格搜索:**

**ModelArts 启动命令(单标的):**

    # 数据需从 OBS 挂载,输出回 OBS
    export QUANT_DATA_ROOT=/cache/data
    ln -s /cache/output models
    uv run python lgb_enhanced_trainer.py --symbols 688041 --force-retrain

**参数网格搜索(多作业并行):**

对每个参数组合提交独立 ModelArts 作业,并行搜索:

    # Mac 上循环提交(或用 ModelArts 的超参搜索功能)
    for leaves in 64 128 256; do
      for lr in 0.005 0.01 0.02; do
        # 修改 LGB_ENHANCED_CONFIG 或用环境变量传入
        hcloud ModelArtsCreateTrainingJob --image=qt-lgb-trainer \
          --command="uv run python lgb_enhanced_trainer.py --force-retrain" \
          --env="LGB_NUM_LEAVES=$leaves LGB_LEARNING_RATE=$lr" \
          --data=obs://qt-data/ --output=obs://qt-models/lgb_enhanced/
      done
    done

> **推荐:** 用 ModelArts 自带的**自动超参搜索(AutoSearch)** 功能,而非手动循环,更高效。

### 5.4 入口 #2:`lgb_tscv_trainer.py`(LightGBM + TSCV,按需上云)

**同 #1,本地优先,大规模 Purged K-Fold 并行时上云。**

**ModelArts 启动命令:**

    export QUANT_DATA_ROOT=/cache/data
    ln -s /cache/output models
    uv run python lgb_tscv_trainer.py --force-retrain

### 5.5 入口 #9:`qlora.py`(AirLLM QLoRA,独立 GPU 作业)

**特点:** LLM 微调,与量化无关,需 A100 GPU,独立作业。

**ModelArts 启动命令:**

    uv run python external/airllm_src/training/qlora.py \
      --dataset="chinese-vicuna" \
      --learning_rate 0.0001 \
      --per_device_train_batch_size 1 \
      --gradient_accumulation_steps 16 \
      --max_steps 10000 \
      --model_name_or_path "timdettmers/guanaco-33b-merged" \
      --source_max_len 512 --target_max_len 512 \
      --output_dir /cache/output/airllm

**资源池:** GPU A100 池,预计数小时–数天(按 max_steps)。

### 5.6 入口 #10:`gat_factor_torch.py`(GAT,研究,可选)

**特点:** 研究资产,不在生产路径,无 main 入口(API 调用)。

**如需云端跑大图:** 写一个训练脚本 `cloud/scripts/train_gat.py` 调用 `GATFactorTorch.train()`:

    from utils.alpha_factor.gat_factor_torch import GATFactorTorch
    import numpy as np
    features = np.load("/cache/data/gat_features.npy")
    adj = np.load("/cache/data/gat_adj.npy")
    labels = np.load("/cache/data/gat_labels.npy")
    gat = GATFactorTorch(n_hidden=16, n_heads=4, device="cuda")
    gat.train(features, adj, labels, epochs=300)
    gat.save("/cache/output/gat_model.pt")

**资源池:** GPU V100 池。

---

## 6. 训练触发与编排

### 6.1 触发方式

| 触发 | 方式 | 适用 |
|---|---|---|
| **手动** | Mac CLI / ModelArts 控制台 | 临时训练、调试 |
| **定时** | Mac launchd → `qt.cloud_task` → cloud-runner → ModelArts | 每日/每月重训 |
| **事件** | DriftMonitor 检测概念漂移 → 触发 | 模型失效自动重训 |
| **超参搜索** | ModelArts AutoSearch | 参数优化 |

### 6.2 cloud-runner(云端任务执行器)

部署在 CCE 常驻,消费 `qt.cloud_task` Topic,触发 ModelArts 作业:

`cloud/runner/cloud_runner.py`:

    import json, os
    from confluent_kafka import Consumer, Producer
    from huaweicloudsdkcore.auth.credentials import BasicCredentials
    from huaweicloudsdkmodelarts.v2 import ModelArtsClient, CreateTrainingJobRequest

    class CloudRunner:
        def __init__(self):
            self.consumer = Consumer({...})
            self.consumer.subscribe(['qt.cloud_task'])
            self.producer = Producer({...})
            self.ma = ModelArtsClient.new_builder() \
                .with_credentials(BasicCredentials(ak=os.environ['OBS_AK'], sk=os.environ['OBS_SK'])) \
                .build()

        def handle_task(self, msg):
            task = json.loads(msg.value())
            job = self._submit_modelarts(task)
            self.producer.produce('qt.cloud_result', json.dumps({
                'task_id': task['id'], 'job_id': job.id, 'status': 'submitted'
            }))

        def _submit_modelarts(self, task):
            """根据 task['type'] 提交对应 ModelArts 训练作业"""
            templates = {
                'modelscope_csi300': 'cloud/modelarts/modelscope_csi300.json',
                'qlib_improved':     'cloud/modelarts/qlib_improved.json',
                'lgb_enhanced':      'cloud/modelarts/lgb_enhanced.json',
                'airllm_qlora':      'cloud/modelarts/airllm_qlora.json',
            }
            template = templates.get(task['type'])
            if not template:
                raise ValueError(f"未知任务类型: {task['type']}")
            # 读取模板,合并 task['params'],提交作业
            config = self._load_template(template)
            config = self._merge_params(config, task.get('params', {}))
            return self.ma.create_training_job(CreateTrainingJobRequest(body=config))

        def run(self):
            while True:
                msg = self.consumer.poll(1.0)
                if msg:
                    self.handle_task(msg)

### 6.3 Mac 端触发(Mac Agent)

`local_cloud_agent_mac.py` 新增方法:

    def trigger_cloud_train(self, task_type, params=None):
        """触发云端训练作业"""
        self.producer.produce('qt.cloud_task', json.dumps({
            'id': uuid4().hex,
            'type': task_type,           # 'modelscope_csi300' / 'qlib_improved' / ...
            'params': params or {},
            'triggered_at': datetime.now().isoformat(),
        }))

**调用示例:**

    # 手动触发 Qlib csi300 训练
    agent.trigger_cloud_train('modelscope_csi300', {
        'market': 'csi300', 'train_end': '2024-12-31', 'valid_end': '2025-06-30'
    })

    # 触发 AirLLM QLoRA
    agent.trigger_cloud_train('airllm_qlora', {'max_steps': 5000})

### 6.4 定时训练(launchd)

`mac/launchd/com.qt.cloud-retrain-qlib.plist`(每月 1 日 06:00):

    <?xml version="1.0" encoding="UTF-8"?>
    <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "...">
    <plist version="1.0"><dict>
      <key>Label</key><string>com.qt.cloud-retrain-qlib</string>
      <key>ProgramArguments</key><array>
        <string>/Users/yuppiefree2025/.local/bin/uv</string>
        <string>run</string><string>python</string>
        <string>-c</string>
        <string>from local_cloud_agent_mac import MacCloudAgent; a=MacCloudAgent(); a.trigger_cloud_train('modelscope_csi300')</string>
      </array>
      <key>StartCalendarInterval</key><dict>
        <key>Day</key><integer>1</integer><key>Hour</key><integer>6</integer>
      </dict>
    </dict></plist>

---

## 7. 模型回传与热重载

### 7.1 模型回传流程

```
ModelArts 作业完成
    ↓ 写模型到 /cache/output/
    ↓ 自动同步到 OBS qt-models/
    ↓ cloud-runner 监听作业完成事件
    ↓ produce qt.cloud_result {job_id, model_path: obs://qt-models/...}
    ↓ Mac Agent 消费 qt.cloud_result
    ↓ obsutil cp obs://qt-models/xxx ~/QuantData/models/xxx
    ↓ Mac 策略热重载新模型
```

### 7.2 Mac 端模型回传实现

`local_cloud_agent_mac.py`:

    def on_cloud_result(self, msg):
        result = json.loads(msg.value())
        if result['status'] != 'completed':
            return
        # 从 OBS 拉模型
        model_path = result['model_path']  # obs://qt-models/qlib/xxx.pkl
        local_path = model_path.replace('obs://qt-models/', '~/QuantData/models/')
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        subprocess.run(['obsutil', 'cp', model_path, local_path])
        # 通知策略热重载
        self.strategy_hot_reload(local_path)
        logger.info(f"模型已回传并热重载: {local_path}")

### 7.3 策略热重载

策略进程监听模型目录变化,自动加载新模型:

    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler

    class ModelReloadHandler(FileSystemEventHandler):
        def on_modified(self, event):
            if event.src_path.endswith('.pkl'):
                self.strategy.load_model(event.src_path)

    observer = Observer()
    observer.schedule(ModelReloadHandler(), path='~/QuantData/models/', recursive=True)
    observer.start()

---

## 8. 训练作业模板(JSON)

### 8.1 `cloud/modelarts/modelscope_csi300.json`

    {
      "job_name": "qt-modelscope-csi300",
      "algorithm": {
        "image": "swr.cn-east-3.myhuaweicloud.com/qt/qt-qlib-trainer:latest",
        "command": "uv run python ms_strategy/cloud_train/modelscope_train.py --data-dir /cache/data/qlib_data/cn_data --market csi300 --start 2015-01-01 --end 2026-08-14 --train-end 2024-12-31 --valid-end 2025-06-30 --model lightgbm --num-leaves 128 --boost-round 500 --learning-rate 0.02 --max-depth 8 --output /cache/output"
      },
      "resource": {
        "pool_name": "cpu-train-pool",
        "flavor": "modelarts.bm.8c32g.cpu",
        "count": 1
      },
      "inputs": {
        "data": {"obs": "obs://qt-data/", "local": "/cache/data"}
      },
      "outputs": {
        "models": {"obs": "obs://qt-models/qlib/", "local": "/cache/output"}
      },
      "timeout": 3600
    }

### 8.2 `cloud/modelarts/qlib_improved.json`

    {
      "job_name": "qt-qlib-improved",
      "algorithm": {
        "image": "swr.cn-east-3.myhuaweicloud.com/qt/qt-qlib-trainer:latest",
        "command": "ln -s /cache/output reports && export QLIB_DATA_DIR=/cache/data/qlib_data/cn_data && uv run python ms_strategy/training/qlib_improved_train.py"
      },
      "resource": {"pool_name": "cpu-train-pool", "flavor": "modelarts.bm.8c32g.cpu", "count": 1},
      "inputs": {"data": {"obs": "obs://qt-data/", "local": "/cache/data"}},
      "outputs": {"models": {"obs": "obs://qt-models/qlib/", "local": "/cache/output"}},
      "timeout": 7200
    }

### 8.3 `cloud/modelarts/airllm_qlora.json`

    {
      "job_name": "qt-airllm-qlora",
      "algorithm": {
        "image": "swr.cn-east-3.myhuaweicloud.com/qt/qt-airllm:latest",
        "command": "uv run python external/airllm_src/training/qlora.py --dataset chinese-vicuna --learning_rate 0.0001 --per_device_train_batch_size 1 --gradient_accumulation_steps 16 --max_steps 10000 --model_name_or_path timdettmers/guanaco-33b-merged --source_max_len 512 --target_max_len 512 --output_dir /cache/output/airllm"
      },
      "resource": {"pool_name": "gpu-a100-pool", "flavor": "modelarts.bm.12c64g.a100", "count": 1},
      "inputs": {"data": {"obs": "obs://qt-data/", "local": "/cache/data"}},
      "outputs": {"models": {"obs": "obs://qt-models/airllm/", "local": "/cache/output"}},
      "timeout": 86400
    }

---

## 9. 成本估算

| 作业 | 资源 | 频率 | 单次耗时 | 月成本 |
|---|---|---|---|---|
| modelscope_csi300 | CPU 8核32G | 每月 1 次 | 30–60 分钟 | ¥10 |
| qlib_improved | CPU 8核32G | 每月 1 次 | 60–120 分钟 | ¥15 |
| lgb_enhanced 参数搜索 | CPU 8核32G × 8 并行 | 每季 1 次 | 20 分钟 × 8 | ¥20(季度摊) |
| airllm_qlora | A100 | 按需 | 数小时–数天 | ¥200–600(按需) |
| OBS 存储 | 50GB | 常驻 | — | ¥15 |
| **合计(不含 AirLLM)** | | | | **¥40/月** |
| **合计(含 AirLLM 月 1 次)** | | | | **¥240–640/月** |

> **LightGBM/Qlib 训练成本极低(¥40/月)**,因本地 M5 Max 承担主力,云仅做全市场/参数搜索。AirLLM 是大头(若用)。

---

## 10. 已有云训练代码利用(`ms_strategy/cloud_train/`)

### 10.1 现状

项目已有 `ms_strategy/cloud_train/modelscope_train.py`(196 行)和 `README_云端部署.md`(177 行),已支持:

| 平台 | 状态 | 本方案关系 |
|---|---|---|
| 魔搭社区(ModelScope) | ✅ 已支持 | 并行可选,免费额度 |
| Kaggle | ✅ 已支持 | 并行可选,免费额度 |
| AutoDL | ✅ 已支持 | 并行可选,¥0.5/h |
| 超算中心(SLURM) | ✅ 已支持 | 大规模可选 |
| **华为云 ModelArts** | ❌ 未支持 | **本方案新增** |

### 10.2 复用策略

`modelscope_train.py` 的 CLI 参数设计已足够通用(`--data-dir --market --start --end --train-end --valid-end --model ...`),**无需修改代码**,只需:

1. 构建含该脚本的 Docker 镜像(`qt-qlib-trainer`)
2. ModelArts 作业用 OBS 挂载数据 + 该脚本命令
3. `README_云端部署.md` 追加华为云 ModelArts 章节

### 10.3 `README_云端部署.md` 追加内容

    ## 华为云 ModelArts(推荐,与 CodeArts 同生态)

    | 规格 | 费用 | 适合 |
    |---|---|---|
    | CPU 8核32G | ¥0.5/h | csi300/csi500 LightGBM |
    | GPU V100 | ¥3/h | Qlib torch/GAT |
    | GPU A100 | ¥10/h | AirLLM QLoRA |

    部署:
    1. 构建镜像 qt-qlib-trainer(见 cloud/docker/Dockerfile.qlib-trainer)
    2. OBS 上传 qlib_data/(obsutil cp -r qlib_data/ obs://qt-data/qlib_data/)
    3. 提交训练作业(见 cloud/modelarts/modelscope_csi300.json)
    4. 模型回传(obsutil cp obs://qt-models/ ~/QuantData/models/)

---

## 11. 验证与监控

### 11.1 训练作业验证

| 验证项 | 方法 |
|---|---|
| 作业提交成功 | ModelArts 控制台 / `hcloud ModelArtsShowTrainingJob` |
| 数据挂载正确 | 作业日志检查 `/cache/data/qlib_data/cn_data/` 存在 |
| 训练正常 | 作业日志无报错,IC/Rank IC 输出 |
| 模型产出 | OBS `qt-models/` 有新 `.pkl` + `.json` |
| 模型回传 | Mac `~/QuantData/models/` 有新模型 |
| 热重载 | 策略进程日志显示加载新模型 |

### 11.2 监控告警

| 指标 | 告警条件 | 方式 |
|---|---|---|
| 作业失败 | status=failed | AOM + SMN 邮件 |
| 作业超时 | 运行 > timeout | AOM |
| 训练 IC 退化 | IC < 阈值 | 作业输出解析 + 告警 |
| OBS 容量 | > 100GB | OBS 告警 |
| 成本 | 月超 ¥500 | 预算告警 |

---

## 12. 实施步骤(对应实施手册 P4 扩展)

| 步骤 | 对应手册 | 内容 | 耗时 |
|---|---|---|---|
| 1 | D31 | OBS 数据上传 | 2h |
| 2 | D26–D29 | 构建 4 个训练镜像推 SWR | 8h |
| 3 | D32 | modelscope_csi300 作业模板 + 试跑 | 4h |
| 4 | D33 | qlib_improved 作业配置 + 跑通 | 6h |
| 5 | D34 | lgb_enhanced 参数搜索作业 | 4h |
| 6 | D35 | airllm_qlora 作业(可选) | 8h |
| 7 | D36 | cloud-runner 部署 CCE | 8h |
| 8 | D37 | Mac 触发云端训练联调 | 4h |
| 9 | D38 | 模型回传 + 热重载 | 6h |
| 10 | D39 | 定时训练 launchd | 2h |
| 11 | — | `README_云端部署.md` 追加华为云章节 | 1h |
| 12 | D40 | P4 验收 | 4h |

**合计约 57 小时 ≈ 7 工作日**(含等待训练完成)。

---

## 13. 风险与对策

| 风险 | 对策 |
|---|---|
| ModelArts 作业排队等待 | 用按需池;非高峰提交;急用可切专属池 |
| OBS 数据与本地不一致 | 每日增量同步;训练前校验数据日期 |
| 镜像依赖缺失 | Dockerfile 用 `uv sync --frozen`;CI 流水线验证 |
| 训练 IC 退化 | 作业输出解析 IC,低于阈值告警 + 自动重训 |
| 模型回传延迟 | OBS SDK 并行下载;大模型用 OBS 分段 |
| AirLLM 成本失控 | 严格按需;max_steps 限制;预算告警 |
| `qlib_improved_train.py` 硬编码路径 | 用环境变量 `QLIB_DATA_DIR` 覆盖(已支持) |
| `feature_importance_analysis.py` 指向 7.1 版数据 | 本地跑,不上云(一次性) |

---

## 14. 附录:命令速查

### Mac 触发云端训练

    # 手动触发
    uv run python -c "from local_cloud_agent_mac import MacCloudAgent; MacCloudAgent().trigger_cloud_train('modelscope_csi300')"

    # 查看作业状态
    hcloud ModelArtsShowTrainingJob --job_id=xxx

### ModelArts 提交作业(CLI)

    hcloud ModelArtsCreateTrainingJob --body=cloud/modelarts/modelscope_csi300.json

### OBS 模型回传

    obsutil cp obs://qt-models/qlib/latest.pkl ~/QuantData/models/qlib/

### 镜像构建

    docker build --platform linux/amd64 -t swr.cn-east-3.myhuaweicloud.com/qt/qt-qlib-trainer:latest \
      -f cloud/docker/Dockerfile.qlib-trainer .
    docker push swr.cn-east-3.myhuaweicloud.com/qt/qt-qlib-trainer:latest

---

**文档结束。本方案基于项目已有 `ms_strategy/cloud_train/` 云训练代码扩展,核心是构建 4 个镜像 + 配置 ModelArts 作业模板 + cloud-runner 编排 + 模型回传热重载。LightGBM/Qlib 训练月成本约 ¥40,AirLLM 按需另计。**
