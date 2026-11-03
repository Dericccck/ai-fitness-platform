import assert from "node:assert/strict";
import test from "node:test";
import { encodeWav } from "../src/voice/recorder.ts";
import { VoicePlayer } from "../src/voice/player.ts";

const tick = () => new Promise(resolve => setTimeout(resolve, 0));
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function fixture(overrides = {}) {
  const calls = [], revoked = [], states = [], audios = [];
  const dependencies = {
    manifest: async runId => ({run_id:runId, revision:"v", segment_count:3, segments:[0,1,2].map(index=>({index,text:`part ${index}`}))}),
    segment: async (runId,index) => { calls.push([runId,index]); return new Blob([`${runId}:${index}`]); },
    audio: () => {
      const audio = {src:"", onended:null, onerror:null, plays:0, pauses:0,
        play:async () => { audio.plays++; }, pause:()=>{audio.pauses++;},
        removeAttribute:()=>{audio.src="";}, load:()=>{}};
      audios.push(audio); return audio;
    },
    objectUrl: blob => `blob:${blob.size}:${Math.random()}`,
    revokeUrl: url => revoked.push(url), changed: state => states.push(state),
    ...overrides,
  };
  return {player:new VoicePlayer(dependencies),calls,revoked,states,audios};
}

test("WAV encoder writes bounded mono PCM16 with clipping", async () => {
  const blob = encodeWav(new Float32Array([-2,-1,0,.5,1,2]));
  const data = new DataView(await blob.arrayBuffer());
  assert.equal(blob.type,"audio/wav");
  assert.equal(blob.size,56);
  assert.equal(data.getUint16(22,true),1);
  assert.equal(data.getUint32(24,true),16000);
  assert.equal(data.getUint16(34,true),16);
  assert.equal(data.getInt16(44,true),-32768);
  assert.equal(data.getInt16(54,true),32767);
});

test("plays in order and prefetches only the next segment", async () => {
  const {player,calls,audios,revoked} = fixture();
  await player.start("run"); await tick();
  assert.deepEqual(calls,[["run",0],["run",1]]);
  assert.equal(player.state.status,"playing");
  audios[0].onended(); await tick();
  assert.equal(player.state.index,1);
  assert.deepEqual(calls,[["run",0],["run",1],["run",2]]);
  assert.equal(revoked.length,1);
  audios[0].onended(); await tick();
  assert.equal(player.state.index,2);
  audios[0].onended();
  assert.equal(player.state.status,"ended");
  player.stop(); assert.equal(revoked.length,3);
});

test("late response after stop or account switch cannot create a Blob URL or play", async () => {
  const pending = deferred(); let created = 0;
  const {player,audios} = fixture({segment:()=>pending.promise,objectUrl:()=>{created++;return "blob:x";}});
  const task = player.start("old"); await tick();
  player.stop(); pending.resolve(new Blob(["old"])); await task;
  assert.equal(created,0); assert.equal(audios[0].plays,0);
  assert.equal(player.state.status,"idle");
});

test("switching replies isolates generations and stops prior audio", async () => {
  const pending = deferred();
  const {player,audios} = fixture({manifest:runId => runId === "old" ? pending.promise : Promise.resolve({run_id:runId,revision:"v",segment_count:1,segments:[{index:0,text:"new"}]})});
  const task = player.start("old");
  await player.start("new");
  pending.resolve({run_id:"old",revision:"v",segment_count:1,segments:[{index:0,text:"old"}]});
  await task;
  assert.equal(player.state.runId,"new"); assert.equal(audios[0].plays,0);
  assert.equal(audios[1].plays,1);
  player.stop();
});

test("autoplay rejection reuses ready audio on user click", async () => {
  const {player,calls,audios} = fixture();
  const create = player.start("run");
  await tick(); await create;
  audios[0].play = async () => { throw new DOMException("blocked","NotAllowedError"); };
  await player.resume();
  assert.equal(player.state.status,"ready");
  const prior = calls.length;
  audios[0].play = async () => {};
  await player.resume();
  assert.equal(player.state.status,"playing"); assert.equal(calls.length,prior);
  player.stop();
});

test("pause during preparation waits for resume without playing", async () => {
  const pending = deferred();
  const {player,audios} = fixture({segment:()=>pending.promise});
  const task = player.start("r"); await tick(); player.pause();
  pending.resolve(new Blob(["audio"])); await task;
  assert.equal(player.state.status,"paused"); assert.equal(audios[0].plays,0);
  await player.resume(); assert.equal(audios[0].plays,1);
  player.stop();
});

test("failed segment retry keeps the same run and position", async () => {
  let failed = true;
  const {player,calls,audios} = fixture({segment:async (run,index)=>{
    calls.push([run,index]); if(index === 1 && failed) throw Error("failed segment"); return new Blob(["ok"]);
  }});
  await player.start("r"); await tick(); audios[0].onended(); await tick();
  assert.equal(player.state.status,"error"); assert.equal(player.state.index,1);
  failed = false; await player.retry();
  assert.equal(player.state.runId,"r"); assert.equal(player.state.index,1);
  assert.equal(player.state.status,"playing"); player.stop();
});

test("changed speech revision reloads manifest before retrying", async () => {
  let manifests=0;
  const {player,audios} = fixture({
    manifest:async run=>({run_id:run,revision:`v${++manifests}`,segment_count:1,segments:[{index:0,text:"reply"}]}),
    segment:async (_run,_index,revision)=>{
      if(revision === "v1") throw Object.assign(new Error("changed"),{status:409});
      return new Blob(["new revision"]);
    },
  });
  await player.start("r"); assert.equal(player.state.status,"error");
  await player.retry();
  assert.equal(manifests,2); assert.equal(player.state.status,"playing");
  assert.equal(audios[1].plays,1); player.stop();
});
