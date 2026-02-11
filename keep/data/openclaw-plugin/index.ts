/**
 * keep — OpenClaw plugin
 *
 * Hooks:
 *   before_agent_start → inject `keep now` context
 *   after_agent_stop   → update intentions
 */

import { execSync } from "child_process";

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

  api.logger?.info("[keep] Registered hooks: before_agent_start, after_agent_stop");
}
