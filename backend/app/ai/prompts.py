"""Prompt templates for the stock analysis agent."""

SYSTEM_PROMPT = """你是一位专业的 A 股投研分析师。你将基于结构化数据对个股进行多维度分析。
要求：
1. 严格依据提供的数据，不编造未给出的信息。
2. 输出必须是合法 JSON，结构如下：
{
  "score": <0-100 综合评分>,
  "scores": {
    "fundamental": <0-100>,
    "technical": <0-100>,
    "capital": <0-100>,
    "news": <0-100>,
    "risk": <0-100>
  },
  "fundamentals": "<基本面分析 markdown>",
  "technicals": "<技术面分析 markdown>",
  "capital": "<资金面分析 markdown>",
  "news": "<消息面分析 markdown>",
  "risk": "<风险提示 markdown>"
}
3. 仅输出 JSON，不要包含 ```json 代码块标记或多余解释。"""


def build_analysis_user_prompt(stock_code: str, stock_name: str, context: dict) -> str:
    """Build the user-message prompt embedding the structured context."""
    kline_recent = context.get("kline_recent", [])  # list of {date, close, pct_change, volume}
    indicators = context.get("indicators", {})       # {ma: {...}, macd: {...}, kdj: {...}}
    finance = context.get("finance", {})             # {pe, pb, roe, eps, ...}
    money_flow = context.get("money_flow", {})       # {main_net_inflow: ...}

    kline_str = "\n".join(
        [f"  {k.get('date')}: 收盘 {k.get('close')}, 涨跌幅 {k.get('pct_change')}%, 成交量 {k.get('volume')}"
         for k in kline_recent[-20:]]
    ) or "  （暂无近端行情数据）"

    ind_parts = []
    for name, vals in indicators.items():
        if vals:
            ind_parts.append(f"  {name}: {vals}")
    indicators_str = "\n".join(ind_parts) or "  （暂无指标数据）"

    finance_str = "\n".join(f"  {k}: {v}" for k, v in finance.items()) or "  （暂无财务数据）"
    flow_str = f"  主力净流入: {money_flow.get('main_net_inflow', '暂无')}" if money_flow else "  （暂无资金数据）"

    return f"""请分析股票：{stock_name}（{stock_code}）

【近端行情】
{kline_str}

【技术指标】
{indicators_str}

【财务数据】
{finance_str}

【资金面】
{flow_str}

请输出 JSON 格式的分析结果。"""


RISK_DISCLAIMER = "⚠ 以上为 AI 生成的参考信号，不构成投资建议。"
