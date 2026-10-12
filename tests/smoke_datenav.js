// Scheduler: jumping to a date, and arrow keys stepping days.
const fs=require('fs'), {JSDOM}=require('jsdom');
const html=fs.readFileSync('/mnt/user-data/outputs/runsheet.html','utf8');
let pass=0,fail=0;
const ok=(l,c)=>{ c?(pass++,console.log('✓ '+l)):(fail++,console.log('✗ FAIL '+l)); };
let loads=[];

const dom=new JSDOM(html,{runScripts:'dangerously',url:'http://localhost/scheduler',
  beforeParse(w){
    w.fetch=async(url)=>{
      const u=String(url);
      if(u.includes('/api/runsheet/')) { loads.push(u.split('/api/runsheet/')[1].split('?')[0]);
        return {ok:true,status:200,json:async()=>({teams:[],schedule:[],tasks:[],jobs:[],loads:[]})}; }
      return {ok:true,status:200,json:async()=>([])};
    };
    w.alert=()=>{};
  }});
const w=dom.window,d=w.document;
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const key=(k,target)=>{
  const e=new w.KeyboardEvent('keydown',{key:k,bubbles:true,cancelable:true});
  (target||d.body).dispatchEvent(e); return e;
};

(async()=>{
  await sleep(400);
  ok('the date label is a button', d.getElementById('date-label').tagName==='BUTTON');
  ok('a hidden date input exists', !!d.getElementById('date-picker'));
  ok('it is out of the way visually',
     /opacity:0/.test(d.getElementById('date-picker').getAttribute('style')));

  // jumping to a date
  loads=[];
  w.pickDate('2026-03-17');
  await sleep(80);
  ok('picking a date loads it', loads.some(x=>x==='2026-03-17'));
  ok('and the label follows', /17 March 2026/.test(d.getElementById('date-label').textContent));

  // backwards too
  loads=[];
  w.pickDate('2025-12-01');
  await sleep(80);
  ok('a date in the past works the same', loads.some(x=>x==='2025-12-01'));

  // nonsense is ignored
  loads=[];
  w.pickDate(''); w.pickDate('not-a-date');
  await sleep(60);
  ok('an empty or invalid value loads nothing', loads.length===0);

  // arrow keys
  w.pickDate('2026-03-17'); await sleep(60);
  loads=[];
  key('ArrowLeft'); await sleep(60);
  ok('left steps back a day', loads.some(x=>x==='2026-03-16'));
  loads=[];
  key('ArrowRight'); key('ArrowRight'); await sleep(60);
  ok('right steps forward', loads.some(x=>x==='2026-03-18'));

  // must not steal keys from fields
  const input=d.createElement('input'); d.body.appendChild(input);
  loads=[];
  key('ArrowLeft', input); await sleep(60);
  ok('typing in a field keeps its arrow keys', loads.length===0);

  const ta=d.createElement('textarea'); d.body.appendChild(ta);
  loads=[];
  key('ArrowRight', ta); await sleep(60);
  ok('and so does a textarea', loads.length===0);

  // not while a popover is open
  const ov=d.querySelector('.pop-overlay');
  if(ov){
    ov.classList.add('open');
    loads=[];
    key('ArrowLeft'); await sleep(60);
    ok('an open popover keeps the arrows', loads.length===0);
    ov.classList.remove('open');
  }

  // modifiers are left to the browser
  loads=[];
  const e=new w.KeyboardEvent('keydown',{key:'ArrowLeft',metaKey:true,bubbles:true,cancelable:true});
  d.body.dispatchEvent(e); await sleep(60);
  ok('cmd/ctrl + arrow is not intercepted', loads.length===0);

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail?1:0);
})();
