// calibrate_via_ifind.js
// iFinD 批量查询 — 19 个标的的真实历史风险收益指标
// 策略: 每标的分指标查询 (单指标最稳定), 共 4 次/标的
//   1. get_*_performance: 近一年收益率
//   2. get_*_performance: 近三年收益率
//   3. get_risk_indicators: 近一年夏普比率
//   4. get_risk_indicators: 近一年年化波动率
//   5. get_risk_indicators: 区间最大回撤 (本年至今)
// 输出: e:/各种PY程序/每日报告归档/YYYY-MM-DD/ifind_raw_results_YYYYMMDD.json

const { call } = require('C:/Users/Administrator/.trae/skills/ifind-finance-data/call-node.js');
const fs = require('fs');
const path = require('path');

// 资产清单
const ASSET_MAP = {
    "核心宽基ETF": [
        { code: "510300.SH", name: "沪深300ETF", server: "fund" },
        { code: "510500.SH", name: "中证500ETF", server: "fund" },
        { code: "512100.SH", name: "中证1000ETF", server: "fund" },
        { code: "588000.SH",  name: "科创50ETF", server: "fund" },
    ],
    "科技成长个股": [
        { code: "688041.SH", name: "海光信息", server: "stock" },
        { code: "300308.SZ", name: "中际旭创", server: "stock" },
    ],
    "高端制造/基建": [
        { code: "002371.SZ", name: "北方华创", server: "stock" },
        { code: "688981.SH", name: "中芯国际", server: "stock" },
        { code: "300750.SZ", name: "宁德时代", server: "stock" },
    ],
    "防御/红利": [
        { code: "600900.SH", name: "长江电力", server: "stock" },
        { code: "600276.SH", name: "恒瑞医药", server: "stock" },
        { code: "603259.SH", name: "药明康德", server: "stock" },
    ],
    "商品/避险": [
        { code: "518880.SH", name: "华安黄金ETF", server: "fund" },
        { code: "601088.SH", name: "中国神华", server: "stock" },
        { code: "600019.SH", name: "宝钢股份", server: "stock" },
        { code: "600219.SH", name: "南山铝业", server: "stock" },
        { code: "000792.SZ", name: "盐湖股份", server: "stock" },
    ],
    "半导体ETF": [
        { code: "512480.SH", name: "半导体ETF", server: "fund" },
    ],
    "新能源ETF": [
        { code: "516160.SH", name: "新能源ETF", server: "fund" },
    ],
};

const sleep = ms => new Promise(r => setTimeout(r, ms));

// 单次调用, 返回 raw content text
async function queryOnce(server, tool, query) {
    const r = await call(server, tool, { query });
    if (!r.ok) return { ok: false, error: r.error };
    const content = r.data?.result?.content;
    if (Array.isArray(content) && content.length > 0) {
        return { ok: true, text: content.map(c => c.text || '').join('\n') };
    }
    return { ok: false, error: 'empty content' };
}

// 解析 markdown 表格, 提取数值
// 表格格式: | 代码 | 简称 | 日期 | 指标值 |
function parseTable(text) {
    if (!text) return [];
    const lines = text.split('\n');
    const rows = [];
    let header = null;
    for (const line of lines) {
        const m = line.match(/^\|(.+)\|$/);
        if (!m) continue;
        const cells = m[1].split('|').map(s => s.trim()).filter(s => s !== '');
        // 跳过分隔行
        if (cells.every(c => /^[-:]+$/.test(c))) continue;
        if (!header) {
            header = cells;
            continue;
        }
        rows.push(cells);
    }
    return { header: header || [], rows };
}

async function queryInstrument(inst) {
    const perfTool = inst.server === "fund" ? "get_fund_market_performance" : "get_stock_performance";
    const riskTool = "get_risk_indicators";  // 风险指标工具 (stock 专用, fund 可能没有)
    const result = {
        windcode: inst.code,
        name: inst.name,
        asset_class: inst.assetClass,
        server: inst.server,
        queries: {},
    };

    // Q1: 近一年收益率
    {
        const q = `${inst.code}近一年收益率`;
        const r = await queryOnce(inst.server, perfTool, q);
        result.queries.recent_1y_return = { query: q, ...r };
        if (r.ok) result.queries.recent_1y_return.parsed = parseTable(r.text);
        await sleep(550);
    }

    // Q2: 近三年收益率
    {
        const q = `${inst.code}近三年收益率`;
        const r = await queryOnce(inst.server, perfTool, q);
        result.queries.recent_3y_return = { query: q, ...r };
        if (r.ok) result.queries.recent_3y_return.parsed = parseTable(r.text);
        await sleep(550);
    }

    // Q3-Q5: 风险指标 (主要适用于 stock; fund 服务可能没有 get_risk_indicators)
    if (inst.server === "stock") {
        // Q3: 夏普比率
        {
            const q = `${inst.code}近一年夏普比率`;
            const r = await queryOnce("stock", riskTool, q);
            result.queries.sharpe_1y = { query: q, ...r };
            if (r.ok) result.queries.sharpe_1y.parsed = parseTable(r.text);
            await sleep(550);
        }
        // Q4: 年化波动率
        {
            const q = `${inst.code}近一年年化波动率`;
            const r = await queryOnce("stock", riskTool, q);
            result.queries.volatility_1y = { query: q, ...r };
            if (r.ok) result.queries.volatility_1y.parsed = parseTable(r.text);
            await sleep(550);
        }
        // Q5: 最大回撤
        {
            const q = `${inst.code}区间最大回撤`;
            const r = await queryOnce("stock", riskTool, q);
            result.queries.max_drawdown = { query: q, ...r };
            if (r.ok) result.queries.max_drawdown.parsed = parseTable(r.text);
            await sleep(550);
        }
    } else {
        // fund 服务没有 get_risk_indicators, 用 get_fund_market_performance 多指标
        // Q3+Q4: 近一年波动率 + 夏普
        {
            const q = `${inst.code}近一年夏普比率`;
            const r = await queryOnce(inst.server, perfTool, q);
            result.queries.sharpe_1y = { query: q, ...r };
            if (r.ok) result.queries.sharpe_1y.parsed = parseTable(r.text);
            await sleep(550);
        }
        {
            const q = `${inst.code}近一年波动率`;
            const r = await queryOnce(inst.server, perfTool, q);
            result.queries.volatility_1y = { query: q, ...r };
            if (r.ok) result.queries.volatility_1y.parsed = parseTable(r.text);
            await sleep(550);
        }
        {
            const q = `${inst.code}近一年最大回撤`;
            const r = await queryOnce(inst.server, perfTool, q);
            result.queries.max_drawdown = { query: q, ...r };
            if (r.ok) result.queries.max_drawdown.parsed = parseTable(r.text);
            await sleep(550);
        }
    }

    return result;
}

async function main() {
    console.log("=".repeat(70));
    console.log("iFinD 批量查询 — 19 标的真实历史风险收益指标");
    console.log("=".repeat(70));

    const allResults = {};
    let total = 0, ok = 0, fail = 0;

    for (const [assetClass, instruments] of Object.entries(ASSET_MAP)) {
        console.log(`\n--- [${assetClass}] (${instruments.length} 个标的) ---`);
        for (const inst of instruments) {
            total++;
            inst.assetClass = assetClass;
            process.stdout.write(`  → ${inst.code} ${inst.name} ... `);
            try {
                const r = await queryInstrument(inst);
                allResults[inst.code] = r;
                // 简要汇总
                const q = r.queries;
                const summary = [
                    q.recent_1y_return?.ok ? '1yRet✓' : '1yRet✗',
                    q.recent_3y_return?.ok ? '3yRet✓' : '3yRet✗',
                    q.sharpe_1y?.ok ? 'Sharpe✓' : 'Sharpe✗',
                    q.volatility_1y?.ok ? 'Vol✓' : 'Vol✗',
                    q.max_drawdown?.ok ? 'MaxDD✓' : 'MaxDD✗',
                ].join(' ');
                console.log(summary);
                ok++;
            } catch (e) {
                console.log(`✗ ${e.message}`);
                allResults[inst.code] = { windcode: inst.code, name: inst.name, asset_class: assetClass, error: e.message };
                fail++;
            }
        }
    }

    // 保存原始结果 (统一到根目录 每日报告归档)
    const today = new Date().toISOString().slice(0, 10);
    const archiveDir = `e:/各种PY程序/每日报告归档/${today}`;
    fs.mkdirSync(archiveDir, { recursive: true });
    const jsonPath = path.join(archiveDir, `ifind_raw_results_${new Date().toISOString().slice(0, 10).replace(/-/g, '')}.json`);
    fs.writeFileSync(jsonPath, JSON.stringify(allResults, null, 2), 'utf-8');
    console.log(`\n原始结果已保存: ${jsonPath}`);
    console.log(`总计 ${total} 标的, 成功 ${ok}, 失败 ${fail}`);
}

main().catch(e => {
    console.error("主流程异常:", e);
    process.exit(1);
});
