---
type: project_topic
status: active
authoring_mode: ai_generated
created: 2026-08-17
updated: 2026-08-17
contains: modelarts, obs, swr, docker, auto-train, sdk-integration, log-retrieval
related:
  - cairn/model-training.md
  - cairn/architecture-map.md
  - docs/云端部署小白教程_Windows版_从零到第一个训练作业_20260815.md
  - docs/云端部署迁移MacBookPro_后续训练计划_20260816.md
---

# 华为云 ModelArts 云端训练部署

> 记录 ModelArts 自动训练的 SDK 集成、镜像管理、日志获取、定时调度与升级影响。对应 `ms_strategy/cloud_train/auto_train.py`、`cloud/docker/Dockerfile.qlib-trainer`、`ms_strategy/cloud_train/modelscope_train.py`。

## 一、当前部署状态（2026-08-17 升级后）

| 维度 | 值 |
|---|---|
| Docker 镜像 | `qt1/qt-qlib-trainer:v7`（python:3.11-slim + libgomp1 + chmod 777 /app） |
| SWR 组织 | `qt1`（华东-上海一 cn-east-3） |
| OBS 桶 | `qt-data`（训练数据）/ `qt-models`（模型输出+日志） |
| ModelArts 规格 | `modelarts.vm.cpu.8u`（8核32G CPU） |
| 训练脚本 | `ms_strategy/cloud_train/modelscope_train.py` |
| 自动训练 | `ms_strategy/cloud_train/auto_train.py`（SDK 提交+轮询+OBS下载+解析） |
| 定时调度 | 每周六0点（taskId: `d923e9dc`，cron `0 0 * * 6`） |
| 训练参数 | `--start 2015-01-01 --end 2026-07-08 --train-end 2024-12-31 --valid-end 2025-06-30 --num-leaves 128 --boost-round 500 --learning-rate 0.02 --max-depth 8` |
| instruments 修复 | 运行时 `cp -r /app/qlib_data/cn_data /tmp/qlib_data && sed -i 's/2020-09-25/2026-07-08/g'`（v7 镜像中 csi300.txt 只到 2020-09-25） |
| 训练结果 | 日均IC=0.0115, RankIC=0.0387, ICIR=0.0540（2025-07~2026-07 测试期） |
| 作业耗时 | ~2802秒（46分钟，含数据复制+OBS上传） |

## 二、SDK 集成关键发现（踩坑）

### 坑1：`Custom image query failure` (ModelArts.2810)

**根因**：SDK `create_training_job` 请求体与控制台不同。

| 字段 | 控制台（成功） | SDK（错误） | SDK（正确） |
|---|---|---|---|
| `image_url` | `qt1/qt-qlib-trainer:v7` | `swr.cn-east-3.myhuaweicloud.com/qt1/qt-qlib-trainer:v7` | `qt1/qt-qlib-trainer:v7` |
| `engine_id` | `""` (空字符串) | `a0fdbad7-...` (注册ID) | `""` |
| `pool_id` | `null` | `public-pool` | 不传 |

**排查方法**：用 `list_training_jobs` 对比控制台成功作业的 `algorithm.engine` 字段，发现 `engine_id` 为空、`image_url` 不带域名前缀。

**代码**（`auto_train.py:52-58`）：
```python
job = Job(
    kind="job",
    metadata=JobMetadata(name=name),
    algorithm=JobAlgorithm(
        command=CMD,
        engine=JobEngine(engine_id="", image_url=IMAGE),  # engine_id 空串, image_url 不带域名
    ),
    spec=Spec(
        resource=SpecResource(flavor_id=FLAVOR, node_count=1),  # 不传 pool_id
        log_export_path=LogExportPath(obs_url=f"obs://{OBS_BUCKET}/{OBS_PREFIX}"),
    ),
)
```

### 坑2：日志预览 API 不含用户 stdout

**现象**：`show_training_job_logs_preview` 返回的日志全是 ModelArts 基础设施日志（sidecar/init/bootstrap），看不到训练 print 输出。

**根因**：日志预览 API 只返回基础设施日志；用户 stdout 在单独的日志文件中，上传到 `log_export_path` 指定的 OBS 路径。

**解决**：
1. 作业 spec 设 `log_export_path=LogExportPath(obs_url="obs://qt-models/output/")`
2. 完成后从 OBS 下载 `output/modelarts-job-{job_id}-worker-0.log`
3. 用正则解析 IC/RankIC/ICIR

**代价**：设 `log_export_path` 后作业从 ~196秒 变为 ~755秒（OBS 上传开销）。凌晨定时任务可接受。

**代码**（`auto_train.py:85-95`）：
```python
def download_user_log(obs_client, job_id):
    key = f"{OBS_PREFIX}modelarts-job-{job_id}-worker-0.log"
    # ... OBS getObject → 正则解析
```

### 坑3：SDK 参数名与文档不一致

| SDK 方法 | 文档参数名 | 实际参数名 |
|---|---|---|
| `ShowTrainingJobDetailsRequest` | `job_id` | `training_job_id` |
| `ShowTrainingJobLogsPreviewRequest` | `job_id` | `training_job_id` |
| `ShowTrainingJobLogsPreviewRequest` | 无 | `task_id="worker-0"` (必传) |

**排查**：`inspect.signature(RequestClass.__init__)`

### 坑4：状态枚举大小写

SDK 返回的 `resp.status.phase` 是大写开头：`"Completed"` / `"Failed"` / `"Terminated"` / `"Running"` / `"Pending"`。不是小写。

## 三、Docker 镜像构建要点

### Dockerfile 关键行（`cloud/docker/Dockerfile.qlib-trainer`）

```dockerfile
FROM python:3.11-slim

# ma-user (UID 1000) — ModelArts 运行用户
RUN useradd -m -u 1000 ma-user && ...

# LightGBM 依赖
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1

WORKDIR /app
# ... COPY 代码和数据 ...

# 关键：给 /app 写权限，否则 ma-user 无法写 mlruns/reports
RUN chmod 777 /app
```

### 镜像推送（绕过 Docker Desktop 代理 bug）

Docker Desktop 内部代理（3128端口）处理大 layer 不稳定。用 `crane` 工具代替 `docker push`：

```powershell
docker save -o image.tar qt1/qt-qlib-trainer:v7
C:\Users\Administrator\Desktop\crane.exe push image.tar swr.cn-east-3.myhuaweicloud.com/qt1/qt-qlib-trainer:v7
```

### 镜像注册

在 ModelArts 控制台「自定义镜像」页面注册 SWR 镜像。同 tag 不重新拉取（缓存），每次更新需用新 tag（v2→v3→...→v7）。

## 四、auto_train.py 架构

```
auto_train.py
  ├─ get_ma_client()        # ModelArts SDK 客户端
  ├─ get_obs_client()       # OBS SDK 客户端
  ├─ submit_job()           # 提交训练作业 (image_url 不带域名, engine_id="", log_export_path)
  ├─ wait_job()             # 轮询状态 (30秒间隔, 3600秒超时)
  ├─ download_user_log()    # 从 OBS 下载 modelarts-job-{id}-worker-0.log
  ├─ extract_results()      # 正则解析 IC/RankIC/ICIR
  └─ save_report()          # 保存到 reports/cloud_auto_*.json
```

### 环境变量

| 变量 | 用途 |
|---|---|
| `HUAWEICLOUD_AK` | 华为云 AK |
| `HUAWEICLOUD_SK` | 华为云 SK |
| `HUAWEICLOUD_PROJECT_ID` | 项目 ID（`887314fcaff445bfa4d28b0a899ceb02`） |

### 依赖

```
huaweicloudsdkmodelarts  # ModelArts SDK (v1)
esdk-obs-python          # OBS SDK
```

## 五、升级计划对云端的影响

详见 `cairn/LOG.md` 2026-08-17 条目。摘要：

| 时间窗口 | 任务 | 调整内容 |
|---|---|---|
| 08-21~09-12 | Phase B3/B4 | `USE_AUTO_RETRAIN` / `USE_MLOPS_PIPELINE` 开关 |
| 11-01~12-31 | W.C.1 unsloth 微调 | ModelArts CPU→GPU（如 M5Max 不支持） |
| 11-13~12-31 | W7.4.7 MVSK P5-4 | OBS 上传沪深300数据 |
| 2027-01 | Docker v8.7 镜像体系 | 三层镜像(base/trainer/runtime), python 3.14 |

**结论**：当前 v7 镜像 + auto_train.py 短期不需要动。最近调整是 08-21 Phase B3 配置开关。

## 六、MacBook Pro 迁移差异（如执行）

| 维度 | Windows 当前 | Mac 迁移后 |
|---|---|---|
| 定时频率 | 每天凌晨2点 | 每周六18点（周度） |
| 调度方式 | Windows 任务计划 | macOS launchd |
| 镜像 tag | 固定 v7 | latest + 日期 tag |
| 构建命令 | `docker build` | `docker build --platform linux/amd64`（需 Rosetta 2） |
| SWR 组织 | `qt1` | 文档写 `qt`（需确认） |

## 七、contains 标签索引

- `modelarts` — ModelArts SDK 集成、作业提交、状态轮询
- `obs` — OBS 对象存储、日志下载
- `swr` — SWR 镜像仓库、crane 推送
- `docker` — Dockerfile、镜像构建
- `auto-train` — auto_train.py 自动训练流程
- `sdk-integration` — SDK 请求体差异、参数名差异
- `log-retrieval` — 日志获取方案（log_export_path + OBS）

<!-- AUTO-GENERATED: 相关文档 -->
## 相关文档

- [生产上云部署 Runbook (云 Linux + Win 实盘机 混合架构)](cloud-production-deployment.md) (相似度 27%)
- [GitHub 周热门项目集成 Wave10 (2026-08-21)](github-integration-wave10-20260821.md) (相似度 11%)
- [Shadow 30 天验证 (W7.2.8 + W7.2.9)](shadow-30day-validation.md) (相似度 7%)
- [MVSK 高阶矩组合优化（YAND 启发）](mvsk-higher-moment-optimization.md) (相似度 6%)
- [qlib 选股模型回测验证与 V9 对比](qlib-backtest-validation.md) (相似度 6%)

<!-- 由 scripts/cairn_cross_ref.py 自动生成，请勿手动编辑此区块 -->
