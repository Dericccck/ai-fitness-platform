import assert from "node:assert/strict";
import test from "node:test";
import { AuthClient } from "../src/auth.ts";

const session = { authenticated: true, subject: "u", organization_id: "o", roles: ["STUDENT"], csrf_token: "csrf", expires_at: Math.floor(Date.now()/1000)+300, session_expires_at: Math.floor(Date.now()/1000)+3600 };
const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: {"Content-Type":"application/json"} });

test("cookie mode has CSRF but never browser identity or refresh token", async () => {
  const client = new AuthClient("", async (url, init) => {
    assert.equal(init.credentials, "same-origin");
    return json(url.endsWith("config") ? {enabled:true, demo_identity_allowed:false} : session);
  }, () => {});
  const headers = await client.headers("POST", () => { throw Error("demo must not be used"); });
  assert.equal(headers.get("X-CSRF-Token"), "csrf");
  assert.equal(headers.get("X-Agent-Context"), null);
});

test("parallel expired requests share one refresh, business requests are not retried", async () => {
  let refreshes = 0;
  const client = new AuthClient("", async (url) => {
    if (url.endsWith("config")) return json({enabled:true, demo_identity_allowed:false});
    if (url.endsWith("refresh")) { refreshes++; await new Promise(resolve=>setTimeout(resolve,10)); return json(session); }
    return json({...session, expires_at: Math.floor(Date.now()/1000)+10});
  }, () => {});
  await Promise.all([client.headers("POST", ()=>""), client.headers("POST", ()=>"")]);
  assert.equal(refreshes,1);
});

test("production unconfigured auth never falls back to demo", async () => {
  const client = new AuthClient("", async () => json({enabled:false, demo_identity_allowed:false}), () => {});
  await assert.rejects(client.headers("POST", ()=>"fake-signature"), error=>error.status===503);
});

test("account switch stops current action and clears prior scope", async () => {
  let changed = 0;
  const client = new AuthClient("", async url => json(url.endsWith("config") ? {enabled:true, demo_identity_allowed:false} : {...session, subject:"other"}), () => changed++);
  client.session = session;
  await assert.rejects(client.headers("POST", ()=>""), error=>error.status===401);
  assert.equal(changed,1);
});

test("401 does not trigger repeated login or refresh", async () => {
  const calls = [];
  const client = new AuthClient("", async url => { calls.push(url); return url.endsWith("config") ? json({enabled:true, demo_identity_allowed:false}) : json({},401); }, () => {});
  await assert.rejects(client.headers("POST", ()=>""), error=>error.status===401);
  assert.deepEqual(calls,["/api/v1/auth/config","/api/v1/auth/session"]);
});
