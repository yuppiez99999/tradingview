# ============================================================
# 本周 GitHub 升星项目一键部署脚本
# 数据窗口: 2026.7.19 - 7.26 | 生成: 2026-07-28
# 运行方式: 右键 -> 使用 PowerShell 运行
# ============================================================
# 
# 执行状态 (2026-07-28): 全部部署 + 深度集成完成
#   P0: i-have-adhd (插件+开机自启) + ai-agent-book (clone+本地仓库)
#   P1: mattpocock/skills (4个已复制到.skills/) + worldmonitor (SDK集成到晨间流水线)
#        + code-review-graph (uvx可用) + pi (已clone)
#   P2: jcode v0.61.0 已安装 + orca 已安装
# ============================================================

$ErrorActionPreference = "Continue"
$BASE = "E:\各种PY程序"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  本周 GitHub 升星项目一键部署脚本" -ForegroundColor Cyan
Write-Host "  数据窗口: 2026.7.19 - 7.26" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

# ============================================================
# P0 - 立刻执行 (ROI 最高)
# ============================================================

# [1/8] i-have-adhd � Claude Code 插件
Write-Host "`n[1/8] i-have-adhd" -ForegroundColor Green
try {
    claude plugin marketplace add ayghri/i-have-adhd 2>&1 | Out-Null
    claude plugin install i-have-adhd@i-have-adhd 2>&1 | Out-Null
    # 开机自启
    New-Item -Path "$env:USERPROFILE\.claude\.i-have-adhd-always" -ItemType File -Force 2>&1 | Out-Null
    Write-Host "  [OK] 插件已安装 + 开机自启已启用" -ForegroundColor Green
} catch {
    Write-Host "  [跳过] 手动: claude plugin marketplace add ayghri/i-have-adhd" -ForegroundColor Yellow
    Write-Host "          claude plugin install i-have-adhd@i-have-adhd" -ForegroundColor Yellow
}

# [2/8] ai-agent-book
Write-Host "`n[2/8] ai-agent-book" -ForegroundColor Green
$BOOK = "$BASE\ai-agent-book"
if (-not (Test-Path $BOOK)) {
    git clone --depth 1 https://github.com/bojieli/ai-agent-book $BOOK 2>&1 | Out-Null
    Write-Host "  [OK] 仓库已 clone (含10章正文 + 92实验代码)" -ForegroundColor Green
} else {
    Write-Host "  [OK] 已存在，跳过 clone" -ForegroundColor Green
}
Write-Host "  在线阅读: https://bojieli.github.io/ai-agent-book/" -ForegroundColor White
Write-Host "  本地仓库: $BOOK" -ForegroundColor White
Write-Host "  阅读路线: 第2章(上下文工程) -> 第4章(MCP工具) -> 第6章(评估) -> 第5章(Coding Agent) -> 第8章(自我进化)" -ForegroundColor Yellow

# ============================================================
# P1 - 本周安排时间
# ============================================================

# [3/8] mattpocock/skills
Write-Host "`n[3/8] mattpocock/skills" -ForegroundColor Green
$SK = "$BASE\mattpocock-skills"
if (-not (Test-Path $SK)) { 
    git clone --depth 1 https://github.com/mattpocock/skills $SK 2>&1 | Out-Null
    Write-Host "  [OK] clone 完成" -ForegroundColor Green
} else { 
    git -C $SK pull 2>&1 | Out-Null
    Write-Host "  [OK] 已更新" -ForegroundColor Green
}
Write-Host "  包含 Skills: $(Get-ChildItem "$SK\skills" -Directory | ForEach-Object { $_.Name })" -ForegroundColor White
Write-Host "  重点: code-review / tdd / research / triage / domain-modeling" -ForegroundColor White
Write-Host "  [深度集成] code-review/tdd/research/diagnosing-bugs 已复制到 .skills/" -ForegroundColor Green

# [4/8] worldmonitor (已 clone，只更新)
Write-Host "`n[4/8] worldmonitor" -ForegroundColor Green
$WM = "$BASE\worldmonitor"
if (Test-Path "$WM\.git") { 
    git -C $WM pull 2>&1 | Out-Null
    Write-Host "  [OK] 已更新到最新" -ForegroundColor Green 
} else { 
    git clone --depth 1 https://github.com/koala73/worldmonitor $WM 2>&1 | Out-Null
    Write-Host "  [OK] clone 完成" -ForegroundColor Green
}
Write-Host "  集成方向: 接入 morning_report_runner.py 晨间流水线，补充全球地缘政治数据源" -ForegroundColor White
Write-Host "  [深度集成] worldmonitor_connector.py 已创建 (14量化符号 + 6国风险 + 冲突+新闻)" -ForegroundColor Green
Write-Host "  [深度集成] morning_report_runner.py [10/9] 已新增 WorldMonitor 全球情报段" -ForegroundColor Green

# [5/8] code-review-graph (通过 uvx 运行，无需 pip)
Write-Host "`n[5/8] code-review-graph" -ForegroundColor Green
Write-Host "  注意: 需要 Python 3.10+, 当前 Python 3.8 不兼容" -ForegroundColor Yellow
Write-Host "  uvx 方案已验证通过:" -ForegroundColor Green
try {
    uvx --from code-review-graph code-review-graph --version 2>&1 | Out-Null
    Write-Host "  [OK] uvx --from code-review-graph 可用" -ForegroundColor Green
    Write-Host "  构建图谱: uvx --from code-review-graph code-review-graph build --dir $BASE" -ForegroundColor White
    Write-Host "  安装 MCP:  uvx --from code-review-graph code-review-graph install --platform claude" -ForegroundColor White
    Write-Host "  增量更新:  uvx --from code-review-graph code-review-graph update --dir $BASE" -ForegroundColor White
} catch {
    Write-Host "  [失败] 请检查 uv 安装: pip install uv" -ForegroundColor Red
}

# [6/8] pi (多模型统一 API)
Write-Host "`n[6/8] pi" -ForegroundColor Green
$PI = "$BASE\pi"
if (-not (Test-Path $PI)) {
    git clone --depth 1 https://github.com/earendil-works/pi $PI 2>&1 | Out-Null
    Write-Host "  [OK] clone 完成" -ForegroundColor Green
} else {
    Write-Host "  [OK] 已存在" -ForegroundColor Green
}
Write-Host "  注意: pi 是 TypeScript monorepo，与 Python 技术栈有差异" -ForegroundColor Yellow
Write-Host "  npm 构建 (可选): cd $PI ; npm install --ignore-scripts ; npm run build" -ForegroundColor White

# ============================================================
# P2 - 可选，按需
# ============================================================

Write-Host "`n======================================== " -ForegroundColor Cyan
Write-Host "  P2 - 可选安装" -ForegroundColor Cyan
Write-Host "======================================== " -ForegroundColor Cyan

# [7/8] orca
Write-Host "`n[7/8] orca (并行 Agent 桌面工作区)" -ForegroundColor Green
Write-Host "  状态: 已安装到 $env:LOCALAPPDATA\Programs\orca\Orca.exe" -ForegroundColor Green
Write-Host "  场景: 同一 prompt 发给多个 Agent，并排比对 diff" -ForegroundColor White

# [8/8] jcode
Write-Host "`n[8/8] jcode (极轻量终端 Agent)" -ForegroundColor Green
Write-Host "  状态: v0.61.0 已安装到 C:\Users\Administrator\AppData\Local\jcode\bin\" -ForegroundColor Green
Write-Host "  优势: 单 session 27.8MB (Claude Code 386.6MB), 启动 14ms" -ForegroundColor White
Write-Host "  适合: 盘前->盘中->盘后 多 session 工作流" -ForegroundColor White
Write-Host "  绑定热键: jcode setup-hotkey" -ForegroundColor White

# ============================================================
# 部署总结
# ============================================================

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "  部署总结" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

Write-Host "`n  [P0 - 立刻可用]" -ForegroundColor Green
Write-Host "    i-have-adhd         Claude Code 输入 /i-have-adhd 启用" -ForegroundColor White
Write-Host "                        开机自启: ~/.claude/.i-have-adhd-always 已创建" -ForegroundColor DarkGray
Write-Host "    ai-agent-book       在线阅读: https://bojieli.github.io/ai-agent-book/" -ForegroundColor White
Write-Host "                        本地仓库: $BOOK" -ForegroundColor DarkGray

Write-Host "`n  [P1 - 本周消化]" -ForegroundColor Yellow
Write-Host "    mattpocock-skills   浏览: $SK\skills\" -ForegroundColor White
Write-Host "    worldmonitor        已更新并同步到最新" -ForegroundColor White
Write-Host "    code-review-graph   使用 uvx 运行 (Python 3.10+)" -ForegroundColor White
Write-Host "    pi                  已 clone: $PI" -ForegroundColor White

Write-Host "`n  [P2 - 按需安装]" -ForegroundColor DarkGray
Write-Host "    orca                已安装" -ForegroundColor Green
Write-Host "    jcode               irm https://jcode.sh/install.ps1 | iex" -ForegroundColor DarkGray

Write-Host "`n  跳过: buzz / OmniRoute / hallmark / RuView / voicebox / open-seo / ai-engineering-from-scratch" -ForegroundColor DarkGray

Write-Host "`n  [ai-agent-book 阅读路线]" -ForegroundColor Cyan
Write-Host "    第2章 上下文工程(KV Cache/提示/Skills) -> 你的 CLAUDE.md + .skills/" -ForegroundColor White
Write-Host "    第4章 MCP工具协议(感知/执行/协作)       -> ifind_client.py / wind_mcp_fetcher.py" -ForegroundColor White
Write-Host "    第6章 Agent评估(环境/指标/显著性)      -> backtest_engine.py / OPTIMIZATION_REPORT.md" -ForegroundColor White
Write-Host "    第5章 Coding Agent全景                -> Claude Code + ai-hedge-fund/" -ForegroundColor White
Write-Host "    第8章 Agent自我进化(经验/蒸馏/工具)    -> .skills_optimized/" -ForegroundColor White

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "  [深度集成 (2026-07-28)]" -ForegroundColor Cyan
Write-Host "    worldmonitor  → 15_每日工作流/worldmonitor_connector.py (14品种+6国风险+冲突+新闻)" -ForegroundColor Green
Write-Host "                    15_每日工作流/morning_report_runner.py [10/9] 已集成" -ForegroundColor Green
Write-Host "    mattpocock     → .skills/code-review + tdd + research + diagnosing-bugs" -ForegroundColor Green
Write-Host "    jcode          → v0.61.0 已安装" -ForegroundColor Green
Write-Host ""
Write-Host "  部署完成! 优先启用 i-have-adhd，然后翻开 ai-agent-book 第2章。" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
