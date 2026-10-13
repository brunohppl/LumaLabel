const fs=require('fs'), {JSDOM}=require('jsdom');
let pass=0,fail=0;
const ok=(l,c)=>{ c?(pass++,console.log('✓ '+l)):(fail++,console.log('✗ FAIL '+l)); };
const DATE='2026-08-20';
const PAYLOAD={teams:[{id:'T1',name:'Nemo',vehicle:'Nemo',function:'transport',date:DATE,members:['Jo']},
                      {id:'T2',name:'Marlin',vehicle:'Marlin',function:'styling',date:DATE,members:['Addy']}],
  schedule:[{id:'E1',job_id:'J1',team_id:'T1',type:'install',date:DATE,start_time:'09:00',duration:90}],
  tasks:[{id:'K1',team_id:'T1',date:DATE,kind:'opening',title:'Fuel up',duration:15},
         {id:'K2',team_id:'T1',date:DATE,kind:'closing',title:'Sweep',duration:10}],
  jobs:[{id:'J1',job_ref:'QU-1',address:'1 A St, Ascot'}], loads:[]};

const html=fs.readFileSync('/mnt/user-data/outputs/today.html','utf8');
const js=fs.readFileSync('/mnt/user-data/outputs/static/dayview.js','utf8');
const dom=new JSDOM(html,{runScripts:'dangerously',pretendToBeVisual:true,url:'http://x/today',
  beforeParse(w){
    w.fetch=async(url)=>String(url).includes('/api/runsheet/')
      ? {ok:true,status:200,json:async()=>PAYLOAD}
      : {ok:true,status:200,json:async()=>([])};
    w.alert=()=>{};
  }});
const w=dom.window,d=w.document;
setTimeout(()=>{
  const s=d.createElement('script'); s.textContent=js; d.body.appendChild(s);
  setTimeout(()=>{
    try{ w.DayView.render(PAYLOAD, new w.Date()); }catch(e){ console.log('render threw:', e.message); }
    const caps=[...d.querySelectorAll('.dv-cap-box')];
    ok('caps drawn for the crew with tasks', caps.length===2);
    ok('opening names its task', caps.some(c=>/Fuel up/.test(c.textContent)));
    ok('closing names its task', caps.some(c=>/Sweep/.test(c.textContent)));
    ok('durations shown', caps.some(c=>/15m/.test(c.textContent)));
    ok('Before and After labels on the ruler',
       /Before/.test(d.body.textContent) && /After/.test(d.body.textContent));
    ok('the crew with none has an empty cap, not a box',
       d.querySelectorAll('.dv-cap').length > caps.length);
    ok('they are not drawn as timeline blocks',
       ![...d.querySelectorAll('.blk')].some(b=>/Fuel up|Sweep/.test(b.textContent)));
    console.log(`\n${pass} passed, ${fail} failed`);
    process.exit(fail?1:0);
  },250);
},400);
