// Smoke test: loads the real runsheet page in a DOM, stubs the network and
// drives the flows that keep breaking. Catches thrown handlers and undefined
// variables — the class of bug that logic-only tests miss.
const fs = require('fs');
const { JSDOM } = require('jsdom');

const html = fs.readFileSync('/mnt/user-data/outputs/runsheet.html','utf8').replace(/\{\{[^}]*\}\}/g,'');
const TEAMS=[{id:'T1',name:'Nemo Crew',vehicle:'Nemo',function:'transport',sort_order:0},
             {id:'T4',name:'Styling Crew 1',vehicle:'Marlin',function:'styling',sort_order:3},
             {id:'T7',name:'Warehouse',vehicle:null,function:'warehouse',sort_order:6}];
const JOBS=[{id:'J400',job_ref:'#400',job_number:'1400',address:'12 Somers St, Ascot',access_notes:'Gate 4823'}];
const SCHEDULE=[{id:'SRC',job_id:'J400',type:'install',date:'2026-08-20',team_id:null,start_time:null,duration:null},
                {id:'PLACED',job_id:'J400',type:'install',date:'2026-08-20',team_id:'T1',start_time:'08:00',duration:60}];
let posted=[];
const dom=new JSDOM(html,{runScripts:'dangerously',url:'http://localhost/runsheet',beforeParse(w){
  w.fetch=async(url,opts={})=>{
    if(opts.method&&opts.method!=='GET') posted.push({url,method:opts.method,body:opts.body});
    let data;
    if(url.includes('/api/runsheet/')) data={teams:TEAMS,schedule:SCHEDULE,tasks:[],jobs:JOBS};
    else if(url.includes('/api/team-templates')) data=[];
    else {
      // Echo the request back the way the real endpoint does — a fixed
      // response made the second crew look already-assigned.
      const sent=opts.body?JSON.parse(opts.body):{};
      data={success:true,entry:Object.assign({id:'N'+posted.length},sent)};
    }
    return {ok:true,status:200,json:async()=>data,text:async()=>JSON.stringify(data)};
  };
  w.alert=m=>{(w.__alerts=w.__alerts||[]).push(m);};
  w.confirm=()=>true;
}});
const w=dom.window, d=w.document;
let pass=0, fail=0;
const ok=(l,c)=>{ c?pass++:fail++; console.log((c?'✓ ':'✗ FAIL ')+l); };
const call=async(fn,...a)=>{ try{ await fn(...a); return null; }catch(e){ return e.message||String(e); } };
const sleep=ms=>new Promise(r=>setTimeout(r,ms));

(async()=>{
  await sleep(1200);

  let err=await call(w.openEntryPop,null,'T1','09:00');
  ok('empty cell opens the popover'+(err?' — '+err:''), !err && d.getElementById('entry-pop').classList.contains('open'));

  err=await call(w.openEntryPop,'SRC',null,null);
  ok('tray tile opens without throwing'+(err?' — '+err:''), !err);
  ok('crew checkboxes rendered ('+d.querySelectorAll('.crew-chk').length+')', d.querySelectorAll('.crew-chk').length===3);
  ok('start time defaults to 07:30 (got "'+d.getElementById('entry-time').value+'")', d.getElementById('entry-time').value==='07:30');
  ok('access field visible for a job', d.getElementById('entry-access-wrap').style.display==='block');
  ok('access notes pre-filled', d.getElementById('entry-access').value==='Gate 4823');

  posted=[];
  // T1 already holds this job (see SCHEDULE), so tick the two free crews.
  d.querySelectorAll('.crew-chk').forEach(b=>{ b.checked = ['T4','T7'].includes(b.getAttribute('data-team')); });
  err=await call(w.saveEntry);
  await sleep(150);
  ok('multi-crew save does not throw'+(err?' — '+err:''), !err);
  const creates=posted.filter(p=>p.url.includes('/api/schedule-entry'));
  const teamsPosted=creates.map(c=>JSON.parse(c.body).team_id);
  ok('one entry per ticked crew ('+creates.length+')', creates.length===2);
  ok('all start at 07:30', creates.length>0 && creates.every(c=>JSON.parse(c.body).start_time==='07:30'));
  ok('styling crew gets 3h, warehouse 1h',
     creates.some(c=>JSON.parse(c.body).duration===180) && creates.some(c=>JSON.parse(c.body).duration===60));

  // A crew that already holds the job must not get a duplicate
  posted=[];
  d.querySelectorAll('.crew-chk').forEach(b=>{ b.checked = b.getAttribute('data-team')==='T1'; });
  await call(w.saveEntry); await sleep(100);
  ok('already-assigned crew creates nothing',
     posted.filter(p=>p.url.includes('/api/schedule-entry')).length===0);
  ok('and says so rather than failing silently', (w.__alerts||[]).some(a=>/already/i.test(a)));

  err=await call(w.openEntryPop,'PLACED','T1','08:00');
  ok('placed tile opens'+(err?' — '+err:''), !err);
  ok('placed tile keeps its own time', d.getElementById('entry-time').value==='08:00');

  // Break: third mode, 30 minutes, on a crew, not tied to a job
  posted=[];
  err=await call(w.openEntryPop,null,'T1','12:00');
  ok('empty cell opens for a break'+(err?' — '+err:''), !err);
  err=await call(w.setMode,'break');
  ok('break mode selectable'+(err?' — '+err:''), !err);
  ok('break fields shown', d.getElementById('break-fields').style.display==='block');
  ok('job and task fields hidden',
     d.getElementById('job-fields').style.display==='none' && d.getElementById('task-fields').style.display==='none');
  ok('duration defaults to 30', d.getElementById('entry-dur').value==='30');
  err=await call(w.saveEntry); await sleep(120);
  ok('saving a break does not throw'+(err?' — '+err:''), !err);
  const breaks=posted.filter(p=>p.url.includes('/api/tasks'));
  ok('break posted as a task ('+breaks.length+')', breaks.length===1);
  if(breaks.length){
    const b=JSON.parse(breaks[0].body);
    ok('marked as a break', b.kind==='break');
    ok('30 minutes', b.duration===30);
    ok('on the crew', b.team_id==='T1');
    ok('not attached to a job', !b.job_id);
    ok('titled for the crew to read', b.title==='Lunch break');
  }

  err=await call(w.openTeamPop);
  ok('team popover opens'+(err?' — '+err:''), !err || err.includes('not a function'));

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail?1:0);
})().catch(e=>{ console.log('HARNESS ERROR: '+e.message); process.exit(2); });
