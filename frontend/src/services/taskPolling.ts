import { api } from '../api';
import type { PlatformTask, QueuedTaskResponse, TaskDetailResponse } from '../types';

const TERMINAL_STATUSES = new Set(['completed', 'partial', 'blocked', 'failed', 'cancelled', 'interrupted', 'rejected']);
const ERROR_STATUSES = new Set(['failed', 'cancelled', 'interrupted', 'rejected']);

export class TaskPollingError extends Error {
  readonly response: TaskDetailResponse;

  constructor(response: TaskDetailResponse) {
    const ledgerMessage = response.ledger?.error?.message;
    const message = typeof ledgerMessage === 'string'
      ? ledgerMessage
      : response.task.status === 'cancelled' ? '任务已停止' : '后台任务执行失败';
    super(message);
    this.name = 'TaskPollingError';
    this.response = response;
  }
}

export const isQueuedTask = (value: unknown): value is QueuedTaskResponse => (
  Boolean(value)
  && typeof value === 'object'
  && (value as { queued?: unknown }).queued === true
  && typeof (value as { task_id?: unknown }).task_id === 'string'
  && Boolean((value as { task_id: string }).task_id.trim())
);

export const taskStatusLabel = (status: string) => ({
  queued: '排队',
  running: '运行中',
  cancelling: '停止中',
  completed: '完成',
  partial: '部分完成',
  blocked: '已阻断',
  failed: '失败',
  cancelled: '已停止',
  interrupted: '中断',
  rejected: '拒绝',
}[status] || status);

export async function waitForTask(
  taskId: string,
  options: {
    onUpdate?: (task: PlatformTask) => void;
    timeoutMs?: number;
  } = {},
): Promise<TaskDetailResponse> {
  const deadline = Date.now() + Math.max(5_000, options.timeoutMs ?? 15 * 60_000);
  let delayMs = 400;
  while (true) {
    const { data } = await api.get<TaskDetailResponse>(`/tasks/${encodeURIComponent(taskId)}`);
    options.onUpdate?.(data.task);
    if (TERMINAL_STATUSES.has(data.task.status)) {
      if (ERROR_STATUSES.has(data.task.status)) throw new TaskPollingError(data);
      return data;
    }
    if (Date.now() >= deadline) throw new Error('后台任务等待超时，请到任务中心查看状态');
    await new Promise((resolve) => window.setTimeout(resolve, delayMs));
    delayMs = Math.min(2_500, Math.round(delayMs * 1.35));
  }
}

export async function resolveTaskResponse<T>(
  value: T | QueuedTaskResponse,
  options: {
    onUpdate?: (task: PlatformTask) => void;
    timeoutMs?: number;
  } = {},
): Promise<T> {
  if (!isQueuedTask(value)) return value as T;
  const completed = await waitForTask(value.task_id, options);
  return completed.detail as T;
}
