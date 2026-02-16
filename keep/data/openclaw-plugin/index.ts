/**
 * keep — OpenClaw plugin
 *
 * Hooks:
 *   before_agent_start  → inject `keep now` context
 *   after_agent_stop    → update intentions
 *   after_compaction    → index workspace memory files into keep
 */

import { execSync } from "child_process";
import path from "node:path";
import fs from "node:fs";

function keepAvailable(): boolean {
  try {
    execSync("keep config", { timeout: 3000, stdio: "ignore" });
    return true;
  } catch {
    return false;
  }
}

function runKeep(args: string, input?: string): string | null {
  try {
    return execSync(`keep ${args}`, {
      encoding: "utf-8",
      timeout: 5000,
      input: input ?? "",
    }).trim();
  } catch {
    return null;
  }
}

function runKeepLong(args: string, timeoutMs: number = 60000): string | null {
  try {
    return execSync(`keep ${args}`, {
      encoding: "utf-8",
      timeout: timeoutMs,
      stdio: ["pipe", "pipe", "pipe"],
    }).trim();
  } catch {
    return null;
  }
}

export default function register(api: any) {
  if (!keepAvailable()) {
    api.logger?.warn("[keep] keep CLI not found, plugin inactive");
    return;
  }

  // Agent start: inject current intentions + similar context
  api.on(
    "before_agent_start",
    async (_event: any, _ctx: any) => {
      const now = runKeep("now -n 10");
      if (!now) return;

      return {
        prependContext: `\`keep now\`:\n${now}`,
      };
    },
    { priority: 10 },
  );

  // Agent stop: update intentions
  api.on(
    "after_agent_stop",
    async (_event: any, _ctx: any) => {
      runKeep("now 'Session ended'");
    },
    { priority: 10 },
  );

  // After compaction: index memory files into keep
  // Memory flush writes files right before compaction, so they're fresh here.
  // Uses `keep put` with file stat fast-path — unchanged files are no-ops.
  api.on(
    "after_compaction",
    async (_event: any, ctx: any) => {
      const workspaceDir = ctx?.workspaceDir;
      if (!workspaceDir) return;

      const memoryDir = path.join(workspaceDir, "memory");
      if (!fs.existsSync(memoryDir)) return;

      api.logger?.debug("[keep] Indexing memory files after compaction");
      const result = runKeepLong(`put "${memoryDir}/"`, 30000);
      if (result) {
        api.logger?.info("[keep] Post-compaction memory sync complete");
      }
    },
    { priority: 20 },
  );

  api.logger?.info("[keep] Registered hooks: before_agent_start, after_agent_stop, after_compaction");
}
