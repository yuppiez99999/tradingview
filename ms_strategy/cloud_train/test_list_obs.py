import os

from obs import ObsClient

AK = os.environ["HUAWEICLOUD_AK"]
SK = os.environ["HUAWEICLOUD_SK"]
obs = ObsClient(access_key_id=AK, secret_access_key=SK, server="https://obs.cn-east-3.myhuaweicloud.com")

resp = obs.listObjects("qt-models", prefix="output/")
print(f"status: {resp.status}")
if resp.status < 300:
    print(f"body: {resp.body}")
    if resp.body:
        print(f"contents: {resp.body.contents}")
        if resp.body.contents:
            for obj in resp.body.contents:
                print(f"  key={obj.key}, size={obj.size}")
        else:
            print("contents 为空")
else:
    print(f"错误: {resp.errorMessage}")
