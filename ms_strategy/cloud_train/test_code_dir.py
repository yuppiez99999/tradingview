"""快速测试 code_dir 是否工作：提交一个只列出工作目录的作业，带日志输出"""
import datetime
import os
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

creds = BasicCredentials(ak=AK, sk=SK)
client = ModelArtsClient.new_builder().with_credentials(creds).with_region(ModelArtsRegion.CN_EAST_3).build()
obs = ObsClient(access_key_id=AK, secret_access_key=SK, server="https://obs.cn-east-3.myhuaweicloud.com")

now = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
name = f"qt-codetest-{now}"
CMD = 'bash -c "echo === USER-JOB-DIR === && ls -laR /home/ma-user/modelarts/user-job-dir/ 2>&1 && echo === DONE ==="'

job = Job(
    kind="job",
    metadata=JobMetadata(name=name),
    algorithm=JobAlgorithm(
        command=CMD,
        code_dir="obs://qt-data/code/",
        engine=JobEngine(engine_id="", image_url="qt1/qt-qlib-trainer:v7"),
    ),
    spec=Spec(
        resource=SpecResource(flavor_id="modelarts.vm.cpu.8u", node_count=1),
        log_export_path=LogExportPath(obs_url="obs://qt-models/output/"),
    ),
)
resp = client.create_training_job(CreateTrainingJobRequest(body=job))
job_id = resp.metadata.id
print(f"提交成功: {name} (ID: {job_id})")

for _ in range(60):
    r = client.show_training_job_details(ShowTrainingJobDetailsRequest(training_job_id=job_id))
    status = r.status.phase
    print(f"[{datetime.datetime.now()}] {status}")
    if status in ("Completed", "Succeeded", "Failed", "Terminated", "Error"):
        break
    time.sleep(10)

print(f"\n最终状态: {status}")

time.sleep(10)
key = f"output/modelarts-job-{job_id}-worker-0.log"
tmp_path = Path(tempfile.gettempdir()) / f"ma_log_{job_id}.txt"
r = obs.getObject("qt-models", key, downloadPath=str(tmp_path))
if r.status < 300:
    log_text = tmp_path.read_text(encoding="utf-8", errors="replace")
    print(f"\n=== 用户日志 ({len(log_text)} 字符) ===")
    print(log_text[-3000:])
else:
    print(f"下载日志失败: {r.errorMessage}")
