// The Monday page's sync history.
const fs=require('fs'), {JSDOM}=require('jsdom');
let pass=0,fail=0;
const ok=(l,c)=>{ c?(pass++,console.log('✓ '+l)):(fail++,console.log('✗ FAIL '+l)); };
const sleep=ms=>new Promise(r=>setTimeout(r,ms));

const RUNS=[
 {id:'1',trigger:'scheduled',status:'ok',started_at:'2026-10-18T15:00:00Z',duration_ms:8400,
  dates_updated:12,completed_updated:3,tiles_created:5,skipped_errors:0,unmatched:0,writes_remaining:0},
 {id:'2',trigger:'manual',status:'ok',started_at:'2026-10-17T04:12:00Z',duration_ms:6100,
  dates_updated:4,skipped_errors:2,unmatched:7,writes_remaining:0},
 {id:'3',trigger:'scheduled',status:'error',started_at:'2026-10-16T15:00:00Z',
  error:'Monday token rejected'},
 {id:'4',trigger:'scheduled',status:'running',started_at:'2026-10-15T15:00:00Z'},
 {id:'5',trigger:'manual',status:'ok',started_at:'2026-10-14T01:00:00Z',duration_ms:900,
  dates_updated:0,writes_remaining:40}];

const html=fs.readFileSync('/mnt/user-data/outputs/monday.html','utf8');
const dom=new JSDOM(html,{runScripts:'dangerously',pretendToBeVisual:true,url:'https://x/monday',
  beforeParse(w){
    w.fetch=async(url)=>{
      if(String(url).includes('/api/monday/runs'))
        return {ok:true,json:async()=>({success:true,runs:RUNS})};
      return {ok:true,json:async()=>({groups:{},detected_columns:{}})};
    };
    w.alert=()=>{};
  }});
const w=dom.window,d=w.document;

(async()=>{
  await sleep(400);
  const rows=[...d.querySelectorAll('.run')];
  ok('a history section exists', !!d.getElementById('run-list'));
  ok('one row per run', rows.length===5);

  ok('a clean run lists what it did', /12 dates, 3 completed, 5 tiles made/.test(rows[0].textContent));
  ok('and says it was scheduled', /Scheduled/.test(rows[0].textContent));
  ok('with how long it took', /8\.4s/.test(rows[0].textContent));

  ok('a manual run is marked manual', /Manual/.test(rows[1].textContent));
  ok('problems are shown, not hidden', /2 errors, 7 unmatched/.test(rows[1].textContent));
  ok('and flagged', rows[1].classList.contains('warn'));

  ok('a failed run shows the reason', /Monday token rejected/.test(rows[2].textContent));
  ok('and is marked as an error', rows[2].classList.contains('error'));

  // the case the start-row exists for
  ok('a run that never finished is visible', /Did not finish/.test(rows[3].textContent));
  ok('and distinguishable', rows[3].classList.contains('running'));

  ok('hitting the write limit is called out', /hit the limit/.test(rows[4].textContent));
  ok('a run that changed nothing says so', /nothing to change/.test(rows[4].textContent));

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail?1:0);
})();
