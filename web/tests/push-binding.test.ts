import assert from "node:assert/strict";
import { PushBindingCoordinator, type PushBindingTarget, type PushBindingResult } from "../src/push.ts";

const bindingA: PushBindingTarget = { machineId: "machine-a", mode: "generic" };
const bindingB: PushBindingTarget = { machineId: "machine-b", mode: "session" };
const sessionA: PushBindingTarget = { ...bindingA, mode: "session" };

let rejectBinding = true;
const rejectingCoordinator = new PushBindingCoordinator(async () => {
  if (rejectBinding) throw new Error("adapter failed");
  return true;
});
assert.deepEqual(await rejectingCoordinator.setTarget(bindingA), {
  state: "local", target: bindingA, bound: null,
}, "a rejected apply settles to local instead of leaving binding pending");
rejectBinding = false;
assert.deepEqual(await rejectingCoordinator.setTarget(bindingA), {
  state: "remote", target: bindingA, bound: bindingA,
}, "the drain clears after rejection so the same target can recover");
rejectBinding = true;
assert.deepEqual(await rejectingCoordinator.setTarget(sessionA), {
  state: "remote", target: sessionA, bound: bindingA,
}, "a rejected mode change retains the confirmed binding without duplicate local delivery");
assert.deepEqual(await rejectingCoordinator.setTarget(null), {
  state: "off", target: null, bound: bindingA,
}, "a rejected disable exits binding without falsely clearing the confirmed subscription");
assert.equal(rejectingCoordinator.isRemoteActive(bindingA.machineId), false);
rejectBinding = false;
assert.deepEqual(await rejectingCoordinator.setTarget(null), {
  state: "off", target: null, bound: null,
}, "a subsequent disable can still remove the retained subscription");

let rejectPendingBinding!: (reason: Error) => void;
const rejectionTargets: (PushBindingTarget | null)[] = [];
const changedAfterRejection = new PushBindingCoordinator(target => {
  rejectionTargets.push(target);
  return rejectionTargets.length === 1
    ? new Promise<boolean>((_resolve, reject) => { rejectPendingBinding = reject; })
    : Promise.resolve(true);
});
const rejectedA = changedAfterRejection.setTarget(bindingA);
const followingB = changedAfterRejection.setTarget(bindingB);
rejectPendingBinding(new Error("lazy chunk unavailable"));
await Promise.all([rejectedA, followingB]);
assert.deepEqual(rejectionTargets, [bindingA, bindingB]);
assert.deepEqual(changedAfterRejection.snapshot(), {
  state: "remote", target: bindingB, bound: bindingB,
}, "a rejected old target must not strand the newer queued binding");

let applyResult: PushBindingResult = true;
const revokedCoordinator = new PushBindingCoordinator(async () => applyResult);
await revokedCoordinator.setTarget(bindingA);
applyResult = null;
assert.deepEqual(await revokedCoordinator.setTarget(sessionA), {
  state: "local", target: sessionA, bound: null,
}, "provider rollback must clear the old binding instead of claiming remote delivery");
assert.equal(revokedCoordinator.isRemoteActive(bindingA.machineId), false);
applyResult = true;
assert.deepEqual(await revokedCoordinator.setTarget(bindingA), {
  state: "remote", target: bindingA, bound: bindingA,
}, "returning to a revoked target must apply a fresh binding");

let finishRevocation!: (value: PushBindingResult) => void;
const revocationTargets: (PushBindingTarget | null)[] = [];
const queuedAfterRevocation = new PushBindingCoordinator(target => {
  revocationTargets.push(target);
  return revocationTargets.length === 2
    ? new Promise<PushBindingResult>(resolve => { finishRevocation = resolve; })
    : Promise.resolve(true);
});
await queuedAfterRevocation.setTarget(bindingA);
const failedMode = queuedAfterRevocation.setTarget(sessionA);
const restoreGeneric = queuedAfterRevocation.setTarget(bindingA);
finishRevocation(null);
await Promise.all([failedMode, restoreGeneric]);
assert.deepEqual(revocationTargets, [bindingA, sessionA, bindingA],
  "a newer request for the old target cannot reuse its revoked provider token");
assert.deepEqual(queuedAfterRevocation.snapshot(), {
  state: "remote", target: bindingA, bound: bindingA,
});

console.log("push binding failure and revocation tests passed");
