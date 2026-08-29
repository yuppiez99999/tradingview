import os
import tempfile
from pathlib import Path

from obs import ObsClient

AK = os.environ["HUAWEICLOUD_AK"]
SK = os.environ["HUAWEICLOUD_SK"]
obs = ObsClient(access_key_id=AK, secret_access_key=SK, server="https://obs.cn-east-3.myhuaweicloud.com")
job_id = "fcbf60b2-b9a9-4064-8bed-c0eb14ee2050"
key = f"output/modelarts-job-{job_id}-worker-0.log"
tmp = Path(tempfile.gettempdir()) / f"ma_log_{job_id}.txt"
r = obs.getObject("qt-models", key, downloadPath=str(tmp))
text = tmp.read_text(encoding="utf-8", errors="replace")

for kw in ["Alpha158", "特征工程", "构建数据集", "训练 LightGBM", "完成!", "评估", "报告已保存", "模型已保存", "Collecting", "Successfully installed"]:
    idx = text.find(kw)
    if idx >= 0:
        print(f'[{kw}] 位置 {idx}:')
        print(text[max(0, idx - 30) : idx + 200])
        print("---")

idx = text.find("run command")
if idx >= 0:
    end = text.find("\n", idx)
    print(f"\n=== RUN COMMAND ===\n{text[idx:end]}")
