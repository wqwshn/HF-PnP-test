// Run the actual import/cache handlers with a small DOM stub; no browser dependency.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(__dirname + '/index.html', 'utf8');
const source = html.slice(html.indexOf('async function loaded(ds)'), html.indexOf('async function load(path)'));
const lines = ['function key(', 'function saveLocal('].map(prefix => html.split('\n').find(line => line.startsWith(prefix))).join('\n');
const elements = new Map();
const storage = new Map();
const ds = {session:'test',name:'test_DOWN.csv',raw_sha256:'raw',marker_sha256:'new-marker',samples:100,duration:1,
  markers:[{marker:'1',sample:50,distance:1,force:null},{marker:'2',sample:90,distance:-.2,force:.5}],defaults:{direction:'DOWN',zero:0}};
const context = vm.createContext({
  dataset:null,revision:0,latest:null,notes:[],fmt:(v,d)=>Number(v).toFixed(d),
  $: id => {if(!elements.has(id)) elements.set(id,{value:0,classList:{add(){}},replaceChildren(){},appendChild(){}}); return elements.get(id);},
  document:{createElement:()=>({})},
  localStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v)},
  setConfig:c=>{elements.get('direction').value=c.direction;},
  config:()=>ds.defaults,updatePointWindow(){},fillNotes(){},schedule(){},status(){},
});
elements.set('direction',{value:'DOWN'});
vm.runInContext(lines+'\n'+source,context);
const key='fig1gh:raw:DOWN';
const empty=[[null,null],[null,null]];
(async()=>{
  for(const saved of [{config:ds.defaults,notes:empty},{marker_sha256:'old-marker',config:ds.defaults,notes:empty}]){
    storage.set(key,JSON.stringify(saved));
    await context.loaded(ds);
    assert.equal(context.notes[0][0],1,'stale cache must not override marker file');
  }
  storage.set(key,JSON.stringify({marker_sha256:'new-marker',config:ds.defaults,notes:[[.9,null],[-.3,.6]]}));
  await context.loaded(ds);
  assert.equal(context.notes[0][0],.9,'current marker edits must survive reload');
  context.saveLocal();
  assert.equal(JSON.parse(storage.get(key)).marker_sha256,'new-marker');
  const reset = html.split('\n').find(line=>line.startsWith("$('reloadNotes').onclick="));
  vm.runInContext(reset,context);
  elements.get('reloadNotes').onclick();
  assert.equal(context.notes[0][0],1,'reset restores file values');
  assert.equal(context.notes[1][1],.5);
  console.log('PASS: legacy cache, changed marker, matching cache, identity save, file-value recovery');
})().catch(e=>{console.error(e);process.exitCode=1;});
