import { hasHarmonyPush, enableHarmonyPush, disableHarmonyPush } from "./harmony-push.ts";
import type { RemoteNotificationMode } from "./notification-mode";

export interface PushBindingTarget {
  machineId: string;
  mode: RemoteNotificationMode;
}

export type PushBindingState = "off" | "binding" | "remote" | "local";

export interface PushBindingSnapshot {
  state: PushBindingState;
  target: PushBindingTarget | null;
  bound: PushBindingTarget | null;
}

type PushBindingApply = (target: PushBindingTarget | null) => Promise<boolean>;
type PushBindingListener = (snapshot: PushBindingSnapshot) => void;

function sameTarget(
  left: PushBindingTarget | null,
  right: PushBindingTarget | null,
): boolean {
  return left?.machineId === right?.machineId && left?.mode === right?.mode;
}

export class PushBindingCoordinator {
  private readonly apply: PushBindingApply;
  private desired: PushBindingTarget | null = null;
  private bound: PushBindingTarget | null = null;
  private state: PushBindingState = "off";
  private drain: Promise<void> | null = null;
  private refresh = false;
  private listener: PushBindingListener | null = null;

  constructor(apply: PushBindingApply) {
    this.apply = apply;
  }

  subscribe(listener: PushBindingListener): () => void {
    this.listener = listener;
    listener(this.snapshot());
    return () => {
      if (this.listener === listener) this.listener = null;
    };
  }

  snapshot(): PushBindingSnapshot {
    return {
      state: this.state,
      target: this.desired ? { ...this.desired } : null,
      bound: this.bound ? { ...this.bound } : null,
    };
  }

  isRemoteActive(machineId: string): boolean {
    if (!this.desired || this.desired.machineId !== machineId) return false;
    return this.bound?.machineId === machineId
      && (this.state === "remote" || this.state === "binding");
  }

  async setTarget(target: PushBindingTarget | null, refresh = false): Promise<PushBindingSnapshot> {
    this.desired = target ? { ...target } : null;
    this.refresh ||= refresh;
    if (!this.drain) {
      this.drain = this.run().finally(() => {
        this.drain = null;
      });
    }
    await this.drain;
    return this.snapshot();
  }

  private publish(state: PushBindingState): void {
    this.state = state;
    this.listener?.(this.snapshot());
  }

  private async run(): Promise<void> {
    while (true) {
      const target = this.desired ? { ...this.desired } : null;
      if (sameTarget(target, this.bound) && !this.refresh) {
        this.publish(target ? "remote" : "off");
        return;
      }
      this.refresh = false;
      this.publish("binding");
      let enabled = false;
      try {
        enabled = await this.apply(target);
      } catch {
        // A failed adapter must settle through the same path as a rejected
        // binding, including any newer target queued while it was in flight.
      }
      if (enabled) this.bound = target;

      // A newer request arrived while the network mutation was in flight.
      // Continue from the actual binding that just completed; never publish the
      // stale target as the current machine.
      if (!sameTarget(target, this.desired) || this.refresh) continue;
      if (!target) {
        if (enabled) this.bound = null;
        this.publish("off");
      } else if (enabled || this.bound?.machineId === target.machineId) {
        // During a same-machine mode update the prior remote subscription still
        // delivers. Treat it as remote to avoid a duplicate local notification.
        this.publish("remote");
      } else {
        this.publish("local");
      }
      return;
    }
  }
}

export async function enableRemotePush(machineId: string, mode: RemoteNotificationMode = "generic"): Promise<boolean> {
  try {
    if (hasHarmonyPush()) return await enableHarmonyPush(machineId, mode);
    return await (await import("./web-push.ts")).enableRemotePush(machineId, mode);
  } catch {
    return false;
  }
}

export async function disableRemotePush(): Promise<boolean> {
  try {
    if (hasHarmonyPush()) return await disableHarmonyPush();
    return await (await import("./web-push.ts")).disableRemotePush();
  } catch {
    return false;
  }
}
