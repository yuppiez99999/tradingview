# MacBook Pro (M5 Pro / 48GB / 2TB) 量化环境搭建清单

> 目标机：**16″ MacBook Pro · Apple M5 Pro（18核CPU/20核GPU）· 48GB 统一内存 · 2TB SSD** · Apple 官翻
> 项目：**终极量化交易系统 v8.4**（`E:\各种PY程序\28-终极量化交易系统8.4`）
> 定位：**Mac = 研究/回测机**（实盘/QMT 在 Windows；重训练在云端 ModelArts）
> 编写日期：2026-08-26

---

## 0. 先说结论（为什么这样配）

| 项目 | 你的机器 | 本方案依据 |
|------|---------|-----------|
| 芯片 | M5 Pro（Apple Silicon / arm64） | 科学栈必须**原生 arm64**，否则 numba/scipy 走 Rosetta 慢且易崩 |
| 内存 | 48GB | `settings_mac.yaml` 里 `memory_limit_gb: 64` 是 M5 Max 写的，**你这台要改回 32–40**，留余量给 macOS |
| 硬盘 | 2TB | 放心装多个 venv + qlib 数据集（项目已 292MB，会增长） |
| Python | — | 装 **3.12**（避开 3.14，且 ≥ pyproject 的 `requires-python >=3.10`） |
| 包管理 | — | **Miniforge（conda-forge）优先装 numpy/pandas/numba/xgboost/lightgbm 等原生栈**，纯 pip 装其余；torch 用官方 arm64+MPS 轮子 |
| 研究模式 | — | `config/settings_mac.yaml` 在 Mac 上**自动加载**（检测 Darwin），禁实盘 |

> ⚠️ 项目自带 `scripts/deploy/mac_setup.sh`，但它用 Homebrew+纯 pip 装科学栈、且按 M5 Max 调参。本清单在其基础上**改为 Miniforge 原生 arm64 方案**并适配你的 48GB，更稳。

---

## 1. 开箱后基础确认（5 分钟）

打开「终端」（Spotlight 搜 `terminal`）：

```bash
sysctl -n machdep.cpu.brand_string      # 应含 Apple M5 Pro
sw_vers                                  # ProductVersion ≥ 14
system_profiler SPHardwareDataType | grep Memory   # 应显示 48 GB
```

✅ 三者符合即继续。

---

## 2. 装 Homebrew（Mac 软件管家）

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
# 装完按终端提示把下面两行写入 ~/.zshrc（M 系列默认 /opt/homebrew）
echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zshrc
eval "$(/opt/homebrew/bin/brew shellenv)"
brew --version        # 应显示 Homebrew 4.x
```

> 国内慢可换清华源：`export HOMEBREW_BREW_GIT_REMOTE=https://mirrors.tuna.tsinghua.edu.cn/git/homebrew/brew.git` 后再装。

---

## 3. 装 Miniforge（关键：原生 arm64 Python + conda-forge）

> 用 **Miniforge** 而非官方 Anaconda，默认 conda-forge 源，Apple Silicon 原生包最全。

```bash
cd ~/Downloads
curl -L -o Miniforge3.sh "https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-MacOSX-arm64.sh"
bash Miniforge3.sh          # 一路回车默认装到 ~/miniforge3，按提示初始化 zsh
source ~/.zshrc             # 或重开终端，使 conda 生效
conda --version             # 验证
```

创建研究专用环境（Python 3.12）：

```bash
conda create -n quant38 python=3.12 -y
conda activate quant38
python --version            # Python 3.12.x
which python                # 应指向 ~/miniforge3/envs/quant38/bin/python（arm64 原生）
```

> ⚠️ 验证原生架构：`python -c "import platform; print(platform.machine())"` 应为 `arm64`，不是 `x86_64`。

---

## 4. 用 conda-forge 装「必须原生」的科学栈（A 组）

> 这些包若用纯 pip 在 arm64 上可能拉到 x86 wheel 或编译失败，**务必 conda 装**：

```bash
conda install -y -c conda-forge \
  numpy pandas scipy statsmodels scikit-learn \
  numba xgboost lightgbm \
  cryptography
```

验证：

```bash
python -c "import numpy,pandas,scipy,sklearn,lightgbm,xgboost,numba; print('A组 OK', numpy.__version__)"
```

---

## 5. 用 pip 装其余依赖（B 组，基于 requirements-core.txt）

项目已提供跨平台核心清单 `requirements-core.txt`（已剔除 pyautogui/pywinauto 等 Win 专属）。先把它拷到 Mac 项目根目录，再装：

```bash
conda activate quant38
cd ~/28-终极量化交易系统8.4
pip install --upgrade pip wheel setuptools

# 装核心清单（不含 torch，下一步单独装）
pip install -r requirements-core.txt
```

> 若某包装不上，大概率是它依赖 A 组里的原生包——**确认第 4 步已先装 A 组**。

---

## 6. 单独装 PyTorch（官方 arm64 + MPS 轮子）

> Mac 上 torch 走 **MPS（Metal）后端**加速，不用 CUDA。用官方预编译 arm64 轮子：

```bash
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
```

验证 MPS 可用：

```bash
python -c "import torch; print(torch.__version__); print('MPS 可用:', torch.backends.mps.is_available())"
```

> 输出 `MPS 可用: True` 即成功。注意：torch 在 Mac 是 **CPU/MPS 版**，不能做 CUDA 训练；你的重训练本就在云端，无影响。

---

## 7. 搬项目代码到 Mac

项目代码从 Windows 拷到 Mac（数据已在云端 OBS，可不搬，或连同小样本一起搬）：

```bash
# 方式：U 盘/exFAT 或 git clone；中文路径用引号
mkdir -p ~/"28-终极量化交易系统8.4"
# 示例（U 盘方式）：
# cp -R "/Volumes/U盘名/28-终极量化交易系统8.4" ~/
cd ~/"28-终极量化交易系统8.4"
ls        # 应见 pyproject.toml、ms_strategy/、qlib/、config/ 等
```

> ⚠️ **不要**直接 `pip install -e .`（pyproject 含 pywinauto/pyautogui，Mac 会失败）。用 `requirements-core.txt` 已规避。

---

## 8. 配置研究模式环境变量

`settings_mac.yaml` 在 Mac 上会自动检测 Darwin 加载，**但显式设环境变量更稳**：

```bash
cat >> ~/.zshrc << 'EOF'

# === 终极量化交易系统 — Mac 研究模式 ===
export QUANT_RESEARCH_MODE=1
export NO_PROXY="push2his.eastmoney.com,push2.eastmoney.com,eastmoney.com,sinajs.cn,sina.com.cn"
EOF
source ~/.zshrc
```

确认配置存在：

```bash
ls config/settings_mac.yaml && echo "研究模式配置存在 ✅"
```

> 该文件已禁用：`disable_live_trading / disable_hedge_execution / disable_gui_automation`，并白名单只允许 `--backtest / --train-enhanced / --report / --kondratiev` 等研究模式。**Mac 上严禁 `--live / --ai-hedge / --hedge-rebalance` 等实盘指令。**

---

## 9. 适配你的 48GB：微调 settings_mac.yaml 的 ML 参数

官方文件是按 M5 Max（128GB）写的，你这台 48GB，建议改两处（其余保持）：

```yaml
ml_training:
  n_jobs: 10                 # 18核CPU，留余给系统，原值 8 可不动；48GB 下用 10 更充分
  memory_limit_gb: 32        # 原值 64（为128GB写），改 32 给 macOS 留 16GB 余量
```

> 编辑 `config/settings_mac.yaml` 对应两行即可。别动 `disable_live_trading: true` 等安全锁。

---

## 10. 运行 P0 自检（研究模式）

```bash
conda activate quant38
cd ~/"28-终极量化交易系统8.4"
python -c "from utils.system_check import run_system_check; r = run_system_check(skip_datasource=True); print('退出码:', r.exit_code)"
```

> Wind/QMT/TDX 相关告警在 Mac 研究模式下**属正常**（已禁用），只要核心依赖 OK 即可。

跑一个真实研究命令验证链路：

```bash
python main.py --kondratiev --report       # 康波周期分析（白名单内，纯研究）
# 或
python main.py --backtest                  # 走 walk_forward 回测
```

---

## 11.（可选）云端训练工具链（你已有 Windows 链路，按需）

若要 Mac 直接给华为云 ModelArts 发训练指令，按项目文档 `docs/云端部署迁移MacBookPro_后续训练计划_20260816.md` 装：brew 装 docker（开 Rosetta）、obsutil（darwin_arm64）、华为云 SDK。**关键差异**：`docker build` 必须加 `--platform linux/amd64`（云端是 x86）。此部分与本地研究环境独立，可暂缓。

---

## 12. 最终验证清单

```bash
conda activate quant38
python - <<'PY'
import platform, torch, numpy, pandas, sklearn, lightgbm, xgboost, numba
print("架构:", platform.machine())          # arm64
print("torch:", torch.__version__, "MPS:", torch.backends.mps.is_available())
print("numpy:", numpy.__version__)
print("pandas:", pandas.__version__)
print("sklearn:", sklearn.__version__)
print("lightgbm:", lightgbm.__version__)
print("xgboost:", xgboost.__version__)
print("numba:", numba.__version__)
import os
print("研究模式:", os.environ.get("QUANT_RESEARCH_MODE"))
PY
```

✅ 全绿即搭建完成。

---

## 避坑速查

| 现象 | 原因/解决 |
|------|----------|
| `pip install -e .` 失败 | pyproject 含 pywinauto/pyautogui（Win 专属）；改用 `requirements-core.txt` |
| numba/scipy import 慢或崩 | 纯 pip 装到 x86 wheel；删了用 `conda install -c conda-forge` 重装 A 组 |
| torch 报 CUDA 错 | Mac 无 CUDA，用 MPS；确认装的是 `/whl/cpu` 轮子 |
| `platform.machine()` 显示 x86_64 | 用 Rosetta 跑了终端；确认用的是 Miniforge arm64 的 python |
| 内存交换卡顿 | `memory_limit_gb` 调回 32；关掉无关 app |
| 实盘命令误跑 | settings_mac.yaml 已锁；真要下单选 Windows 端 |

---

## 和你选购结论的对应

- 你买的是 **M5 Pro / 48GB / 2TB 官翻** → 本方案第 3–4 步用 Miniforge arm64 原生栈，第 9 步按 48GB 调参，第 6 步 torch MPS 适配 Apple Silicon。
- 之前我对 3 万预算的推荐是「14″ M4 Pro 48GB/1TB」；你这台 **16″ M5 Pro + 2TB** 实际更优（芯片更新一代、硬盘翻倍），且仍在预算内。搭建命令完全适用，无需降级。

---
*本清单基于项目自带 `scripts/deploy/mac_setup.sh`、`config/settings_mac.yaml`、`requirements-core.txt`、`docs/云端部署迁移MacBookPro_后续训练计划_20260816.md` 核对生成。*
