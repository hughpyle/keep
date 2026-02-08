/**
 * keep — OpenClaw plugin
 *
 * Hook: before_agent_start → inject `keep now` into session context
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

function getKeepNow(): string | null {
  try {
    return execSync("keep now", {
      encoding: "utf-8",
      timeout: 5000,
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

  api.on(
    "before_agent_start",
    async (_event: any, _ctx: any) => {
      const now = getKeepNow();
      if (!now) return;

      return {
        prependContext: `\`keep now\`:\n${now}`,
      };
    },
    { priority: 10 },
  );

  api.logger?.info("[keep] Registered hook: before_agent_start");
}
