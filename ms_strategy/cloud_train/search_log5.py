import os
import tempfile
from pathlib import Path

from obs import ObsClient

AK = os.environ["HUAWEICLOUD_AK"]
SK = os.environ["HUAWEICLOUD_SK"]
obs = ObsClient(access_key_id=AK, secret_access_key=SK, server="https://obs.cn-east-3.myhuaweicloud.com")
job_id = "fd1712df-bc67-4d23-b057-e92cb517ac98"
key = f"output/modelarts-job-{job_id}-worker-0.log"
tmp = Path(tempfile.gettempdir()) / f"ma_log_{job_id}.txt"
r = obs.getObject("qt-models", key, downloadPath=str(tmp))
text = tmp.read_text(encoding="utf-8", errors="replace")

for kw in ["模型已保存", "报告已保存", "LightGBM", "预测结果", "ModelArts 输出", "pickle", "save_model", "shutil", "所有文件"]:  # noqa: E501
    idx = text.find(kw)
    if idx >= 0:
        print(f'[{kw}] 位置 {idx}:')
        print(text[max(0, idx - 50) : idx + 200])
        print("---")

print("\n=== 日志最后3000字符 ===")
print(text[-3000:])
