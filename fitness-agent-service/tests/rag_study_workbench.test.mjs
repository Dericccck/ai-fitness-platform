// 纯函数/事件契约测试，使用内存DOM替身；不启动或控制浏览器。
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import vm from 'node:vm';
import test from 'node:test';

const html=readFileSync(fileURLToPath(new URL('../evals/rag-study-workbench.html',import.meta.url)),'utf8');
const script=html.match(/<script>([\s\S]*?)<\/script>/)[1];
function setup(){
  const elements=new Map(), downloads=[];
  const node=()=>({value:'',checked:false,files:[],textContent:'',children:[],
    append(...rows){this.children.push(...rows);},replaceChildren(){this.children=[];},click(){}});
  const get=id=>{if(!elements.has(id))elements.set(id,node());return elements.get(id);};
  const context=vm.createContext({document:{getElementById:get,createElement:node},
    window:{addEventListener(){}},Blob,URL:{createObjectURL(blob){downloads.push(blob);return 'blob:test';},revokeObjectURL(){}},setTimeout(){}});
  vm.runInContext(script,context);
  return {get,downloads,context,load:async(id,data)=>{get(id).files=[{size:500,text:async()=>JSON.stringify(data)}];await get(id).onchange();}};
}
const packet={packet_type:'annotation',pool_sha256:'hash',source_kind:'synthetic',reviewer_id:'A1',reviewer_kind:'human',cases:[{case_id:'q01',query:'<script>not executable</script>',context:[],retrieval_query:'问题',candidates:['a'],catalog:[{id:'a',title:'标题',content:'正文'}]}]};
test('collection requires consent and preserves source provenance',async()=>{
  const app=setup();await app.load('protocolFile',{study_id:'test',source_kind:'synthetic',consent_version:'v1'});
  app.get('participant').value='anon-1';app.get('query').value='这是我的问题';app.get('reference').value='fixture-reference';
  await app.get('add').onclick();assert.match(app.get('status').textContent,/同意/);
  app.get('consent').checked=true;await app.get('add').onclick();await app.get('exportQuestions').onclick();
  const [row]=JSON.parse(await app.downloads[0].text());assert.equal(row.source_kind,'synthetic');assert.equal(row.query,'这是我的问题');assert.equal(row.consent_version,'v1');
});
test('annotation rejects unauthorized evidence and exports complete label',async()=>{
  const app=setup();await app.load('packetFile',packet);app.get('category').value='answerable';app.get('ids').value='hidden-private';app.get('rationale').value='符合问题的证据';app.get('notes').value='已经检查整个目录';
  await app.get('saveLabel').onclick();assert.match(app.get('status').textContent,/越权/);
  app.get('ids').value='a';app.get('independent').checked=true;await app.get('exportReview').onclick();
  const review=JSON.parse(await app.downloads[0].text());assert.deepEqual(review.labels[0].relevant_ids,['a']);assert.equal(review.independent_review_declared,true);
});
test('partial draft is not an independent completed review',async()=>{
  const app=setup();await app.load('packetFile',packet);await app.get('exportDraft').onclick();
  const draft=JSON.parse(await app.downloads[0].text());assert.equal(draft.independent_review_declared,false);assert.equal(draft.labels.length,0);
});
test('draft restoration rejects another pool or reviewer',async()=>{
  const app=setup();await app.load('packetFile',packet);await app.load('reviewFile',{pool_sha256:'other',reviewer_id:'A1',labels:[]});assert.match(app.get('status').textContent,/不是当前/);
});
test('untrusted question text is displayed as text and page disables network',async()=>{
  const app=setup();await app.load('packetFile',packet);assert.match(app.get('case').textContent,/<script>not executable/);assert.ok(html.includes("connect-src 'none'"));assert.ok(!script.includes('innerHTML'));assert.ok(!script.includes('fetch('));
});
test('special case IDs do not mutate the label dictionary prototype',async()=>{
  const app=setup();const special=structuredClone(packet);special.cases[0].case_id='__proto__';await app.load('packetFile',special);app.get('category').value='no_answer';app.get('rationale').value='授权目录没有答案';app.get('notes').value='全文目录已检查';app.get('independent').checked=true;await app.get('exportReview').onclick();const review=JSON.parse(await app.downloads[0].text());assert.equal(review.labels[0].case_id,'__proto__');
});
