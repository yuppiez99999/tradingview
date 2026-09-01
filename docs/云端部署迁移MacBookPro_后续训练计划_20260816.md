# 云端部署迁移 MacBook Pro + 后续训练计划（小白向）

| 项 | 值 |
|---|---|
| 适用 | **已经按《Windows 版小白教程》在华为云跑通第一个训练作业的人**，后期要换 MacBook Pro |
| 前置 | 华为云账号、OBS 桶、SWR 镜像、ModelArts、训练数据**全部已在云上跑通** |
| 目标 | (1) 把本地开发机从 Windows 平滑迁到 MacBook Pro；(2) 建立长期可重复的云端训练工作流 |
| 耗时 | 迁移约 2–3 小时；后续每次训练 30–60 分钟（含等待） |
| 日期 | 2026-08-16 |

---

## 这个计划教你做什么（先读这段）

**用大白话说：** 你已经在 Windows 上把华为云那条链路跑通了（账号 → OBS → SWR → ModelArts → 模型回拉）。现在你要把身边的电脑换成 MacBook Pro。**云端的东西一样都不用重做**，要做的只是把"本地这台发指令的电脑"换一下，然后建立一套以后能反复用的训练节奏。

**打个比方：** 云端是你的"远程洗衣店"，Windows 是你旧的"手机下单 App"，MacBook Pro 是你新买的"手机"。洗衣店没变，你只是在新手机上重新登录一下账号、装一下 App，以后就能继续下单洗衣服了。

**做完你会得到：**
1. 一台能直接给华为云发训练指令的 MacBook Pro
2. 一套"改参数 → 提交 → 看结果 → 下模型"的日常流程
3. 一套"每周自动训练 + 自动回传模型"的定时机制
4. 一份长期训练路线图（练什么、什么时候练、花多少钱）

> **关键认知：** 云端 90% 的东西不用动。要动的只有 4 样：① 本地命令行工具；② 华为云钥匙（AK/SK）；③ 项目代码；④ Docker 镜像（因为 Mac 是 ARM 芯片，要重新打包一次）。

---

## 第一部分：迁移到 MacBook Pro（一次性，约 2–3 小时）

### 0. 先搞清楚：换 Mac 会变什么、不变什么

| 项 | Windows（旧） | MacBook Pro（新） | 要不要重做 |
|---|---|---|---|
| 华为云账号/实名 | 已通过 | 同一个账号 | ❌ 不用 |
| AK/SK | 已在 Windows 环境变量 | 要搬到 Mac | ✅ 要搬 |
| OBS 桶 qt-data / qt-models | 已建好、有数据 | 同一个桶 | ❌ 不用 |
| SWR 组织 qt | 已建好 | 同一个组织 | ❌ 不用 |
| ModelArts | 已开通 | 同一个服务 | ❌ 不用 |
| 训练数据（730MB） | 已传到 OBS | 在云上，不用重传 | ❌ 不用 |
| Docker 镜像 qt-qlib-trainer | 已推送 | **要重新打包推送**（架构不同） | ✅ 要重做 |
| 命令行 | PowerShell | Terminal + zsh | ✅ 要换 |
| 装软件 | winget | brew（Homebrew） | ✅ 要换 |
| 钥匙保存 | `setx` 环境变量 | `~/.zshrc` 或 Keychain | ✅ 要换 |
| obsutil | windows_amd64 | darwin_arm64 | ✅ 要换 |
| 路径写法 | `C:\` 反斜杠 | `/Users/你/` 正斜杠 | ✅ 要换 |
| Docker 构建 | 直接 build | **要加 `--platform linux/amd64`** | ✅ 关键差异 |

> ⚠️ **最关键的一行：** Mac 是 ARM 芯片（M1/M2/M3/M4/M5），华为云 ModelArts 是 x86 芯片。Docker 构建时**必须**加 `--platform linux/amd64`，否则推上去云端跑不起来。这是 Mac 版和 Windows 版**唯一**的本质差异，也是最容易翻车的地方。

---

### 第 1 步：拿到 MacBook Pro 后的第一件事

**做什么：** 确认机器基本信息，能上网。

**怎么做：**

1.1 开机，按提示设置（联网、登 Apple ID）。

1.2 确认芯片型号。打开"终端"（启动台 → 其他 → 终端，或 Spotlight 搜 `terminal`），输入：

    sysctl -n machdep.cpu.brand_string

应输出类似 `Apple M5` 或 `Apple M3 Pro`。

1.3 确认 macOS 版本：

    sw_vers

应输出 `ProductVersion: 14.x` 或更高。

1.4 确认内存：

    system_profiler SPHardwareDataType | grep Memory

**怎么确认做对了：** 芯片是 M 系列、macOS ≥ 14、内存 ≥ 16GB（训练调度够用） ✅

**⚠️ 避坑：**

| 问题 | 解决 |
|---|---|
| 内存只有 8GB | 能用但本地跑大样本会吃力；云端训练不受影响 |
| macOS 版本 < 14 | 先系统设置 → 软件更新升级 |
| 不知道终端在哪 | Spotlight（Cmd+空格）搜 `terminal` 回车 |

---

### 第 2 步：装 Homebrew（Mac 的"软件管家"）

**做什么：** 装 Homebrew，以后所有命令行工具都用它装。

**怎么做：**

2.1 在终端粘贴这一整行回车（中途要输你 Mac 的开机密码，输的时候不显示，正常现象）：

    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

2.2 装完它会提示你把两行 `eval` 加到 `~/.zshrc`，照做。一般是这样（具体以终端提示为准）：

    echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zshrc
    eval "$(/opt/homebrew/bin/brew shellenv)"

2.3 验证：

    brew --version

**怎么确认做对了：** 输出 `Homebrew 4.x.x` ✅

**⚠️ 避坑：**

| 问题 | 解决 |
|---|---|
| 下载很慢/卡住 | 换国内源：先 `export HOMEBREW_BREW_GIT_REMOTE=https://mirrors.tuna.tsinghua.edu.cn/git/homebrew/brew.git` 再装 |
| 提示 `Command Line Tools not found` | 按提示装，或 `xcode-select --install` |
| 装完 `brew: command not found` | 没执行 2.2 那两行；补上 |

---

### 第 3 步：装 Python 和华为云命令行工具

**做什么：** 在 Mac 上装 Python 3.11 和华为云 SDK。

**怎么做：**

3.1 装 Python：

    brew install python@3.11
    echo 'export PATH="/opt/homebrew/opt/python@3.11/bin:$PATH"' >> ~/.zshrc
    source ~/.zshrc
    python3.11 --version

应输出 `Python 3.11.x`。

3.2 装 uv（项目用的依赖管理工具）：

    curl -LsSf https://astral.sh/uv/install.sh | sh
    source ~/.zshrc
    uv --version

3.3 装华为云 Python SDK：

    pip3.11 install huaweicloudsdkcore huaweicloudsdkiam huaweicloudsdkobs huaweicloudsdkmodelarts huaweicloudsdkswr

3.4 验证：

    python3.11 -c "from huaweicloudsdkcore.auth.credentials import BasicCredentials; print('OK')"

**怎么确认做对了：** 输出 `OK` ✅

**⚠️ 避坑：**

| 问题 | 解决 |
|---|---|
| `pip3.11: command not found` | 用 `python3.11 -m pip install ...` 代替 |
| pip 下载慢 | 加 `-i https://pypi.tuna.tsinghua.edu.cn/simple` |
| `curl: command not found` | Mac 自带 curl，重启终端试试 |

---

### 第 4 步：装 Docker Desktop for Mac（关键一步）

**做什么：** 装 Docker，并开启 Rosetta 支持（ARM Mac 跑 x86 镜像需要）。

**怎么做：**

4.1 装 Docker Desktop：

    brew install --cask docker

4.2 启动 Docker Desktop：启动台 → Docker → 打开。等右上角/状态栏鲸鱼图标稳定（不再动）。

4.3 **开启 Rosetta 2**（关键！）：

- Docker Desktop 点右上角齿轮 ⚙️ → Settings → "Features in development" 或 "General"
- 勾选 **"Use Rosetta for x86/amd64 emulation on Apple Silicon"** ✅
- 点 "Apply & Restart"，等 Docker 重启完

4.4 验证 Docker：

    docker --version
    docker run --rm --platform linux/amd64 hello-world

第二条命令会下载一个 x86 的小镜像并跑起来，看到 `Hello from Docker!` 就说明 Rosetta 跨架构跑通了。

**怎么确认做对了：** 4.4 看到 `Hello from Docker!` ✅

**⚠️ 避坑：**

| 问题 | 解决 |
|---|---|
| Docker 启动报错要更新 | 按提示更新 Docker Desktop |
| 4.4 报 `exec format error` | Rosetta 没开；回 4.3 勾选并重启 Docker |
| 4.4 很慢 | 第一次拉镜像慢，等一会 |
| 鲸鱼图标一直转 | 内存不够或磁盘不够；Docker 设置里把内存调到 8GB |

> ⚠️ **这一步是 Mac 版的核心。** Windows 版没有这步，因为 Windows 本来就是 x86。Mac 必须开 Rosetta，否则后面 `docker build --platform linux/amd64` 会失败。

---

### 第 5 步：把项目代码从 Windows 搬到 Mac

**做什么：** 把 `E:\各种PY程序\28-终极量化交易系统8.4\` 整个项目搬到 Mac。

> **重要：** 训练数据（730MB）已经在云上（OBS），**不需要搬**。要搬的是**代码**。但为了本地能做小样本验证，建议连数据一起搬（730MB 不大）。

**怎么做（三选一，推荐第 1 种）：**

**方式 1：U 盘 / 移动硬盘（最简单，推荐小白）**

5.1a 在 Windows 上：把 `E:\各种PY程序\28-终极量化交易系统8.4\` 整个文件夹拷到 U 盘（NTFS 或 exFAT 格式）。

5.2a U 盘插到 Mac。如果 Mac 读不了 NTFS，装一个免费工具 Mounty：

    brew install --cask mounty

5.3a 在 Mac 终端：

    mkdir -p ~/28-终极量化交易系统8.4
    cp -R /Volumes/你的U盘名/28-终极量化交易系统8.4/* ~/28-终极量化交易系统8.4/

**方式 2：用 git（如果项目在 GitHub/Gitee 上）**

    cd ~
    git clone 你的仓库地址 28-终极量化交易系统8.4

> 注意：数据文件（data_cache/、qlib_data/、models/）一般不进 git，要单独搬或从 OBS 拉回。

**方式 3：从 OBS 拉回（数据部分）**

代码用方式 1/2 搬，数据从 OBS 拉（见第 7 步装好 obsutil 后再做）。

5.4 进项目目录：

    cd ~/28-终极量化交易系统8.4
    ls

应看到 `pyproject.toml`、`ms_strategy/`、`qlib/`、`config/` 等。

5.5 装项目依赖：

    uv sync

**怎么确认做对了：** `ls` 能列出项目文件，`uv sync` 无报错 ✅

**⚠️ 避坑：**

| 问题 | 解决 |
|---|---|
| U 盘 NTFS 只读 | 装 Mounty（见 5.2a）或把 U 盘格式化成 exFAT |
| 中文文件夹名乱码 | 用 `cp -R` 时加双引号：`cp -R "/Volumes/U盘/28-终极量化交易系统8.4" ~/` |
| `uv sync` 报错 | 确认在项目根目录；或 `uv sync --python 3.11` |
| 拷贝很慢 | 730MB 正常 5–10 分钟；只搬代码（不含 data）会快很多 |

---

### 第 6 步：把华为云钥匙（AK/SK）搬到 Mac

**做什么：** 把 Windows 上的 AK/SK 读出来，写到 Mac。

**怎么做：**

6.1 在 **Windows** 上打开 PowerShell，读出 AK 和 SK：

    echo $env:HUAWEICLOUD_AK
    echo $env:HUAWEICLOUD_SK

把这两个值记下来（AK 一串字母数字，SK 一串字母数字）。

> ⚠️ SK 是密钥，**别截图发群里、别提交到 git**。

6.2 在 **Mac** 终端，把钥匙写到 `~/.zshrc`（把 `你的AK` / `你的SK` 换成实际值）：

    echo 'export HUAWEICLOUD_AK="你的AK"' >> ~/.zshrc
    echo 'export HUAWEICLOUD_SK="你的SK"' >> ~/.zshrc
    source ~/.zshrc

6.3 验证：

    echo $HUAWEICLOUD_AK
    echo $HUAWEICLOUD_SK

应分别输出你的 AK 和 SK。

**进阶（更安全，可选）：** 用 macOS Keychain 存，不落明文：

    security add-generic-password -a "$USER" -s "HUAWEICLOUD_AK" -w "你的AK"
    security add-generic-password -a "$USER" -s "HUAWEICLOUD_SK" -w "你的SK"

然后在 `~/.zshrc` 里改成现取现用：

    export HUAWEICLOUD_AK=$(security find-generic-password -a "$USER" -s "HUAWEICLOUD_AK" -w)
    export HUAWEICLOUD_SK=$(security find-generic-password -a "$USER" -s "HUAWEICLOUD_SK" -w)

**怎么确认做对了：** 6.3 输出正确的 AK/SK ✅

**⚠️ 避坑：**

| 问题 | 解决 |
|---|---|
| Windows 上 `echo $env:...` 输出空 | 重新开 PowerShell；或当初 setx 拼错了，去华为云 IAM 重新生成 AK/SK |
| Mac 上 `echo $HUAWEICLOUD_AK` 输出空 | 没执行 `source ~/.zshrc`；或 echo 那行没写进文件，`cat ~/.zshrc` 检查 |
| SK 忘了 | 华为云控制台 → IAM → 用户 → qt-admin → 访问密钥 → 删旧建新，这次一定保存 |

---

### 第 7 步：装 obsutil（Mac 版）

**做什么：** 装 Mac 版的 obsutil（上传下载 OBS 文件的命令行工具）。

**怎么做：**

7.1 下载（Mac ARM 版）：

    cd ~/Downloads
    curl -O https://obs-community.obs.cn-north-1.myhuaweicloud.com/obsutil/current/obsutil_darwin_arm64.tar.gz

> 如果你的 Mac 是 Intel 芯片（很少见），换成 `obsutil_darwin_amd64.tar.gz`。

7.2 解压并放到好找的位置：

    tar -xzf obsutil_darwin_arm64.tar.gz
    mv obsutil_darwin_arm64_*/obsutil ~/obsutil
    chmod +x ~/obsutil

7.3 配置：

    ~/obsutil config -ak=$HUAWEICLOUD_AK -sk=$HUAWEICLOUD_SK -endpoint=obs.cn-east-3.myhuaweicloud.com

看到 `Set obsutil config successfully!` 就对了。

7.4 设个别名方便用：

    echo 'alias obsutil="$HOME/obsutil"' >> ~/.zshrc
    source ~/.zshrc

7.5 验证：

    obsutil ls "obs://qt-data/" -limit=5

应列出你之前传的训练数据（`qlib_data/`、`data_cache/`、`config/`）。

**怎么确认做对了：** 7.5 能列出云端文件 ✅

**⚠️ 避坑：**

| 问题 | 解决 |
|---|---|
| 下载链接打不开 | 浏览器访问 https://obs-community.obs.cn-north-1.myhuaweicloud.com/obsutil/current/ 找最新版 |
| `Permission denied` | 没执行 `chmod +x` |
| `obsutil: command not found` | 别名没生效；用全路径 `~/obsutil` 代替 |
| 列桶报 `Access Denied` | AK/SK 没配对；回第 6 步 |

---

### 第 8 步：登录华为云 SWR（镜像仓库）

**做什么：** 让 Mac 上的 Docker 能往华为云推镜像。

**怎么做：**

8.1 华为云控制台 → ≡ → 搜 `SWR` → 容器镜像服务 SWR → 左侧"登录指令" → "生成临时登录指令" → 复制。

8.2 在 Mac 终端粘贴复制的命令，回车。看到 `Login Succeeded` 就对了。

8.3 验证：

    docker info | grep -i huawei

**怎么确认做对了：** `Login Succeeded` ✅

> ⚠️ 临时登录指令**有效期 24 小时**，过期了重新生成一条登录即可。

---

### 第 9 步：在 Mac 上重新构建并推送镜像（迁移最关键一步）

**做什么：** 因为 Mac 是 ARM，云端是 x86，要重新打包一次镜像。

**怎么做：**

9.1 确认 Dockerfile 还在（第 5 步搬项目时应该一起搬过来了）：

    ls ~/28-终极量化交易系统8.4/cloud/docker/Dockerfile.qlib-trainer

> 如果没有，从 Windows 那台机器补拷一次 `cloud/docker/` 和根目录的 `.dockerignore`。

9.2 进项目目录开始构建（**注意 `--platform linux/amd64`，这是 Mac 版的关键**）：

    cd ~/28-终极量化交易系统8.4
    docker build --platform linux/amd64 -t swr.cn-east-3.myhuaweicloud.com/qt/qt-qlib-trainer:latest -f cloud/docker/Dockerfile.qlib-trainer .

回车，**等 15–30 分钟**（ARM 转 x86 比原生慢一些）。最后看到：

    Successfully tagged swr.cn-east-3.myhuaweicloud.com/qt/qt-qlib-trainer:latest

9.3 验证镜像构建成功：

    docker images | grep qt-qlib-trainer

9.4 推送到华为云：

    docker push swr.cn-east-3.myhuaweicloud.com/qt/qt-qlib-trainer:latest

**等 5–15 分钟**，看到 `digest: sha256:xxx` 就对了。

9.5 推一个带日期的版本号（推荐，方便回滚）：

    docker tag swr.cn-east-3.myhuaweicloud.com/qt/qt-qlib-trainer:latest swr.cn-east-3.myhuaweicloud.com/qt/qt-qlib-trainer:20260816
    docker push swr.cn-east-3.myhuaweicloud.com/qt/qt-qlib-trainer:20260816

**怎么确认做对了：** 华为云 SWR 控制台 → 组织 `qt` → 看到 `qt-qlib-trainer`，有 `latest` 和 `20260816` 两个 tag ✅

**⚠️ 避坑：**

| 问题 | 解决 |
|---|---|
| `docker build` 报 `exec format error` | 没加 `--platform linux/amd64`；或 Rosetta 没开（回第 4 步） |
| 构建到 `uv sync` 卡住 | 网络慢；或 Dockerfile 里把 `uv sync` 换成 `pip install -r requirements.txt` |
| 构建特别慢（>40 分钟） | ARM 转 x86 本身慢；可改用 buildx 多平台构建提速（见下方进阶） |
| `COPY failed` | 项目文件没拷全；`ls ms_strategy/cloud_train/` 检查 |
| 推送报 `denied` | SWR 登录过期；回第 8 步重新登录 |
| 推送断 | 网络问题；再推一次会续传 |

**进阶（可选）：用 buildx 加速**

    docker buildx create --use --name qt-builder
    docker buildx build --platform linux/amd64 -t swr.cn-east-3.myhuaweicloud.com/qt/qt-qlib-trainer:latest -f cloud/docker/Dockerfile.qlib-trainer . --push

---

### 第 10 步：用 Mac 提交一个训练作业，验证整条链路

**做什么：** 用新 Mac 提交一次训练，确认迁移成功。

**怎么做（控制台操作，和 Windows 版一样）：**

10.1 华为云控制台 → ModelArts → 训练管理 → 训练作业 → 创建训练作业。

10.2 填：
- **作业名称：** `qt-mac-migrate-test`
- **算法来源：** 自定义
- **镜像：** SWR → `qt/qt-qlib-trainer:latest`
- **启动命令：**

      uv run python ms_strategy/cloud_train/modelscope_train.py --data-dir /cache/data/qlib_data/cn_data --market csi300 --start 2015-01-01 --end 2026-08-14 --train-end 2024-12-31 --valid-end 2025-06-30 --model lightgbm --num-leaves 128 --boost-round 100 --learning-rate 0.02 --max-depth 8 --output /cache/output

> 注意这里 `--boost-round 100`（先跑 100 轮验证链路，跑通再加到 500）。

- **资源池：** 公共资源池
- **规格：** `modelarts.bm.8c32g.cpu`
- **节点数：** 1
- **永久保存日志：** ✅
- **训练输入：** OBS `qt-data` → 本地 `/cache/data`
- **训练输出：** OBS `qt-models` → 本地 `/cache/output`

10.3 提交，等状态 `已完成`（约 5–15 分钟）。

10.4 看日志有 IC 指标输出。

10.5 下载模型到 Mac：

    obsutil cp -r -f "obs://qt-models/" ~/28-终极量化交易系统8.4/models/cloud_trained/

10.6 验证模型能加载：

    cd ~/28-终极量化交易系统8.4
    python3.11 -c "import pickle, glob; pkls=glob.glob('models/cloud_trained/**/*.pkl', recursive=True); print('找到模型:', pkls); [print('加载成功:', type(pickle.load(open(p,'rb')))) for p in pkls[:1]]"

**怎么确认做对了：** 看到 `找到模型: [...]` 和 `加载成功: ...` ✅

> 🎉 **到这里迁移完成！** 你的 MacBook Pro 已经能完全替代 Windows 做云端训练了。

---

## 第二部分：后续训练计划（日常可重复工作流）

> 从这里开始是"以后每次训练怎么做"。建议把这一部分打印出来贴在屏幕旁边。

### 日常训练工作流（每次训练 4 步）

```
改参数 → 提交作业 → 看日志/结果 → 下载模型
```

#### 步骤 A：改训练参数

训练参数在**启动命令**里改（第 10.2 步那段命令）。常用参数：

| 参数 | 含义 | 常用值 | 怎么选 |
|---|---|---|---|
| `--market` | 训练哪个指数成分股 | `csi300` / `csi500` / `csi800` | 先 300，稳定后扩 500 |
| `--start` / `--end` | 训练数据时间范围 | `2015-01-01` / `2026-08-14` | 越长越稳，但越慢 |
| `--train-end` | 训练集截止 | `2024-12-31` | 留最近半年做验证 |
| `--valid-end` | 验证集截止 | `2025-06-30` | 留最近做测试 |
| `--model` | 模型类型 | `lightgbm` | 也可试 `xgboost` |
| `--num-leaves` | LightGBM 叶子数 | `64` / `128` / `256` | 越大越容易过拟合 |
| `--boost-round` | 迭代轮数 | `100` / `500` / `1000` | 先 100 跑通，再 500 |
| `--learning-rate` | 学习率 | `0.01` / `0.02` / `0.05` | 小更稳但慢 |
| `--max-depth` | 树深 | `6` / `8` / `-1`（不限） | 8 较通用 |

#### 步骤 B：提交作业

控制台 → ModelArts → 训练作业 → 创建 → 填好 → 提交。

**或用命令行提交（进阶，见第三部分脚本）。**

#### 步骤 C：看日志和结果

- 控制台点作业名 → "日志"页签
- 关注：`qlib.init` 成功 → `Alpha158` 特征数 → `LightGBM` 训练进度 → `IC` / `Rank IC` 指标
- **IC > 0.05 算可用，> 0.08 算不错，> 0.1 算很好**

#### 步骤 D：下载模型到 Mac

    obsutil cp -r -f "obs://qt-models/本次作业输出/" ~/28-终极量化交易系统8.4/models/cloud_trained/作业名/

> 建议按作业名建子文件夹，别覆盖之前的模型。

---

### 推荐训练节奏（每周/每月练什么）

#### 每周一次：周度增量更新

**目的：** 用最新一周数据更新模型，保持模型新鲜。

**操作：**
1. 周五收盘后，先在本地跑数据更新（更新 `data_cache/` 和 `qlib_data/`）
2. 把更新后的数据传到 OBS：

       obsutil cp -r -f ~/28-终极量化交易系统8.4/qlib_data/ "obs://qt-data/qlib_data/"
       obsutil cp -r -f ~/28-终极量化交易系统8.4/data_cache/ "obs://qt-data/data_cache/"

3. 提交训练作业，作业名带日期：`qt-weekly-20260815`
4. 周一开盘前下载模型，替换本地使用模型

**规格选择：** `modelarts.bm.8c32g.cpu`，约 15 分钟，约 ¥0.2。

#### 每月一次：月度全量重训

**目的：** 用更长历史、更全数据重训，防止模型漂移。

**操作：**
1. 提交作业，参数：
   - `--start 2015-01-01 --end 2026-08-14`
   - `--boost-round 500`
   - `--num-leaves 128`
2. 作业名：`qt-monthly-202608`
3. 跑完和上周模型对比 IC，新模型更好才替换

**规格选择：** `modelarts.bm.16c64g.cpu`（更大更快），约 30 分钟，约 ¥0.5。

#### 每季度一次：超参搜索

**目的：** 系统性找最优超参。

**操作：** 提交多个作业并行跑不同超参组合（见下方"超参搜索"脚本）。

**规格选择：** 开 4–8 个并行作业，每个 `modelarts.bm.8c32g.cpu`，约 1 小时，约 ¥2。

---

### 多市场训练计划

| 市场 | 频率 | 启动命令关键参数 | 用途 |
|---|---|---|---|
| CSI300（沪深300） | 每周 | `--market csi300` | 主力选股池 |
| CSI500（中证500） | 每两周 | `--market csi500` | 中盘补充 |
| CSI800（中证800） | 每月 | `--market csi800` | 全市场扫描 |
| CSI100（中证100） | 每月 | `--market csi100` | 大盘风格 |

> 建议从 CSI300 跑稳定后再扩到 500/800。每个市场一个 OBS 子目录：`obs://qt-models/csi300/`、`obs://qt-models/csi500/`。

---

### 定时训练（让 Mac 自动每周提交）

**做什么：** 用 macOS 的 `launchd`（Mac 的定时任务）每周五自动提交训练。

**怎么做：**

1. 写一个提交脚本 `~/28-终极量化交易系统8.4/scripts/weekly_train.sh`：

       #!/bin/bash
       # 周度自动训练
       set -e
       export HUAWEICLOUD_AK=$(security find-generic-password -a "$USER" -s "HUAWEICLOUD_AK" -w 2>/dev/null || echo $HUAWEICLOUD_AK)
       export HUAWEICLOUD_SK=$(security find-generic-password -a "$USER" -s "HUAWEICLOUD_SK" -w 2>/dev/null || echo $HUAWEICLOUD_SK)

       WEEK=$(date +%Y%m%d)
       JOB_NAME="qt-weekly-${WEEK}"

       # 用华为云 CLI 提交作业（命令见第三部分脚本）
       python3.11 ~/28-终极量化交易系统8.4/scripts/submit_train.py --job-name $JOB_NAME --market csi300 --boost-round 500

       echo "$(date) 提交了 $JOB_NAME" >> ~/28-终极量化交易系统8.4/logs/weekly_train.log

   给执行权限：

       chmod +x ~/28-终极量化交易系统8.4/scripts/weekly_train.sh

2. 写 launchd 配置 `~/Library/LaunchAgents/com.qt.weekly-train.plist`：

       <?xml version="1.0" encoding="UTF-8"?>
       <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
       <plist version="1.0">
       <dict>
           <key>Label</key>
           <string>com.qt.weekly-train</string>
           <key>ProgramArguments</key>
           <array>
               <string>/Users/你的用户名/28-终极量化交易系统8.4/scripts/weekly_train.sh</string>
           </array>
           <key>StartCalendarInterval</key>
           <dict>
               <key>Weekday</key>
               <integer>6</integer>
               <key>Hour</key>
               <integer>18</integer>
           </dict>
           <key>StandardOutPath</key>
           <string>/Users/你的用户名/28-终极量化交易系统8.4/logs/launchd_out.log</string>
           <key>StandardErrorPath</key>
           <string>/Users/你的用户名/28-终极量化交易系统8.4/logs/launchd_err.log</string>
       </dict>
       </plist>

   > `Weekday 6` = 周六，`Hour 18` = 晚上 6 点。把"你的用户名"换成实际的（终端输 `whoami` 看）。

3. 加载定时任务：

       launchctl load ~/Library/LaunchAgents/com.qt.weekly-train.plist

4. 验证已加载：

       launchctl list | grep qt

5. 想立刻测试一次：

       ~/28-终极量化交易系统8.4/scripts/weekly_train.sh

6. 想取消定时：

       launchctl unload ~/Library/LaunchAgents/com.qt.weekly-train.plist

**⚠️ 避坑：**

| 问题 | 解决 |
|---|---|
| 到点没跑 | Mac 睡眠了；系统设置 → 电池 → 勾"防止自动睡眠"；或用 `caffeinate` |
| 脚本权限不够 | `chmod +x 脚本路径` |
| 路径里有中文 launchd 报错 | 把项目放到无中文路径，如 `~/qt/` |
| 想改时间 | 改 plist 里的 `Weekday` 和 `Hour`，然后 `unload` 再 `load` |

---

### 模型自动回传（训练完自动下载）

在训练脚本里加一段，作业完成后自动拉模型：

    # 等作业完成（轮询）
    python3.11 ~/28-终极量化交易系统8.4/scripts/wait_and_pull.py --job-name $JOB_NAME --local-dir ~/28-终极量化交易系统8.4/models/cloud_trained/$JOB_NAME/

> `wait_and_pull.py` 用 ModelArts SDK 轮询作业状态，完成后用 obsutil 拉模型。脚本模板见第三部分。

---

### 模型版本管理（别乱覆盖）

**命名规则：** `模型名_市场_日期_参数摘要.pkl`

例：`lgb_csi300_20260815_nl128_br500.pkl`

**目录结构：**

    ~/28-终极量化交易系统8.4/models/cloud_trained/
    ├── csi300/
    │   ├── 20260801/    # 8月1日那周
    │   ├── 20260808/
    │   └── 20260815/
    ├── csi500/
    └── current -> csi300/20260815/   # 软链接指向"当前在用"的

**切换在用模型：**

    ln -sfn ~/28-终极量化交易系统8.4/models/cloud_trained/csi300/20260815 ~/28-终极量化交易系统8.4/models/cloud_trained/current

**回滚到上周：**

    ln -sfn ~/28-终极量化交易系统8.4/models/cloud_trained/csi300/20260808 ~/28-终极量化交易系统8.4/models/cloud_trained/current

---

### 成本控制与预算

| 训练类型 | 规格 | 时长 | 单次花费 | 频率 | 月花费 |
|---|---|---|---|---|---|
| 周度增量 | 8c32g.cpu | 15 min | ¥0.2 | 4 次/月 | ¥0.8 |
| 月度全量 | 16c64g.cpu | 30 min | ¥0.5 | 1 次/月 | ¥0.5 |
| 季度超参搜索 | 8c32g.cpu × 8 并行 | 60 min | ¥2 | 1 次/季 | ¥0.7 |
| OBS 存储 | — | — | — | — | ¥0.15 |
| SWR 镜像存储 | — | — | — | — | ¥0.1 |
| **合计** | | | | | **约 ¥2.3/月** |

> **想省钱：** 用公共资源池（不要用专属池）、训练完立即释放、`--boost-round` 先小后大、数据增量更新别每次全传。

> **想加速（钱够）：** 用 GPU 规格 `modelarts.bm.gpu.p4`，LightGBM 不吃 GPU 但深度学习模型会快 10 倍。

---

## 第三部分：常用脚本模板

### 3.1 命令行提交训练作业 `scripts/submit_train.py`

> 用控制台点太慢，写个脚本一条命令提交。保存到 `~/28-终极量化交易系统8.4/scripts/submit_train.py`。

```python
"""命令行提交 ModelArts 训练作业（小白版）"""
import argparse, os, time
from huaweicloudsdkcore.auth.credentials import BasicCredentials
from huaweicloudsdkcore.http.http_config import HttpConfig
from huaweicloudsdkmodelarts.v2.region.modelarts_region import ModelArtsRegion
from huaweicloudsdkmodelarts.v2 import (
    ModelArtsClient, CreateTrainingJobRequest, CreateTrainingJobReq,
    AlgorithmsReq, ResourceReq, FlavorsReq, Parameter
)

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--job-name', default=f'qt-{time.strftime("%Y%m%d%H%M")}')
    p.add_argument('--market', default='csi300')
    p.add_argument('--boost-round', type=int, default=500)
    p.add_argument('--num-leaves', type=int, default=128)
    p.add_argument('--learning-rate', type=float, default=0.02)
    p.add_argument('--max-depth', type=int, default=8)
    p.add_argument('--image', default='swr.cn-east-3.myhuaweicloud.com/qt/qt-qlib-trainer:latest')
    p.add_argument('--spec', default='modelarts.bm.8c32g.cpu')
    args = p.parse_args()

    ak, sk = os.environ['HUAWEICLOUD_AK'], os.environ['HUAWEICLOUD_SK']
    cred = BasicCredentials(ak=ak, sk=sk)
    client = ModelArtsClient.new_builder().with_credentials(cred).with_region(
        ModelArtsRegion.value_of("cn-east-3")).build()

    cmd = (f"uv run python ms_strategy/cloud_train/modelscope_train.py "
           f"--data-dir /cache/data/qlib_data/cn_data --market {args.market} "
           f"--start 2015-01-01 --end 2026-08-14 --train-end 2024-12-31 "
           f"--valid-end 2025-06-30 --model lightgbm --num-leaves {args.num_leaves} "
           f"--boost-round {args.boost_round} --learning-rate {args.learning_rate} "
           f"--max-depth {args.max_depth} --output /cache/output")

    req = CreateTrainingJobRequest(
        body=CreateTrainingJobReq(
            metadata=...,        # 按官方 SDK 填
            algorithm=AlgorithmsReq(image=args.image, command=cmd),
            resource=ResourceReq(flavor_id=args.spec, node_count=1),
            # inputs/outputs 挂载 OBS
        )
    )
    resp = client.create_training_job(req)
    print(f"作业已提交: {args.job_name}, ID: {resp.metadata.id}")

if __name__ == '__main__':
    main()
```

> 上面的 `metadata` / `inputs` / `outputs` 字段按华为云 ModelArts SDK V2 文档补全（控制台提交一次后，在作业详情页能看到完整 JSON，照抄即可）。**小白如果嫌麻烦，继续用控制台点也完全没问题。**

**用法：**

    python3.11 ~/28-终极量化交易系统8.4/scripts/submit_train.py --job-name qt-test --market csi300 --boost-round 100

### 3.2 超参搜索 `scripts/grid_search.sh`

```bash
#!/bin/bash
# 网格搜索超参，并行提交多个作业
for nl in 64 128 256; do
  for br in 300 500 1000; do
    for lr in 0.01 0.02 0.05; do
      JOB="qt-grid-nl${nl}-br${br}-lr${lr}-$(date +%Y%m%d%H%M)"
      python3.11 ~/28-终极量化交易系统8.4/scripts/submit_train.py \
        --job-name $JOB --num-leaves $nl --boost-round $br --learning-rate $lr
      sleep 5  # 避免提交太快被限流
    done
  done
done
```

> 27 个作业并行，约 ¥2，跑完挑 IC 最高的那组参数作为下月全量训练参数。

### 3.3 等作业完成并拉模型 `scripts/wait_and_pull.py`

```python
"""轮询作业状态，完成后拉模型到本地"""
import argparse, os, time, subprocess
from huaweicloudsdkcore.auth.credentials import BasicCredentials
from huaweicloudsdkmodelarts.v2 import ModelArtsClient, ShowTrainingJobRequest
from huaweicloudsdkmodelarts.v2.region.modelarts_region import ModelArtsRegion

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--job-id', required=True)
    p.add_argument('--local-dir', required=True)
    p.add_argument('--obs-prefix', default='obs://qt-models/')
    args = p.parse_args()

    ak, sk = os.environ['HUAWEICLOUD_AK'], os.environ['HUAWEICLOUD_SK']
    client = ModelArtsClient.new_builder().with_credentials(BasicCredentials(ak, sk)).with_region(
        ModelArtsRegion.value_of("cn-east-3")).build()

    while True:
        resp = client.show_training_job(ShowTrainingJobRequest(job_id=args.job_id))
        status = resp.status
        print(f"作业状态: {status}")
        if status in ('completed', 'succeeded'):
            os.makedirs(args.local_dir, exist_ok=True)
            subprocess.run(['~/obsutil', 'cp', '-r', '-f', args.obs_prefix, args.local_dir + '/'], shell=True)
            print(f"模型已拉到 {args.local_dir}")
            break
        if status in ('failed', 'killed', 'error'):
            print("作业失败，去控制台看日志")
            break
        time.sleep(60)

if __name__ == '__main__':
    main()
```

---

## 第四部分：Mac 特有避坑总表

| 问题 | 解决 |
|---|---|
| `docker build` 报 `exec format error` | 加 `--platform linux/amd64`；开 Rosetta（第 4 步） |
| Rosetta 开了还是报错 | Docker Desktop 更新到最新；或 `docker buildx create --use` |
| 终端里 `obsutil: command not found` | 用全路径 `~/obsutil`；或检查 `~/.zshrc` 别名 |
| `launchctl load` 报错 | plist 里有中文路径；把项目放无中文路径 |
| 定时任务到点不跑 | Mac 睡眠了；系统设置 → 电池 → 防止自动睡眠 |
| Mac 跑本地小样本很慢 | M 芯片跑 x86 Python 慢；用 `python3.11`（ARM 原生）别用 Rosetta 跑 Python |
| Docker 占磁盘太大 | Docker Desktop → Settings → Resources → Disk image size 调小；或 `docker system prune -a` |
| `uv sync` 装不上依赖 | 确认 Python 3.11；或 `uv sync --python 3.11 --frozen` |
| Keychain 取钥匙报错 | 改回用 `~/.zshrc` 明文方式（第 6 步基础版） |
| OBS 上传慢 | 家宽上行慢；730MB 正常 5–15 分钟；用 `obsutil cp -r -f` 断点续传 |

---

## 第五部分：长期路线图（接下来 3 个月练什么）

| 周次 | 目标 | 具体动作 | 产出 |
|---|---|---|---|
| 第 1 周 | 迁移稳定 | 完成本计划第一部分，跑通 3 次训练 | Mac 链路通 |
| 第 2 周 | 周度自动化 | 配好 launchd 周度训练 + 自动回传 | 每周自动出模型 |
| 第 3–4 周 | 多市场扩展 | 跑通 CSI500、CSI800 | 3 个市场都有模型 |
| 第 5–6 周 | 超参搜索 | 跑一次网格搜索 27 组 | 最优超参组合 |
| 第 7–8 周 | 模型对比 | 不同超参/市场模型 IC 对比 | 选出主力模型 |
| 第 9–10 周 | 增强训练 | 开 TSCV 时序交叉验证多轮 | 更稳健模型 |
| 第 11–12 周 | 实盘对接 | 模型接入选股策略，模拟盘验证 | 选股信号可用 |

---

## 迁移 + 训练计划总耗时

| 阶段 | 做什么 | 耗时 |
|---|---|---|
| 第 1 步 | 确认 Mac 基本信息 | 5 分钟 |
| 第 2 步 | 装 Homebrew | 10 分钟 |
| 第 3 步 | 装 Python + SDK | 15 分钟 |
| 第 4 步 | 装 Docker + Rosetta | 20 分钟 |
| 第 5 步 | 搬项目代码 | 15 分钟 |
| 第 6 步 | 搬 AK/SK | 5 分钟 |
| 第 7 步 | 装 obsutil | 10 分钟 |
| 第 8 步 | 登录 SWR | 5 分钟 |
| 第 9 步 | 重新构建推送镜像 | 30–45 分钟 |
| 第 10 步 | 验证训练跑通 | 30 分钟 |
| **迁移合计** | | **约 2.5 小时** |
| 后续每次训练 | 改参数→提交→看→下载 | 30–60 分钟 |

> 建议迁移分 2 天：第 1 天做 1–8 步（本地环境），第 2 天做 9–10 步（镜像 + 验证）。

---

## Windows vs Mac 差异速查（给看过 Windows 版教程的人）

| 差异点 | Windows | MacBook Pro |
|---|---|---|
| 命令行 | PowerShell | Terminal + zsh |
| 装软件 | `winget install` | `brew install` |
| 钥匙保存 | `setx` 环境变量 | `~/.zshrc` 或 Keychain |
| 读环境变量 | `echo $env:AK` | `echo $HUAWEICLOUD_AK` |
| obsutil | `obsutil_windows_amd64.zip` | `obsutil_darwin_arm64.tar.gz` |
| 解压 | `Expand-Archive` | `tar -xzf` |
| 路径 | `C:\` 反斜杠 + 双引号 | `/Users/你/` 正斜杠 |
| Docker 构建 | `docker build` | `docker build --platform linux/amd64` |
| 定时任务 | 任务计划 | launchd |
| 项目路径 | `E:\各种PY程序\28-...` | `~/28-终极量化交易系统8.4/` |

---

## 下一步

跑通迁移后可以：
1. **配好周度自动训练** — 第二部分"定时训练"
2. **扩到多市场** — CSI500/CSI800
3. **做一次超参搜索** — 第三部分网格搜索脚本
4. **模型接入策略** — 见《云部署协同本地量化交易实施手册_M5Max_20260815.md》
5. **进阶：buildx 多平台构建** — 同时支持 x86 和 ARM 镜像
6. **进阶：用 GPU 规格** — 深度学习模型（如 GRU/Transformer）用 GPU 加速

---

**计划结束。迁移部分卡住了先看"Mac 特有避坑总表"，训练部分按"日常训练工作流"4 步走。云端的东西都没变，你只是在新的 Mac 上重新登录了一下而已。祝顺利！**
