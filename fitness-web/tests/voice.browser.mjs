// 隔离浏览器验收：业务/API 为明确夹具，真实 MediaRecorder/Web Audio/音频播放。
// 使用已有 Playwright：PLAYWRIGHT_MODULE=/path/to/playwright/index.mjs npm run test:voice-browser
import assert from "node:assert/strict";
import { readFile, mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ?? "playwright");
const baseUrl = process.env.VOICE_BROWSER_URL ?? "http://127.0.0.1:15173";
const root = resolve("..");
const sample = resolve(root, "output/voice/live-recording.wav");
const reply = await readFile(resolve(root, "output/voice/live-reply.wav"));
const browser = await chromium.launch({
  executablePath: process.env.VOICE_CHROME_PATH ?? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  headless:true,
  args:["--use-fake-ui-for-media-stream","--use-fake-device-for-media-stream",`--use-file-for-fake-audio-capture=${sample}`],
});
const report = { api:"mocked fixtures", browser:"isolated Chrome", checks:[], consoleErrors:[] };
let transcriptions = 0, chats = 0, segments = 0, failAsr = false, postedWav = null;
let lastRun = "", lastMessage = "";
const session = {authenticated:true,subject:"browser-test",organization_id:"test",roles:["STUDENT"],
  csrf_token:"fixture-csrf",expires_at:Math.floor(Date.now()/1000)+1800,session_expires_at:Math.floor(Date.now()/1000)+3600};
try {
  const context = await browser.newContext({permissions:["microphone"],viewport:{width:1360,height:950}});
  const page = await context.newPage();
  page.on("pageerror",error=>report.consoleErrors.push(error.message));
  await page.route("**/api/**", async route=>{
    const request = route.request(), path = new URL(request.url()).pathname;
    const json = (body,status=200)=>route.fulfill({status,contentType:"application/json",body:JSON.stringify(body)});
    if (path.endsWith("/auth/config")) return json({enabled:true,demo_identity_allowed:false});
    if (path.endsWith("/auth/session")) return json(session);
    if (path.endsWith("/agent/capabilities")) return json({catalog_version:"voice-browser-fixture",roles:["STUDENT"],items:[]});
    if (path.endsWith("/voice/config")) return json({enabled:true,max_recording_seconds:60,max_upload_bytes:2097152,max_spoken_chars:15360});
    if (path.endsWith("/voice/transcriptions")) {
      transcriptions++; postedWav=request.postDataBuffer();
      assert.equal(request.headers()["content-type"],"audio/wav");
      assert.equal(request.headers()["x-csrf-token"],"fixture-csrf");
      assert.equal(postedWav.toString("ascii",0,4),"RIFF");
      assert.equal(postedWav.readUInt32LE(24),16000); assert.equal(postedWav.readUInt16LE(22),1);
      if(failAsr) { failAsr=false; return json({code:"VOICE_TIMEOUT",message:"识别测试超时，请重试"},504); }
      return json({text:"请先热身5分钟，再开始训练。",duration_ms:1000});
    }
    if (path.endsWith("/chat/stream")) {
      chats++; const body=request.postDataJSON(); lastRun=body.run_id; lastMessage=body.message;
      const result={conversation_id:body.conversation_id,answer:"先热身五分钟，再开始训练。",route:"FITNESS_COACHING",tool_steps:0,input_tokens:1,output_tokens:1};
      const events=[["run.started",{}],["answer.delta",{text:result.answer}],["run.completed",{response:result}]];
      return route.fulfill({status:200,contentType:"text/event-stream",body:events.map(([event,data],i)=>
        `event: ${event}\ndata: ${JSON.stringify({run_id:lastRun,sequence:i+1,event,data})}\n\n`).join("")});
    }
    if (/\/voice\/replies\/[^/]+$/.test(path)) return json({run_id:lastRun,revision:"a".repeat(64),segment_count:2,segments:[{index:0,text:"先热身"},{index:1,text:"再训练"}]});
    if (path.includes("/segments/")) {
      segments++; assert.equal(request.postDataJSON().revision,"a".repeat(64));
      assert.equal(request.headers()["x-csrf-token"],"fixture-csrf");
      return route.fulfill({status:200,contentType:"audio/wav",body:reply});
    }
    return json({message:"Unknown browser fixture route"},404);
  });
  await page.goto(baseUrl);
  const input = page.getByRole("textbox",{name:"发送给健身助手的消息"});
  await page.getByRole("button",{name:"录音",exact:true}).waitFor();
  await page.getByRole("button",{name:"录音",exact:true}).isEnabled().then(value=>assert.equal(value,true));
  await input.fill("这是文字草稿。");
  await page.getByRole("button",{name:"录音",exact:true}).click();
  await page.getByRole("button",{name:"结束录音",exact:true}).waitFor();
  await page.waitForTimeout(1300);
  await page.getByRole("button",{name:"结束录音",exact:true}).click();
  await page.getByText("语音已转为文字，可修改后发送").waitFor();
  assert.equal(await input.inputValue(),"这是文字草稿。\n请先热身5分钟，再开始训练。");
  assert.equal(chats,0); assert.ok(postedWav.length > 44);
  report.checks.push("真实浏览器录音和 WAV 归一化，识别后保留草稿且不自动发送");
  await page.getByRole("checkbox",{name:"语音回复"}).check();
  await page.getByRole("button",{name:"发送",exact:false}).last().click();
  await page.getByText("语音输入",{exact:true}).waitFor();
  await page.getByRole("button",{name:/暂停|点击播放/,exact:true}).waitFor();
  assert.equal(chats,1); assert.equal(lastMessage,"这是文字草稿。\n请先热身5分钟，再开始训练。");
  const ready = page.getByRole("button",{name:"点击播放",exact:true});
  if(await ready.count()) await ready.click();
  await page.getByRole("button",{name:"暂停",exact:true}).click();
  await page.getByRole("button",{name:"继续播放",exact:true}).waitFor();
  await page.getByRole("button",{name:"继续播放",exact:true}).click();
  await page.getByRole("button",{name:"停止播放",exact:true}).click();
  await page.getByRole("button",{name:"朗读",exact:true}).waitFor();
  assert.equal(chats,1); assert.ok(segments>=1 && segments<=2);
  report.checks.push("一次发送、语音输入标记、自动朗读、暂停继续、停止播放");
  await page.getByRole("button",{name:"录音",exact:true}).click();
  await page.getByRole("button",{name:"结束录音",exact:true}).waitFor();
  await page.getByRole("button",{name:"取消",exact:true}).click();
  assert.equal(transcriptions,1);
  report.checks.push("取消录音不请求 ASR 或 Agent");
  failAsr=true;
  await page.getByRole("button",{name:"录音",exact:true}).click();
  await page.getByRole("button",{name:"结束录音",exact:true}).waitFor();
  await page.waitForTimeout(1000);
  await page.getByRole("button",{name:"结束录音",exact:true}).click();
  await page.getByRole("button",{name:"重试识别",exact:true}).waitFor();
  await page.getByRole("button",{name:"重试识别",exact:true}).click();
  await page.getByText("语音已转为文字，可修改后发送").waitFor();
  assert.equal(chats,1); assert.equal(transcriptions,3);
  report.checks.push("识别超时可重试，重试不重跑 Agent");
  await page.setViewportSize({width:390,height:844});
  await page.getByRole("button",{name:"录音",exact:true}).click();
  await page.getByRole("button",{name:"结束录音",exact:true}).waitFor();
  await page.getByRole("button",{name:"取消",exact:true}).click();
  report.checks.push("390px 移动布局录音控制可操作");
  await mkdir(resolve(root,"output/voice"),{recursive:true});
  await page.screenshot({path:resolve(root,"output/voice/browser-mobile.png"),fullPage:true});
  await page.setViewportSize({width:1360,height:950});
  await page.screenshot({path:resolve(root,"output/voice/browser-desktop.png"),fullPage:true});
  assert.deepEqual(report.consoleErrors,[]);
  report.passed=true;
  await writeFile(resolve(root,"output/voice/browser-check.json"),JSON.stringify(report,null,2));
  console.log(JSON.stringify(report));
} finally { await browser.close(); }
