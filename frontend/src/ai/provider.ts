/**
 * 模型接入(前端直连):把用户自己的 OpenAI 兼容网关(如本地 sub2api)配在浏览器里,
 * 前端直接调用,API Key 不经过服务端、不落盘到服务器。
 */

export interface AiProvider {
  endpoint: string; // 例如 http://127.0.0.1:3000/v1
  apiKey: string;
  model: string; // 例如 gpt-4o-mini
}

const STORAGE_KEY = 'crypto_ai_provider';

export function loadProvider(): AiProvider {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      return {
        endpoint: String(parsed.endpoint || '').trim().replace(/\/+$/, ''),
        apiKey: String(parsed.apiKey || ''),
        model: String(parsed.model || '').trim(),
      };
    }
  } catch { /* 忽略损坏的本地配置 */ }
  return { endpoint: '', apiKey: '', model: '' };
}

export function saveProvider(provider: AiProvider): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify({
    endpoint: provider.endpoint.trim().replace(/\/+$/, ''),
    apiKey: provider.apiKey,
    model: provider.model.trim(),
  }));
}

export function providerReady(provider: AiProvider): boolean {
  return Boolean(provider.endpoint && provider.apiKey && provider.model);
}

export async function testConnection(provider: AiProvider): Promise<string> {
  const response = await fetch(`${provider.endpoint}/models`, {
    headers: { Authorization: `Bearer ${provider.apiKey}` },
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const data = await response.json();
  const count = Array.isArray(data?.data) ? data.data.length : 0;
  const hasModel = Array.isArray(data?.data) && data.data.some((m: any) => m?.id === provider.model);
  return hasModel
    ? `连接成功,模型 ${provider.model} 可用`
    : `连接成功(返回 ${count} 个模型),未在列表中看到 ${provider.model},请核对模型名`;
}

export interface NewsInterpretInput {
  title: string;
  summary: string;
  source: string;
  published_at: string;
  symbols: string[];
  topics: string[];
  sentiment: string;
  risk_level: string;
  impact_score: string | number;
}

export function buildNewsPrompt(event: NewsInterpretInput): string {
  return [
    '你是加密货币市场的消息面分析师,只做研究解读,不构成投资建议。',
    '请用简体中文、精炼地分析下面这条新闻,分三节输出:',
    '1. 一句话解读:这条新闻在讲什么,为什么重要;',
    '2. 品种影响:对相关币种短期(数小时-数天)情绪与波动的判断,分币种一句话;',
    '3. 策略建议:对量化策略(如 MACD 反转、均线交叉、动量策略)给出具体操作建议,',
    '   从"暂停新开仓 / 降低仓位 / 观望 / 可关注做多信号"中选择,并说明理由和建议持续时长。',
    '',
    `标题:${event.title}`,
    `摘要:${event.summary}`,
    `来源:${event.source}  发布时间:${event.published_at}`,
    `相关币种:${event.symbols.join('、') || '全市场'}  主题:${event.topics.join('、') || '综合'}`,
    `情绪:${event.sentiment}  风险等级:${event.risk_level}  影响分:${event.impact_score}`,
  ].join('\n');
}

export async function interpretNews(provider: AiProvider, event: NewsInterpretInput): Promise<string> {
  const response = await fetch(`${provider.endpoint}/chat/completions`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${provider.apiKey}`,
    },
    body: JSON.stringify({
      model: provider.model,
      messages: [{ role: 'user', content: buildNewsPrompt(event) }],
      temperature: 0.3,
      max_tokens: 1200,
    }),
  });
  if (!response.ok) {
    const text = await response.text().catch(() => '');
    throw new Error(`模型调用失败 HTTP ${response.status} ${text.slice(0, 120)}`);
  }
  const data = await response.json();
  const content = data?.choices?.[0]?.message?.content;
  if (!content) throw new Error('模型返回为空');
  return String(content);
}

export interface AdviceJudgeInput {
  symbol: string;
  action_text: string;
  urgency_text: string;
  suggested_duration_hours: number;
  position_guidance: string;
  strategy_notes: Record<string, string>;
  reason: string;
  evidence: string[];
  impact_score: number | string;
  heat: number;
  price_change: string; // 技术面,如 "+2.3%"
  event_context: string[]; // 相关事件 标题+摘要
}

export function buildJudgePrompt(input: AdviceJudgeInput): string {
  const notes = Object.entries(input.strategy_notes || {})
    .map(([k, v]) => `${k}:${v}`).join('\n');
  return [
    '你是加密货币量化投顾,只做研究研判,不构成投资建议,不预测具体点位。',
    '新闻只是影响行情的因素之一,必须结合规则层结论与价格技术面综合判断,不要只看新闻字面下结论。',
    '请用简体中文按以下五节输出,每节精炼:',
    '1. 综合方向:偏多 / 偏空 / 中性,一句话理由;',
    '2. 置信度:高 / 中 / 低,并说明依据;',
    '3. 操作建议:对趋势跟踪、动量、均值回归、被动持有四类策略各一句话;',
    '4. 风险提示:最需要警惕的一两个点;',
    '5. 有效期:这条研判大概管用多久,为什么。',
    '',
    `品种:${input.symbol}  价格技术面(近1h涨跌):${input.price_change}`,
    `规则层建议:${input.action_text}  紧急度:${input.urgency_text}  建议时长:${input.suggested_duration_hours}h`,
    `规则层仓位指引:${input.position_guidance}`,
    `规则层策略话术:\n${notes}`,
    `事由:${input.reason}`,
    `依据新闻:${input.evidence.join(' / ')}`,
    `相关事件:\n${input.event_context.join('\n')}`,
    `影响分:${input.impact_score}  报道家数:${input.heat}`,
  ].join('\n');
}

export async function judgeAdvice(provider: AiProvider, input: AdviceJudgeInput): Promise<string> {
  const response = await fetch(`${provider.endpoint}/chat/completions`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
Authorization: `Bearer ${provider.apiKey}`,
    },
    body: JSON.stringify({
      model: provider.model,
      messages: [{ role: 'user', content: buildJudgePrompt(input) }],
      temperature: 0.3,
      max_tokens: 1500,
    }),
  });
  if (!response.ok) {
    const text = await response.text().catch(() => '');
    throw new Error(`模型调用失败 HTTP ${response.status} ${text.slice(0, 120)}`);
  }
  const data = await response.json();
  const content = data?.choices?.[0]?.message?.content;
  if (!content) throw new Error('模型返回为空');
  return String(content);
}
