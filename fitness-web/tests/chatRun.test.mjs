import assert from "node:assert/strict";
import test from "node:test";
import { ChatRunCancelled, waitForChatRun, cancelRegisteredRun, waitForRunDelay } from "../src/chatRun.ts";

test("accepted cancellation remains active until execution acknowledges stopping", async () => {
  const statuses = ["RUNNING", "CANCEL_REQUESTED", "CANCELLED"];
  let polls = 0;
  await assert.rejects(waitForChatRun(async () => ({ status: statuses[polls++] }), undefined,
    async () => {}), ChatRunCancelled);
  assert.equal(polls, 3);
});

test("completion winning the race returns the real answer", async () => {
  const response = { answer: "已完成" };
  assert.equal(await waitForChatRun(async () => ({ status: "COMPLETED", response })), response);
});

test("unknown result never appears as successfully cancelled", async () => {
  await assert.rejects(waitForChatRun(async () => ({ status: "UNKNOWN" })), error =>
    !(error instanceof ChatRunCancelled) && error.message.includes("核对业务结果"));
});

test("stop before registration only retries cancellation and keeps the same operation", async () => {
  let attempts = 0;
  const accepted = { status: "CANCEL_REQUESTED" };
  const cancel = async () => {
    if (++attempts <= 2) throw Object.assign(new Error("not registered"), { status: 404 });
    return accepted;
  };
  assert.equal(await cancelRegisteredRun(cancel, () => true, undefined, async () => {}), accepted);
  assert.equal(attempts, 3);
});

test("foreign/missing recovered run and authentication failures are not retried", async () => {
  for (const [status, submitting] of [[404, false], [401, true], [503, true]]) {
    let attempts = 0;
    await assert.rejects(cancelRegisteredRun(async () => {
      attempts++; throw Object.assign(new Error("failed"), { status });
    }, () => submitting), error => error.status === status);
    assert.equal(attempts, 1);
  }
});

test("component unmount aborts result recovery and retry waits", async () => {
  const controller = new AbortController();
  const wait = waitForRunDelay(60_000, controller.signal);
  controller.abort();
  await assert.rejects(wait, error => error.name === "AbortError");
  let requests = 0;
  await assert.rejects(waitForChatRun(async () => { requests++; return {status:"RUNNING"}; },
    controller.signal), error => error.name === "AbortError");
  assert.equal(requests, 0);
});

test("one transient GET failure after accepted cancellation recovers the original run", async () => {
  let calls = 0;
  await assert.rejects(waitForChatRun(async () => {
    calls++;
    if (calls === 1) throw Object.assign(new Error('temporary'), { status: 503 });
    return { status: 'CANCELLED' };
  }, undefined, async () => {}), ChatRunCancelled);
  assert.equal(calls, 2);
});

test("exhausted retry budget preserves an unresolved operation for manual recovery", async () => {
  const { ChatRunPending } = await import('../src/chatRun.ts');
  let now = 0, queries = 0;
  await assert.rejects(waitForChatRun(async () => {
    queries++; throw Object.assign(new Error('offline'), {status:0});
  }, undefined, async ms => { now += ms; }, 5000, {now:()=>now,random:()=>0.5}), ChatRunPending);
  assert.equal(queries,3);
  const reply = {answer:'恢复完成'};
  assert.equal(await waitForChatRun(async()=>({status:'COMPLETED',response:reply})),reply);
});

test("Retry-After is honored within the overall budget and auth failures stop immediately", async () => {
  const delays = []; let now = 0, calls = 0;
  const reply = {answer:'ok'};
  assert.equal(await waitForChatRun(async()=>{
    if (++calls === 1) throw Object.assign(new Error('limited'),{status:429,retryAfterMs:12000});
    return {status:'COMPLETED',response:reply};
  },undefined,async ms=>{delays.push(ms);now+=ms;},20000,{now:()=>now}),reply);
  assert.deepEqual(delays,[12000]);
  for (const status of [401,403,404]) {
    let count = 0;
    await assert.rejects(waitForChatRun(async()=>{count++;throw Object.assign(new Error('denied'),{status});},undefined,async()=>{}),e=>e.status===status);
    assert.equal(count,1);
  }
});

test("pending record survives query failure and refresh without being overwritten", async () => {
  const { readPendingChat, savePendingChat, clearPendingChat, ChatRunPending } = await import('../src/chatRun.ts');
  const data = new Map();
  const storage = {getItem:key=>data.get(key)??null,setItem:(key,value)=>data.set(key,value),removeItem:key=>data.delete(key)};
  const original = {version:2,runId:'original',conversationId:'conversation',cancelRequestId:'stable-stop',stopRequested:true};
  savePendingChat(storage,original);
  await assert.rejects(waitForChatRun(async()=>{throw Object.assign(new Error('offline'),{status:0});},undefined,async()=>{},0),ChatRunPending);
  assert.deepEqual(readPendingChat(storage),original);
  assert.throws(()=>savePendingChat(storage,{...original,runId:'new'}),ChatRunPending);
  clearPendingChat(storage,'old-callback');
  assert.deepEqual(readPendingChat(storage),original);
  clearPendingChat(storage,'original');
  assert.equal(readPendingChat(storage),null);
  storage.setItem('fitness.pending-chat',JSON.stringify({runId:'legacy',conversationId:'conversation'}));
  const restored = readPendingChat(storage);
  assert.equal(restored.version,2);
  assert.equal(readPendingChat(storage).cancelRequestId,restored.cancelRequestId);
});

test("single-request timeout retries while lifecycle abort cancels the entire recovery", async () => {
  let calls = 0;
  await assert.rejects(waitForChatRun(async()=>{
    if (++calls === 1) throw new DOMException('request timeout','TimeoutError');
    return {status:'CANCELLED'};
  },undefined,async()=>{}),ChatRunCancelled);
  const controller = new AbortController();
  await assert.rejects(waitForChatRun(async()=>{
    controller.abort();throw Object.assign(new Error('offline'),{status:0});
  },controller.signal,async()=>{}),e=>e.name==='AbortError');
});
