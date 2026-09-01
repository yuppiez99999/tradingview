"""
ModelArts 自动训练 + 结果保存脚本
每天凌晨由 Windows 任务计划调用

流程:
  1. 提交训练作业到 ModelArts (输出路径设为 obs://qt-models/output/)
  2. 轮询作业状态直到完成
  3. 从 OBS 下载用户日志 (含训练 stdout)
  4. 解析 IC/RankIC/ICIR 指标
  5. 保存到本地 reports/ 目录
"""
import datetime
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

from huaweicloudsdkcore.auth.credentials import BasicCredentials
from huaweicloudsdkmodelarts.v1.model.create_training_job_request import CreateTrainingJobRequest
from huaweicloudsdkmodelarts.v1.model.job import Job
from huaweicloudsdkmodelarts.v1.model.job_algorithm import JobAlgorithm
from huaweicloudsdkmodelarts.v1.model.job_engine import JobEngine
from huaweicloudsdkmodelarts.v1.model.job_metadata import JobMetadata
from huaweicloudsdkmodelarts.v1.model.log_export_path import LogExportPath
from huaweicloudsdkmodelarts.v1.model.show_training_job_details_request import ShowTrainingJobDetailsRequest
from huaweicloudsdkmodelarts.v1.model.spec import Spec
from huaweicloudsdkmodelarts.v1.model.spec_resource import SpecResource
from huaweicloudsdkmodelarts.v1.modelarts_client import ModelArtsClient
from huaweicloudsdkmodelarts.v1.region.modelarts_region import ModelArtsRegion
from obs import ObsClient

AK = os.environ.get("HUAWEICLOUD_AK", "")
SK = os.environ.get("HUAWEICLOUD_SK", "")
IMAGE = "qt1/qt-qlib-trainer:v7"
CODE_DIR = "obs://qt-data/code/ms_strategy/cloud_train/"
WORK_SCRIPT = "/home/ma-user/modelarts/user-job-dir/cloud_train/modelscope_train.py"
CMD = (
    'bash -c "'
    "cp -r /app/qlib_data/cn_data /tmp/qlib_data"
    " && sed -i 's/2020-09-25/2026-07-08/g' /tmp/qlib_data/instruments/csi300.txt"
    f" && python {WORK_SCRIPT}"
    " --data-dir /tmp/qlib_data"
    " --market csi300"
    " --start 2015-01-01 --end 2026-07-08"
    " --train-end 2024-12-31 --valid-end 2025-06-30"
    " --num-leaves 128 --boost-round 500 --learning-rate 0.02 --max-depth 8"
    '"'
)
FLAVOR = "modelarts.vm.cpu.8u"
OBS_BUCKET = "qt-models"
OBS_PREFIX = "output/"
REPORT_DIR = Path(r"E:\各种PY程序\28-终极量化交易系统8.4\reports")


def get_ma_client():
    creds = BasicCredentials(ak=AK, sk=SK)
    return ModelArtsClient.new_builder().with_credentials(creds).with_region(ModelArtsRegion.CN_EAST_3).build()


def get_obs_client():
    return ObsClient(access_key_id=AK, secret_access_key=SK, server="https://obs.cn-east-3.myhuaweicloud.com")


def submit_job(client):
    now = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    name = f"qt-auto-{now}"
    job = Job(
        kind="job",
        metadata=JobMetadata(name=name),
        algorithm=JobAlgorithm(
            command=CMD,
            code_dir=CODE_DIR,
            engine=JobEngine(engine_id="", image_url=IMAGE),
            environments={"MA_AK": AK, "MA_SK": SK},
        ),
        spec=Spec(
            resource=SpecResource(flavor_id=FLAVOR, node_count=1),
            log_export_path=LogExportPath(obs_url=f"obs://{OBS_BUCKET}/{OBS_PREFIX}"),

        ),
    )
    resp = client.create_training_job(CreateTrainingJobRequest(body=job))
    job_id = resp.metadata.id
    print(f"[{datetime.datetime.now()}] 提交成功: {name} (ID: {job_id})")
    return job_id, name


def wait_job(client, job_id, timeout=7200):
    start = time.time()
    last_status = None
    while time.time() - start < timeout:
        resp = client.show_training_job_details(ShowTrainingJobDetailsRequest(training_job_id=job_id))
        status = resp.status.phase
        if status != last_status:
            dur = resp.status.duration // 1000
            print(f"[{datetime.datetime.now()}] 状态: {status} ({dur}s)")
            last_status = status
        if status in ("Completed", "Succeeded"):
            return True
        if status in ("Failed", "Terminated", "Error"):
            return False
        time.sleep(30)
    return False


def download_user_log(obs_client, job_id):
    key = f"{OBS_PREFIX}modelarts-job-{job_id}-worker-0.log"
    tmp_path = Path(tempfile.gettempdir()) / f"ma_log_{job_id}.txt"
    resp = obs_client.getObject(OBS_BUCKET, key, downloadPath=str(tmp_path))
    if resp.status < 300:
        log_text = tmp_path.read_text(encoding="utf-8", errors="replace")
        print(f"已从 OBS 下载用户日志: {key} ({len(log_text)} 字符)")
        return log_text
    print(f"下载用户日志失败: {resp.errorMessage}")
    return ""


def download_model_files(obs_client):
    """从 OBS output/ 目录下载模型文件（.pkl / .lgb.txt / .csv / .json）到本地"""
    model_dir = REPORT_DIR / "models"
    model_dir.mkdir(parents=True, exist_ok=True)

    resp = obs_client.listObjects(OBS_BUCKET, prefix=OBS_PREFIX)
    if resp.status >= 300:
        print(f"列出 OBS 模型文件失败: {resp.errorMessage}")
        return []

    downloaded = []
    contents = resp.body.contents if resp.body and resp.body.contents else []
    for obj in contents:
        key = obj.key
        if not key or key.endswith("/"):
            continue
        if key.endswith(".log"):
            continue
        if not key.endswith((".pkl", ".lgb.txt", ".csv", ".json")):
            continue
        if "runtime_info" in key or "global_status" in key or "pod_fault" in key or "runningCount" in key or "core-dump" in key:  # noqa: E501
            continue
        filename = os.path.basename(key)
        local_path = model_dir / filename
        r = obs_client.getObject(OBS_BUCKET, key, downloadPath=str(local_path))
        if r.status < 300:
            downloaded.append(str(local_path))
            print(f"已下载模型文件: {key} -> {local_path}")
        else:
            print(f"下载失败: {key} ({r.errorMessage})")
    return downloaded


def extract_results(log_text):
    results = {}
    patterns = {
        "overall_ic": r"整体 IC:\s*([\d.-]+)",
        "daily_ic": r"日均 IC:\s*([\d.-]+)",
        "daily_rank_ic": r"日均 Rank IC:\s*([\d.-]+)",
        "ic_ir": r"IC IR:\s*([\d.-]+)",
        "ic_positive_ratio": r"IC > 0 占比:\s*([\d.%]+)",
    }
    for key, pat in patterns.items():
        m = re.search(pat, log_text)
        if m:
            val = m.group(1).strip("%")
            try:
                results[key] = float(val)
            except ValueError:
                results[key] = val
    return results


def save_report(job_name, results, log_text):
    now = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    report = {
        "job_name": job_name,
        "download_time": now,
        "image": IMAGE,
        "results": results,
        "log_tail": log_text[-3000:] if log_text else "",
    }
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    fp = REPORT_DIR / f"cloud_auto_{now}.json"
    fp.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"报告已保存: {fp}")

    if results:
        print(f"\n{'='*50}")
        print("训练结果摘要")
        print(f"{'='*50}")
        print(f"日均 IC:      {results.get('daily_ic', '?')}")
        print(f"日均 Rank IC: {results.get('daily_rank_ic', '?')}")
        print(f"IC IR:        {results.get('ic_ir', '?')}")
        print(f"{'='*50}")


def main():
    if not AK or not SK:
        print("错误: 请设置 HUAWEICLOUD_AK 和 HUAWEICLOUD_SK")
        sys.exit(1)

    print(f"[{datetime.datetime.now()}] 开始自动训练")
    ma_client = get_ma_client()
    obs_client = get_obs_client()

    job_id, job_name = submit_job(ma_client)
    ok = wait_job(ma_client, job_id)

    if ok:
        print(f"[{datetime.datetime.now()}] 训练成功! 下载日志和模型...")
        time.sleep(10)
        log_text = download_user_log(obs_client, job_id)
        results = extract_results(log_text)
        save_report(job_name, results, log_text)
        model_files = download_model_files(obs_client)
        if model_files:
            print(f"\n下载了 {len(model_files)} 个模型文件到 {REPORT_DIR / 'models'}")
    else:
        print(f"[{datetime.datetime.now()}] 训练失败!")
        sys.exit(1)


if __name__ == "__main__":
    main()
