#!/usr/bin/env bash
# ============================================================
# sync_data.sh — 同步本地数据/配置到云机 (首次上云 / 增量更新)
# 在 Mac / WSL / Linux 上跑 (需 rsync + ssh)
# 用法:
#   bash scripts/cloud/sync_data.sh ubuntu@1.2.3.4 [/opt/quant]
# 云机需先开 SSH, 且 /opt/quant 目录存在 (mkdir -p)
# ============================================================
set -euo pipefail

TARGET=${1:?用法: sync_data.sh <user@ip> [remote-root]}
REMOTE_ROOT=${2:-/opt/quant}
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOCAL_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "============================================================"
echo "  同步数据/配置到云机"
echo "  本地: $LOCAL_ROOT"
echo "  云机: $TARGET:$REMOTE_ROOT"
echo "============================================================"

# 远程建目录
ssh "$TARGET" "mkdir -p $REMOTE_ROOT/{data_cache,qlib_data,data,config,configs,reports,trade_plans,trade_instructions,logs}"

# 1. 行情数据 (parquet, 增量)
echo "[1/5] data_cache/ ..."
rsync -az --progress \
  --exclude='__pycache__' \
  "$LOCAL_ROOT/data_cache/" "$TARGET:$REMOTE_ROOT/data_cache/"

# 2. QLib 数据
echo "[2/5] qlib_data/ ..."
rsync -az --progress \
  "$LOCAL_ROOT/qlib_data/" "$TARGET:$REMOTE_ROOT/qlib_data/"

# 3. 业务库 (SQLite, 仅 *.db; 排除 feature_store/cache 大子目录)
echo "[3/5] data/ (仅 *.db) ..."
rsync -az --progress \
  --include='*/' --include='*.db' --exclude='feature_store/*' --exclude='cache/*' --exclude='*' \
  "$LOCAL_ROOT/data/" "$TARGET:$REMOTE_ROOT/data/"

# 4. 配置
echo "[4/5] config/ + configs/ ..."
rsync -az --progress "$LOCAL_ROOT/config/"  "$TARGET:$REMOTE_ROOT/config/"
rsync -az --progress "$LOCAL_ROOT/configs/" "$TARGET:$REMOTE_ROOT/configs/"

# 5. 交易计划/指令 (如需迁移历史)
echo "[5/5] trade_plans/ + trade_instructions/ ..."
rsync -az --progress "$LOCAL_ROOT/trade_plans/"        "$TARGET:$REMOTE_ROOT/trade_plans/"        2>/dev/null || true
rsync -az --progress "$LOCAL_ROOT/trade_instructions/" "$TARGET:$REMOTE_ROOT/trade_instructions/" 2>/dev/null || true

echo ""
echo "============================================================"
echo "  完成。下一步在云机:"
echo "    1. 将 $REMOTE_ROOT/{data_cache,qlib_data} 绑定到 K8s PV (hostPath 或手动 cp 到 PVC)"
echo "    2. 将 $REMOTE_ROOT/{config,configs} 打入镜像 (已 COPY) 或挂载"
echo "============================================================"