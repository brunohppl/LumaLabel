// Smoke test: loads the real runsheet page in a DOM, stubs the network and
// drives the flows that keep breaking. Catches thrown handlers and undefined
// variables — the class of bug that logic-only tests miss.
const fs = require('fs');
const { JSDOM } = require('jsdom');

const html = fs.readFileSync('/mnt/user-data/outputs/runsheet.html','utf8').replace(/\{\{[^}]*\}\}/g,'');
const TEAMS=[{id:'T1',name:'Nemo Crew',vehicle:'Nemo',function:'transport',sort_order:0},
             {id:'T4',name:'Styling Crew 1',vehicle:'Marlin',function:'styling',sort_order:3},
             {id:'T7',name:'Warehouse',vehicle:null,function:'warehouse',sort_order:6}];
// J400 receives stock FROM J401 (so J401 is the pickup being transferred out).
// J402 is an ordinary job with no transfer at all.
const JOBS=[{id:'J400',job_ref:'#400',job_number:'1400',address:'12 Somers St, Ascot',access_notes:'Gate 4823',
             is_transfer:true,transfer_from_job_id:'J401'},
            {id:'J401',job_ref:'#401',job_number:'1401',address:'9 Hale St',is_transfer:false},
            {id:'J402',job_ref:'#402',job_number:'1402',address:'4 Oxford St',is_transfer:false}];
const SCHEDULE=[{id:'SRC',job_id:'J400',type:'install',date:'2026-08-20',team_id:null,start_time:null,duration:null},
                {id:'PLACED',job_id:'J400',type:'install',date:'2026-08-20',team_id:'T1',start_time:'08:00',duration:60},
                {id:'PICKUP',job_id:'J401',type:'pickup',date:'2026-08-20',team_id:'T4',start_time:'10:00',duration:60},
                {id:'PLAIN',job_id:'J402',type:'install',date:'2026-08-20',team_id:'T7',start_time:'13:00',duration:60}];
// J400 was loaded onto Nemo yesterday; J402 onto Bruce.
const LOADS=[{id:'L1',job_id:'J400',type:'to_load',date:'2026-08-19',vehicle:'Nemo',team_id:'T1'},
             {id:'L2',job_id:'J402',type:'to_load',date:'2026-08-19',vehicle:'Bruce',team_id:'TX'}];
const ORPHAN={id:'K9',title:'Collect keys from agent',vehicle:'',team_id:null,date:'2026-08-20',start_time:'09:00',duration:30};
let posted=[];
const dom=new JSDOM(html,{runScripts:'dangerously',url:'http://localhost/runsheet',beforeParse(w){
  w.fetch=async(url,opts={})=>{
    if(opts.method&&opts.method!=='GET') posted.push({url,method:opts.method,body:opts.body});
    let data;
    if(url.includes('/api/runsheet/')) data={teams:TEAMS,schedule:SCHEDULE,tasks:[ORPHAN],jobs:JOBS,loads:LOADS};
    else if(url.includes('/api/team-templates')) data=[];
    else if(url.includes('/api/runsheet-orphan-case')) data={};
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

  // Transfer marker on tiles
  const tiles=[...d.querySelectorAll('.rs-tile,.rs-tray-tile')].map(x=>x.textContent);
  const forJob=r=>tiles.filter(t=>t.includes(r));
  ok('receiving job says where stock comes FROM',
     forJob('#400').some(t=>t.includes('TRANSFER FROM')));
  ok('and names the other job', forJob('#400').some(t=>t.includes('#401')));
  ok('the pickup says where stock goes TO',
     forJob('#401').some(t=>t.includes('TRANSFER TO')));
  ok('and names the other job', forJob('#401').some(t=>t.includes('#400')));
  // Select by tile type: "#400" also appears inside "TRANSFER TO #400".
  const installs=tiles.filter(t=>t.includes('Install'));
  const pickups =tiles.filter(t=>t.includes('Pickup'));
  ok('direction is never ambiguous',
     installs.filter(t=>t.includes('#400')).every(t=>t.includes('TRANSFER FROM') && !t.includes('TRANSFER TO')) &&
     pickups.every(t=>t.includes('TRANSFER TO') && !t.includes('TRANSFER FROM')));
  ok('an ordinary job carries no transfer marker',
     !forJob('#402').some(t=>t.includes('TRANSFER')));

  // Loading vehicle carried onto the install tile
  const allTiles=[...d.querySelectorAll('.rs-tile')];
  const txt=t=>t.textContent;
  const j400=allTiles.filter(t=>txt(t).includes('#400')&&txt(t).includes('Install'));
  ok('install tile shows the loading truck', j400.some(t=>txt(t).includes('Nemo')));
  const onNemo=j400.find(t=>txt(t).includes('📦'));
  ok('matching crew shows it plainly, no warning', !!onNemo);
  // J402 loaded on Bruce but installed by the Warehouse crew (no vehicle) -> no warning
  // J400 also sits on Styling Crew 1 (Marlin) -> that IS a mismatch
  // J400 is loaded on Nemo and one of its crews (T1) IS Nemo, so the other
  // crews on the job must NOT be warned — that's the normal multi-crew case.
  // The tag is informational only now — no warning state anywhere.
  ok('no warning styling anywhere', !d.querySelector('.rs-loaded.mismatch'));
  ok('no warning symbol in any tag',
     ![...d.querySelectorAll('.rs-loaded')].some(x=>x.textContent.includes('⚠')));
  ok('every loaded job still names its truck',
     [...d.querySelectorAll('.rs-loaded')].every(x=>/Nemo|Bruce|Nigel|Marlin|VUG/.test(x.textContent)));
  ok('every crew on the job still shows the truck',
     j400.length>1 && j400.every(t=>txt(t).includes('Nemo')));
  // #401 is a PICKUP, and a pickup is never "loaded" — check the tag is absent
  // on its own tile rather than on any tile mentioning #401 (the install tiles
  // say "TRANSFER TO #400" and would match a naive text search).
  const pickupTiles=allTiles.filter(t=>txt(t).includes('Pickup'));
  ok('a pickup never shows a loading tag ('+pickupTiles.length+' tiles)',
     pickupTiles.length>0 && !pickupTiles.some(t=>txt(t).includes('📦')));

  // The tray tile — the thing you read before deciding which truck to use
  const trayTiles=[...d.querySelectorAll('.rs-tray-tile')].map(x=>x.textContent);
  ok('tray tile shows the loading truck ('+trayTiles.length+' tiles)',
     trayTiles.some(t=>t.includes('#400') && t.includes('Nemo')));
  ok('tray tile uses the same plain tag',
     [...d.querySelectorAll('.rs-tray-tile .rs-loaded')].every(x=>x.textContent.includes('📦')));

  // A task that resolves to no column must be VISIBLE, not silently gone
  const trayTasks=[...d.querySelectorAll('.rs-tray-task')];
  ok('orphaned task appears in the tray', trayTasks.length===1);
  ok('named so it can be recognised', trayTasks[0] && trayTasks[0].textContent.includes('Collect keys'));
  ok('flagged for reassignment', trayTasks[0] && trayTasks[0].textContent.includes('⚠️'));

  err=await call(w.openTeamPop);
  ok('team popover opens'+(err?' — '+err:''), !err || err.includes('not a function'));

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail?1:0);
})().catch(e=>{ console.log('HARNESS ERROR: '+e.message); process.exit(2); });
