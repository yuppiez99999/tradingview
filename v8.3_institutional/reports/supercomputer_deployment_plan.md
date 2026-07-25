# 500万组合部署国家超级计算中心可落地方案

**版本**：v1.0  
**日期**：2026-07-17  
**视角**：顶级对冲基金技术架构  
**目标**：年化收益 ≥8%，最大回撤 ≤15%  
**资金规模**：500 万人民币  
**当前状态**：已实现全自动交易闭环（盘前计划 → 盘中决策 → 盘后总结）

---

## 一、执行摘要

本方案将现有 500 万量化交易系统从**本地单机**升级为**超算中心 + 云服务器**的混合架构。

**核心原则**
- **超算只做研究**：回测、参数优化、信号生成、风险模拟
- **交易服务器只做执行**：下单、风控、监控、报单
- **不直连交易所**：通过持牌券商柜台系统执行，符合监管要求

**预期效果**
- 回测速度提升 **100-1000 倍**
- 参数优化覆盖 **10 倍以上** 策略空间
- 交易执行延迟 **<50ms**
- 年化收益目标 **≥8%**，最大回撤 **≤15%**

---

## 二、当前系统诊断

### 2.1 已有能力（可直接复用）

| 模块 | 文件 | 状态 |
|------|------|------|
| 收盘报告生成 | `run_daily_eod.py` | ✅ 已实现 |
| LLM 盘中决策 | `llm_intraday_decision_engine.py` | ✅ 已实现 |
| 次日计划生成 | `apply_llm_decisions_to_plan.py` | ✅ 已实现 |
| 开盘前摘要 | `generate_pre_market_summary.py` | ✅ 已实现 |
| 监控名单 | `watchlist` | ✅ 已实现 |
| 风控系统 | KillSwitch + 止损线 | ✅ 已实现 |
| 对冲系统 | IF 期货 + Put 保护 | ✅ 已实现 |

### 2.2 当前瓶颈

| 瓶颈 | 影响 | 超算可解决 |
|------|------|-----------|
| 单机回测慢 | 参数优化空间小 | ✅ 100-1000 倍加速 |
| 无并行计算 | 多策略回测耗时 | ✅ 分布式 Spark/Dask |
| 本地存储有限 | 历史数据容量受限 | ✅ PB 级并行文件系统 |
| 单点故障 | 服务器宕机导致停摆 | ✅ 高可用集群 |
| 延迟不确定 | 网络波动影响执行 | ✅ 交易服务器专线 |

---

## 三、超级计算中心架构设计

### 3.1 整体架构图

```
┌─────────────────────────────────────────────────────────────┐
│                    国家超级计算中心                           │
│  ┌───────────────────────────────────────────────────────┐  │
│  │              计算节点群 (LSF/PBS 调度)                  │  │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐   │  │
│  │  │ 回测节点     │  │ 优化节点     │  │ 风控节点     │   │  │
│  │  │ 32核 × 20   │  │ 64核 × 10   │  │ 32核 × 5    │   │  │
│  │  └─────────────┘  └─────────────┘  └─────────────┘   │  │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐   │  │
│  │  │ 数据节点     │  │ LLM推理节点  │  │ 可视化节点   │   │  │
│  │  │ 512GB × 10  │  │ A100 × 4    │  │ 32核 × 3    │   │  │
│  │  └─────────────┘  └─────────────┘  └─────────────┘   │  │
│  └───────────────────────────────────────────────────────┘  │
│  ┌───────────────────────────────────────────────────────┐  │
│  │              并行文件系统 (Lustre/GPFS)                 │  │
│  │  - 历史行情数据 (10年+ tick 级)                        │  │
│  │  - 回测结果库                                          │  │
│  │  - 模型文件仓库                                        │  │
│  │  - 交易计划归档                                        │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                           ↓ 计算结果/信号/计划
                   ┌───────────────────────┐
                   │    rsync/scp/WebSocket  │
                   └───────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────────┐
│                 交易执行节点（云服务器/本地）                  │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  run_daily_eod.py                                    │  │
│  │  llm_intraday_decision_engine.py                     │  │
│  │  daily_trade_executor.py                             │  │
│  │  risk_monitor.py                                     │  │
│  └───────────────────────────────────────────────────────┘  │
│                           ↓ 交易指令                         │
│                  ┌───────────────────────┐                  │
│                  │    持牌券商柜台系统     │                  │
│                  │  XTP / CTP / 文件扫单  │                  │
│                  └───────────────────────┘                  │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 超算资源配置申请

| 资源类型 | 配置要求 | 用途 | 申请建议 |
|----------|----------|------|----------|
| **计算节点** | 32-64 核 × 50 节点 | 并行回测、参数优化 | 申请科研/教育配额 |
| **内存** | 512GB-1TB × 10 节点 | 大数据加载、内存计算 | 按需申请 |
| **存储** | 10-50 TB（Lustre/GPFS） | 历史数据、回测结果 | 申请项目存储 |
| **GPU 节点** | A100 40GB × 4 卡 | LLM 推理、深度学习 | 可选，初期可不用 |
| **网络** | InfiniBand / 10GbE | 节点间通信 | 超算标配 |
| **调度系统** | LSF / PBS / SLURM | 任务排队调度 | 超算提供 |

**申请渠道**
- 国家超算长沙中心、天津中心、深圳中心等
- 通常对**科研/教育项目**免费或低价开放
- 申请时强调：**金融量化策略研究、风险评估、并行计算**

---

## 四、超算端部署（研究层）

### 4.1 环境准备

```bash
#!/bin/bash
# 超算通常提供模块化环境
module load python/3.8
module load spark/3.0
module load dask/2022.0
module load cuda/11.8

# 创建项目目录
mkdir -p /lustre/your_project/quant
cd /lustre/your_project/quant

# 创建虚拟环境
python -m venv quant_env
source quant_env/bin/activate

# 安装依赖
pip install numpy pandas scipy scikit-learn
pip install pyspark dask distributed
pip install akshare wind-api ifind-api
pip install lightgbm xgboost
```

### 4.2 数据存储架构

```python
# 目录结构（基于 Lustre 并行文件系统）
/lustre/your_project/quant/
├── data/
│   ├── raw/
│   │   ├── daily/           # 日线行情（10年+）
│   │   ├── tick/            # Tick 级行情（如有）
│   │   ├── fundamentals/    # 财务数据
│   │   └── alternative/     # 另类数据（舆情、宏观）
├── signals/                 # LLM/模型生成的交易信号
├── backtest/                # 回测结果
│   ├── params/              # 参数优化结果
│   └── reports/             # 回测报告
├── models/                  # 训练好的模型
├── plans/                   # 交易计划文件
└── logs/                    # 运行日志
```

### 4.3 分布式回测框架（Spark/Dask）

```python
# distributed_backtest.py
from dask.distributed import Client
from dask_jobqueue import LSFCluster
import pandas as pd
import json

def backtest_strategy(params, data_path):
    """单组参数回测"""
    # 加载数据
    df = pd.read_parquet(data_path)
    
    # 应用策略逻辑
    signals = generate_signals(df, **params)
    returns = calculate_returns(df, signals)
    
    # 计算指标
    sharpe = calculate_sharpe(returns)
    max_dd = calculate_max_drawdown(returns)
    annual_return = (1 + returns.mean()) ** 252 - 1
    
    return {
        'params': params,
        'sharpe': sharpe,
        'max_drawdown': max_dd,
        'annual_return': annual_return
    }

# 提交 LSF 作业
cluster = LSFCluster(
    cores=32,
    memory='64GB',
    walltime='02:00',
    queue='quant',
    project='your_project_id'
)

client = Client(cluster)

# 参数网格
param_grid = {
    'lookback': [20, 40, 60],
    'threshold': [0.01, 0.02, 0.03],
    'stop_loss': [0.05, 0.10, 0.15]
}

# 生成所有参数组合
from itertools import product
params_list = [dict(zip(param_grid.keys(), p)) 
               for p in product(*param_grid.values())]

# 并行回测
futures = [client.submit(backtest_strategy, p, '/lustre/.../data.parquet') 
           for p in params_list]

results = client.gather(futures)

# 保存结果
df_results = pd.DataFrame(results)
df_results.to_csv('/lustre/.../backtest_results.csv', index=False)
```

### 4.4 LSF 作业脚本

```bash
#!/bin/bash
#BSUB -J backtest_20260720
#BSUB -n 32                    # 32 核
#BSUB -R "span[hosts=1]"       # 单节点
#BSUB -M 64GB                  # 内存 64GB
#BSUB -q quant                 # 队列
#BSUB -o /lustre/your_project/quant/logs/output_%J.log
#BSUB -e /lustre/your_project/quant/logs/error_%J.log

cd /lustre/your_project/quant
source quant_env/bin/activate

# 1. 同步最新交易计划
scp user@trading-server:/home/quant/plans/trade_plan_20260720.json ./plans/

# 2. 运行回测优化
python distributed_backtest.py --plan ./plans/trade_plan_20260720.json

# 3. 生成优化后的交易计划
python generate_optimized_plan.py --date 2026-07-20

# 4. 同步回交易服务器
scp ./plans/trade_plan_20260720_optimized.json user@trading-server:/home/quant/plans/

# 5. 生成 LLM 决策
python llm_intraday_decision_engine.py --mode eod --date 2026-07-20
```

**提交作业**
```bash
bsub < job_backtest.lsf
```

**查询作业状态**
```bash
bjobs                    # 查看所有作业
bpeek <jobid>           # 查看作业输出
bkill <jobid>           # 终止作业
```

---

## 五、交易执行节点部署（执行层）

### 5.1 硬件配置建议

| 组件 | 配置 | 说明 |
|------|------|------|
| **CPU** | 8 核以上 | Intel Xeon / AMD EPYC |
| **内存** | 32GB+ | 缓存行情数据 |
| **存储** | 500GB NVMe SSD | 系统/日志/缓存 |
| **网络** | 10GbE | 低延迟行情接入 |
| **操作系统** | Ubuntu 22.04 LTS | 长期支持，内核 5.15+ |

**部署位置选择**
- **最优**：交易所托管机房（延迟 <1ms）
- **次优**：云服务器同城节点（延迟 1-5ms）
- **可用**：本地服务器 + 专线（延迟 10-30ms）

### 5.2 软件环境

```bash
# 基础环境
sudo apt update
sudo apt install -y python3.9 python3.9-venv build-essential

# 创建虚拟环境
python3.9 -m venv ~/quant_trading
source ~/quant_trading/bin/activate

# 安装依赖
pip install numpy pandas scipy
pip install akshare wind-api ifind-api
pip install requests websockets
pip install schedule python-crontab
```

### 5.3 核心执行脚本

```python
# daily_trade_executor.py
import json
import time
import logging
from datetime import datetime

class TradeExecutor:
    def __init__(self, plan_file):
        self.plan = self.load_plan(plan_file)
        self.broker = self.init_broker()
        self.risk_engine = RiskEngine()
        
    def execute_morning_session(self):
        """上午盘执行（09:30-11:30）"""
        orders = self.plan['execution_plan']['morning_orders']
        for order in orders:
            if self.risk_engine.check(order):
                self.broker.send_order(order)
                logging.info(f"Sent order: {order}")
            time.sleep(1)  # 避免报单过快
    
    def execute_afternoon_session(self):
        """下午盘执行（13:00-14:30）"""
        orders = self.plan['execution_plan']['afternoon_orders']
        for order in orders:
            if self.risk_engine.check(order):
                self.broker.send_order(order)
                logging.info(f"Sent order: {order}")
            time.sleep(1)
    
    def execute_hedge(self):
        """执行对冲端（期货/期权）"""
        hedge_orders = self.plan.get('futures_options_hedge', {}).get('orders', [])
        for order in hedge_orders:
            if self.risk_engine.check(order):
                self.broker.send_order(order)
                logging.info(f"Sent hedge order: {order}")

# 定时调度
if __name__ == '__main__':
    executor = TradeExecutor('trade_plan_20260720.json')
    
    # 上午盘
    executor.execute_morning_session()
    
    # 下午盘
    executor.execute_afternoon_session()
    
    # 对冲
    executor.execute_hedge()
```

### 5.4 Systemd 服务配置

```ini
# /etc/systemd/system/quant-executor.service
[Unit]
Description=Quant Trading Executor
After=network.target

[Service]
Type=simple
User=quant
WorkingDir=/home/quant
ExecStart=/home/quant/run_trading_loop.sh
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
```

```bash
# /home/quant/run_trading_loop.sh
#!/bin/bash
source ~/quant_trading/bin/activate

while true; do
    date=$(date +%Y-%m-%d)
    
    # 检查是否为交易日
    if is_trading_day(date); then
        # 盘前执行
        python run_daily_eod.py --date $date
        
        # 等待开盘
        sleep 1800  # 30分钟后检查
        
        # 盘中执行
        python daily_trade_executor.py --date $date
        
        # 盘后执行
        python run_daily_eod.py --date $date
    else
        sleep 3600  # 非交易日每小时检查一次
    
    sleep 60
done
```

**启用服务**
```bash
sudo systemctl enable quant-executor
sudo systemctl start quant-executor
sudo systemctl status quant-executor
```

---

## 六、数据同步方案

### 6.1 超算 → 交易服务器（文件同步）

```python
# sync_plans.py（超算端）
import subprocess
import glob
import os

def sync_to_trading_server():
    """将交易计划同步到交易服务器"""
    plans = glob.glob('/lustre/your_project/quant/plans/trade_plan_*.json')
    
    for plan in plans:
        # 同步到交易服务器
        subprocess.run([
            'scp',
            plan,
            'quant@trading-server:/home/quant/plans/'
        ])
        
        # 同时同步 LLM 决策
        decision_file = plan.replace('trade_plan', 'llm_decisions').replace('.json', '_decisions.json')
        if os.path.exists(decision_file):
            subprocess.run([
                'scp',
                decision_file,
                'quant@trading-server:/home/quant/decisions/'
            ])
```

### 6.2 交易服务器 → 超算（数据回传）

```bash
# 交易服务器定期上传交易日志
0 23 * * 1-5 /home/quant/sync_logs_to_hpc.sh

# sync_logs_to_hpc.sh 内容
#!/bin/bash
DATE=$(date +%Y-%m-%d)
scp /home/quant/logs/trade_${DATE}.log user@hpc:/lustre/your_project/quant/logs/
scp /home/quant/reports/daily_pnl_report_${DATE}.json user@hpc:/lustre/your_project/quant/reports/
```

### 6.3 WebSocket 实时推送（可选）

```python
# 超算端：实时推送信号
import asyncio
import websockets
import json

async def push_signals():
    uri = "ws://trading-server:8765/signals"
    async with websockets.connect(uri) as ws:
        while True:
            signal = generate_signal()  # 生成交易信号
            await ws.send(json.dumps(signal))
            await asyncio.sleep(1)  # 每秒推送

# 交易服务器端：接收信号并执行
async def receive_signals():
    async with websockets.serve(handler, "0.0.0.0", 8765):
        await asyncio.Future()  # 永久运行
```

---

## 七、券商对接方案

### 7.1 券商选择标准

| 券商 | 接口类型 | 延迟 | 门槛 | 推荐度 |
|------|----------|------|------|--------|
| **华泰证券** | XTP API | <10ms | 50 万 | ⭐⭐⭐⭐⭐ |
| **华宝证券** | LTS API | <10ms | 50 万 | ⭐⭐⭐⭐⭐ |
| **中信证券** | 自有 API | <5ms | 100 万 | ⭐⭐⭐⭐ |
| **招商证券** | 文件扫单 | 分钟级 | 无 | ⭐⭐⭐ |

**推荐**：华泰证券 XTP API 或华宝证券 LTS API

### 7.2 XTP API 接入示例

```python
# xtp_broker.py
from xtp import XTPApi

class XTPBroker:
    def __init__(self):
        self.api = XTPApi()
        self.session_id = None
        
    def login(self):
        """登录 XTP"""
        self.session_id = self.api.login(
            server_ip='120.27.163.89',
            server_port=17001,
            user_id='your_account',
            password='your_password'
        )
    
    def send_order(self, order):
        """发送订单"""
        xtp_order = {
            'order_xtp_id': 1,
            'order_client_id': 2,
            'ticker': order['code'],
            'market': 1 if order['code'].startswith('6') else 2,
            'price': order['price'],
            'quantity': order['quantity'],
            'side': 1 if order['action'] == 'buy' else 2,
            'price_type': 1,  # 限价单
            'business_type': 0  # 股票
        }
        self.api.insert_order(xtp_order)
    
    def query_position(self):
        """查询持仓"""
        return self.api.query_position()
    
    def query_asset(self):
        """查询资金"""
        return self.api.query_asset()
```

### 7.3 文件扫单（最简单）

```python
# file_based_executor.py
import pandas as pd
import os
import time

class FileBasedExecutor:
    def __init__(self, watch_dir='/home/quant/orders'):
        self.watch_dir = watch_dir
        os.makedirs(watch_dir, exist_ok=True)
    
    def generate_csv(self, orders):
        """生成券商可识别的 CSV 文件"""
        df = pd.DataFrame(orders)
        df['指令类型'] = '买入'
        df['委托方式'] = '限价委托'
        
        filename = f"{self.watch_dir}/orders_{time.strftime('%Y%m%d_%H%M%S')}.csv"
        df.to_csv(filename, index=False, encoding='gbk')
        return filename
    
    def monitor_and_execute(self):
        """监控目录并执行"""
        processed = set()
        while True:
            files = os.listdir(self.watch_dir)
            for f in files:
                if f.endswith('.csv') and f not in processed:
                    filepath = os.path.join(self.watch_dir, f)
                    # 券商系统自动扫描此目录
                    processed.add(f)
            time.sleep(60)
```

---

## 八、风险控制与合规

### 8.1 三级熔断机制

| 级别 | 触发条件 | 响应动作 | 执行者 |
|------|----------|----------|--------|
| **L0 正常** | 无异常 | 正常交易 | 系统自动 |
| **L1 警告** | 单日亏损 >2% | 暂停新建仓，仅允许减仓 | LLM 决策引擎 |
| **L2 警惕** | 单日亏损 >5% | 全面减仓，IF 加仓对冲 | 人工 + LLM |
| **L3 熔断** | 单日亏损 >10% | 停止所有交易，仅保留对冲 | 强制系统 |

```python
# risk_control.py
class RiskEngine:
    def __init__(self):
        self.level = 0
        self.daily_pnl = 0.0
        
    def check(self, order):
        """检查订单是否通过风控"""
        if self.level == 3:
            if order['action'] != 'reduce':
                return False
        
        if self.level >= 1 and order['action'] == 'buy':
            return False
            
        return True
    
    def update_level(self, daily_pnl_pct):
        """更新风险级别"""
        if daily_pnl_pct <= -0.10:
            self.level = 3
        elif daily_pnl_pct <= -0.05:
            self.level = 2
        elif daily_pnl_pct <= -0.02:
            self.level = 1
        else:
            self.level = 0
```

### 8.2 合规要求

| 要求 | 说明 | 实现方式 |
|------|------|----------|
| **持牌券商** | 必须通过券商下单 | XTP API / 文件扫单 |
| **程序化交易备案** | 向交易所备案 | 券商协助办理 |
| **交易日志** | 完整记录所有订单 | 自动保存 5 年以上 |
| **最大持仓限制** | 单票不超过总资金 10% | 系统自动检查 |
| **单日亏损限制** | 单日不超过总资金 2% | 熔断机制 |
| **数据安全** | 交易数据本地化 | 超算 + 本地双备份 |

### 8.3 网络安全

```python
# security.py
import ssl
import hashlib
from cryptography.fernet import Fernet

class SecureChannel:
    def __init__(self):
        self.key = Fernet.generate_key()
        self.cipher = Fernet(self.key)
    
    def encrypt_signal(self, signal):
        """加密交易信号"""
        data = json.dumps(signal).encode()
        return self.cipher.encrypt(data)
    
    def decrypt_signal(self, encrypted):
        """解密交易信号"""
        return json.loads(self.cipher.decrypt(encoded))
    
    def verify_checksum(self, data, checksum):
        """校验数据完整性"""
        return hashlib.sha256(data).hexdigest() == checksum
```

---

## 九、实施路线图

### Phase 1：超算环境搭建（2-4 周）

| 任务 | 耗时 | 负责方 |
|------|------|--------|
| 申请超算账号 | 1-2 周 | 用户 |
| 环境配置（Python/Spark/Dask） | 3-5 天 | 用户 |
| 数据上传（历史行情） | 3-5 天 | 用户 |
| 部署分布式回测框架 | 1 周 | 用户 |
| 测试 LSF 作业提交 | 2-3 天 | 用户 |

**交付物**
- 超算环境可用
- 数据上传完成
- 分布式回测通过测试

### Phase 2：交易服务器部署（1-2 周）

| 任务 | 耗时 | 负责方 |
|------|------|--------|
| 云服务器采购 | 2-3 天 | 用户 |
| 系统安装（Ubuntu） | 1 天 | 用户 |
| 交易执行脚本部署 | 3-5 天 | 用户 |
| 券商 API 对接 | 1-2 周 | 券商 + 用户 |
| 测试下单流程 | 3-5 天 | 用户 |

**交付物**
- 交易服务器运行
- 券商 API 连通
- 模拟盘测试通过

### Phase 3：连接与同步（1 周）

| 任务 | 耗时 | 负责方 |
|------|------|--------|
| 配置 SSH 密钥免密登录 | 2-3 天 | 用户 |
| 部署 rsync/scp 同步脚本 | 2-3 天 | 用户 |
| 测试超算 → 交易服务器数据流 | 2-3 天 | 用户 |
| 测试交易服务器 → 超算日志回传 | 2-3 天 | 用户 |

**交付物**
- 双向数据流打通
- 同步延迟 <1 分钟

### Phase 4：实盘切换（1-2 周）

| 任务 | 耗时 | 负责方 |
|------|------|--------|
| 小仓位模拟盘验证（1-2 周） | 2 周 | 用户 |
| 风控系统压力测试 | 3-5 天 | 用户 |
| 程序化交易备案 | 1-2 周 | 券商 |
| 正式切换实盘 | 1 天 | 用户 |

**交付物**
- 模拟盘稳定运行
- 实盘交易开始

---

## 十、成本估算

### 10.1 超算成本

| 项目 | 费用 | 说明 |
|------|------|------|
| 超算资源申请 | **免费-数万/年** | 科研/教育项目通常免费 |
| 数据存储 | 已包含 | 超算标配 |
| 网络带宽 | 已包含 | 超算标配 |

**说明**：国家超算中心对**科研、教育类项目**通常免费开放，或仅收取少量电费/管理费（每年几千到几万）。

### 10.2 交易服务器成本

| 项目 | 费用 | 说明 |
|------|------|------|
| 云服务器（2核4G） | 300-500 元/月 | 阿里云/腾讯云 |
| 带宽（10Mbps） | 200-500 元/月 | 按流量计费 |
| 存储（500GB SSD） | 50-100 元/月 | 对象存储 |

**小计**：约 **600-1500 元/月**

### 10.3 券商费用

| 项目 | 费用 | 说明 |
|------|------|------|
| 程序化交易权限 | 免费 | 需资金门槛 50-100 万 |
| API 接口费 | 免费 | 部分券商收取年费 |
| 交易佣金 | 万 2-万 3 | 与普通账户相同 |

### 10.4 总成本

| 周期 | 成本 | 备注 |
|------|------|------|
| 首年 | 1-5 万 | 含服务器、可能的超算管理费 |
| 第二年+ | 1-3 万/年 | 服务器 + 维护 |

---

## 十一、风险与应对

| 风险 | 概率 | 影响 | 应对措施 |
|------|------|------|----------|
| 超算申请被拒 | 中 | 无法使用超算 | 方案 B：租用云 HPC |
| 网络延迟过高 | 低 | 下单慢 | 选择同城云服务器 |
| 券商 API 故障 | 中 | 无法下单 | 备用：文件扫单 |
| 超算 downtime | 低 | 无法回测 | 本地备份 + 降级方案 |
| 数据同步失败 | 中 | 计划未送达 | 重试机制 + 告警 |
| 风控系统失效 | 低 | 极端亏损 | 多级熔断 + 人工监控 |

---

## 十二、备选方案（Plan B）

### 12.1 方案 A：超算 + 本地交易（推荐）

- **超算**：回测、优化、信号生成
- **本地服务器**：执行、风控、监控
- **优点**：延迟最低、控制力最强
- **缺点**：需要本地硬件投入

### 12.2 方案 B：纯云服务器（最简单）

- **云服务器**：回测 + 执行一体化
- **无需超算**
- **优点**：部署简单、成本低
- **缺点**：计算能力有限

### 12.3 方案 C：云 HPC + 云交易（折中）

- **云 HPC**：阿里云/腾讯云高性能计算实例
- **云服务器**：执行节点
- **优点**：弹性扩展、无需申请超算
- **缺点**：成本高于方案 A

---

## 十三、关键代码与脚本清单

| 脚本 | 位置 | 用途 |
|------|------|------|
| `distributed_backtest.py` | 超算 | 分布式回测 |
| `job_backtest.lsf` | 超算 | LSF 作业脚本 |
| `daily_trade_executor.py` | 交易服务器 | 订单执行 |
| `xtp_broker.py` | 交易服务器 | 券商 API 封装 |
| `file_based_executor.py` | 交易服务器 | 文件扫单 |
| `sync_plans.py` | 超算 | 同步到交易服务器 |
| `sync_logs_to_hpc.sh` | 交易服务器 | 日志回传 |
| `risk_control.py` | 交易服务器 | 风控引擎 |
| `run_daily_eod.py` | 交易服务器 | 盘后闭环 |
| `llm_intraday_decision_engine.py` | 交易服务器 | 盘中决策 |
| `quant-executor.service` | 交易服务器 | Systemd 服务 |

---

## 十四、下一步行动

**立即执行**
1. [ ] 访问国家超算中心官网（如天津、长沙、深圳），提交项目申请
2. [ ] 联系华泰证券/华宝证券客户经理，开通 XTP/LTS 程序化交易权限
3. [ ] 采购云服务器（2核4G，上海/深圳节点）

**本周完成**
4. [ ] 超算环境配置
5. [ ] 数据上传（历史行情）
6. [ ] 交易服务器部署

**两周内完成**
7. [ ] 分布式回测框架测试
8. [ ] 券商 API 对接
9. [ ] 模拟盘验证

**一个月内**
10. [ ] 实盘切换

---

## 十五、参考案例

### 15.1 国家超算长沙中心金融方案

> "国家超级计算长沙中心与天阳科技、湖南大学信科院、湖南大学金统院联合推出金融级智能算力全栈方案。该解决方案以国家超级计算长沙中心的高性能弹性算力为底座，聚焦金融行业场景，深度融合 DeepSeek 大模型的场景化能力，打造'算力+模型+场景'三位一体解决方案，助力金融机构实现秒级算力扩容、全链路数据合规与低门槛 AI 应用部署。"

来源：[国家超算长沙中心](https://nscc.hnu.edu.cn/info/1003/3016.htm)

### 15.2 中科院超算金融大数据平台

> "基于超级计算'Explane'系统，建立了金融量化大数据平台。关键贡献包括：为 HPC 系统构建高效存储机制；在 HPC 系统上自动部署 Spark 和 Dask 分布式计算框架；实现大规模指标计算、大规模策略回测和分布式超参数调优。"

来源：Sun et al., "Financial Quantitative Big Data Platform based on High Performance Computing", IEEE CSE 2019

### 15.3 城堡证券技术架构

> "城堡证券聚集了来自应用数学、计算机科学、生物信息学等 40 多个领域的 260 多名博士，他们开发的数学模型支撑着三大技术优势：高频交易（HFT）通过算法在毫秒内完成订单匹配；智能对冲与库存管理；订单流分析。"

来源：[网易财经](https://www.163.com/dy/article/KTGQ7FSD05568W0A.html)

---

**报告生成时间**：2026-07-17  
**下次更新**：2026-07-20（实盘部署后）
