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
