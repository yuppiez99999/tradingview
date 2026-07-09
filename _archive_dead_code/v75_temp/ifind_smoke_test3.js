// iFinD 多指标 query 稳定性测试 — 找到最稳定的查询模式
const { call } = require('C:/Users/Administrator/.trae/skills/ifind-finance-data/call-node.js');

const PROBES = [
    // ETF 单指标查询
    { server: "fund", tool: "get_fund_market_performance", query: "510300.SH近三年最大回撤" },
    { server: "fund", tool: "get_fund_market_performance", query: "510300.SH近三年夏普比率" },
    { server: