// Opening and closing tasks: scheduler strips, team view bands, day view caps.
const fs=require('fs'), {JSDOM}=require('jsdom');
let pass=0,fail=0;
const ok=(l,c)=>{ c?(pass++,console.log('✓ '+l)):(fail++,console.log('✗ FAIL '+l)); };
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const DATE='2026-08-20';

const TEAMS=[{id:'T1',name:'Nemo',vehicle:'Nemo',function:'transport',date:DATE,sort_order:0},
             {id:'T2',name:'Marlin',vehicle:'Marlin',function:'styling',date:DATE,sort_order:1}];
const TASKS=[
  {id:'K1',team_id:'T1',date:DATE,kind:'opening',title:'Fuel up',duration:15,notes:'BP on Ipswich Rd'},
  {id:'K2',team_id:'T1',date:DATE,kind:'opening',title:'Check straps',duration:20},
  {id:'K3',team_id:'T1',date:DATE,kind:'closing',title:'Sweep the bay',duration:10},
  {id:'K4',team_id:'T1',date:DATE,kind:null,title:'Normal task',start_time:'10:00',duration:60}];
const JOBS=[{id:'J1',job_ref:'QU-1',address:'1 A St, Ascot'}];
const SCHED=[{id:'E1',job_id:'J1',team_id:'T1',type:'install',date:DATE,start_time:'09:00',duration:60}];

// ── Scheduler ──
(async()=>{
  const html=fs.readFileSync('/mnt/user-data/outputs/runsheet.html','utf8');
  const dom=new JSDOM(html,{runScripts:'dangerously',url:'http://localhost/scheduler',beforeParse(w){
    w.fetch=async(url)=>String(url).includes('/api/runsheet/')
      ? {ok:true,status:200,json:async()=>({teams:TEAMS,schedule:SCHED,tasks:TASKS,jobs:JOBS,loads:[],jobs_nearby:[]})}
      : {ok:true,status:200,json:async()=>([])};
    w.alert=()=>{};
  }});
  const w=dom.window,d=w.document;
  await sleep(400);

  ok('an Opening mode button exists', !!d.getElementById('mode-opening'));
  ok('and a Closing one', !!d.getElementById('mode-closing'));
  w.setMode('opening'); await sleep(30);
  ok('its own description field shows', d.getElementById('oc-fields').style.display==='block');
  ok('the start time is hidden', d.getElementById('time-field').style.display==='none');
  ok('duration stays available', !!d.getElementById('entry-dur'));
  w.setMode('job'); await sleep(20);
  ok('switching back restores the time field', d.getElementById('time-field').style.display==='block');

  const strips=[...d.querySelectorAll('.rs-oc')];
  ok('strips rendered for the crew with tasks', strips.length===2);
  ok('opening lists both its tasks', /Fuel up/.test(strips[0].textContent) && /Check straps/.test(strips[0].textContent));
  ok('and totals their duration', /35m|35 m/.test(strips[0].textContent));
  ok('a note is flagged', /📝/.test(strips[0].textContent));
  ok('closing is separate', /Sweep the bay/.test(strips[1].textContent));
  ok('the crew with none shows no strip',
     ![...d.querySelectorAll('.rs-team-hdr')].slice(1).some(x=>/Opening/.test(x.textContent)));
  ok('they are NOT drawn on the timed grid',
     ![...d.querySelectorAll('.rs-tile')].some(t=>/Fuel up|Sweep the bay/.test(t.textContent)));
  ok('the ordinary task still is',
     [...d.querySelectorAll('.rs-tile')].some(t=>/Normal task/.test(t.textContent)));
  ok('and they are not flagged as orphans',
     ![...d.querySelectorAll('.rs-tray-tile')].some(t=>/Fuel up/.test(t.textContent)));

  // ── Team view ──
  const th=fs.readFileSync('/mnt/user-data/outputs/today.html','utf8');
  const d2=new JSDOM(th,{runScripts:'dangerously',url:'http://localhost/today',beforeParse(w2){
    w2.fetch=async(url)=>String(url).includes('/api/runsheet/')
      ? {ok:true,status:200,json:async()=>({teams:TEAMS,schedule:SCHED,tasks:TASKS,jobs:JOBS,loads:[]})}
      : {ok:true,status:200,json:async()=>([])};
    w2.alert=()=>{};
  }});
  await sleep(400);
  const doc=d2.window.document;
  const bands=[...doc.querySelectorAll('.oc-band')];
  ok('bands on the team view', bands.length===2);
  ok('opening band first', bands[0].classList.contains('opening'));
  ok('notes are shown without tapping', /Ipswich Rd/.test(bands[0].textContent));
  ok('durations shown', /15m/.test(bands[0].textContent));
  ok('total shown', /35m/.test(bands[0].textContent));
  ok('no tick circles', !doc.querySelector('.oc-band input[type=checkbox]'));
  ok('they are not job cards', ![...doc.querySelectorAll('.card')].some(c=>/Fuel up/.test(c.textContent)));

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail?1:0);
})();
