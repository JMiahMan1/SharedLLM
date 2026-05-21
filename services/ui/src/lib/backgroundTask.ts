import { Capacitor } from '@capacitor/core';
import { BackgroundTask } from '@capawesome/capacitor-background-task';

let taskId: string | null = null;

export async function startBackgroundTask(onExpiry?: () => void): Promise<string | null> {
  if (!Capacitor.isNativePlatform()) return null;

  try {
    taskId = await BackgroundTask.beforeAppTerminates((task) => {
      if (onExpiry) onExpiry();
      task.finish();
    });
    return taskId;
  } catch {
    return null;
  }
}

export async function finishBackgroundTask(): Promise<void> {
  if (!taskId || !Capacitor.isNativePlatform()) return;
  try {
    await BackgroundTask.finish({ taskId });
    taskId = null;
  } catch {
    // Task already finished
  }
}

export async function keepAliveWhile(callback: () => Promise<void>): Promise<void> {
  const id = await startBackgroundTask();
  try {
    await callback();
  } finally {
    if (id) await finishBackgroundTask();
  }
}
