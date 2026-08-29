"""训练完成后，用 OBS SDK 上传模型文件到 OBS"""
import glob
import os

from obs import ObsClient

AK = os.environ.get("MA_AK", "")
SK = os.environ.get("MA_SK", "")
BUCKET = "qt-models"
PREFIX = "models/"
LOCAL_DIR = "/home/ma-user/modelarts/output"

if not AK or not SK:
    print("错误: MA_AK/MA_SK 未设置")
    exit(1)

obs = ObsClient(access_key_id=AK, secret_access_key=SK, server="https://obs.cn-east-3.myhuaweicloud.com")
uploaded = 0
for fp in glob.glob(os.path.join(LOCAL_DIR, "*")):
    if os.path.isdir(fp):
        continue
    if fp.endswith(".log"):
        continue
    key = PREFIX + os.path.basename(fp)
    resp = obs.putFile(BUCKET, key, file_path=fp)
    if resp.status < 300:
        size = os.path.getsize(fp) / 1024
        print(f"已上传: {os.path.basename(fp)} ({size:.1f} KB) -> obs://{BUCKET}/{key}")
        uploaded += 1
    else:
        print(f"上传失败: {fp} ({resp.errorMessage})")
print(f"\n完成: {uploaded} 个模型文件已上传到 obs://{BUCKET}/{PREFIX}")
