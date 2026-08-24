// Install-date popover in jobs.html.
// The page keeps state in top-level `let` bindings, which are not window
// properties — fixtures are injected via eval so they reach those bindings.
const fs=require('fs'), {JSDOM}=require('jsdom');
const html=fs.readFileSync('/mnt/user-data/outputs/jobs.html','utf8');
let pass=0,fail=0;
const ok=(l,c)=>{ c?(pass++,console.log('✓ '+l)):(fail++,console.log('✗ FAIL '+l)); };
let patched=null, alerted='';

const dom=new JSDOM(html,{
  runScripts:'dangerously', pretendToBeVisual:true, url:'https://x.test/jobs',
  beforeParse(w){
    w.fetch=async(url,opt={})=>{
      if(opt.method==='PATCH'){ patched={url,body:JSON.parse(opt.body||'{}')};
        return {ok:true,status:200,json:async()=>({success:true})}; }
      return {ok:true,status:200,json:async()=>([])};
    };
    w.confirm=()=>true;
    w.alert=m=>{ alerted=String(m); };
  }
});
const w=dom.window, d=w.document;
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const pop=()=>d.getElementById('date-popover');
const input=()=>d.getElementById('date-popover-input');

(async()=>{
  await sleep(250);
  w.eval(`
    window.__J1={id:'J1',job_ref:'QU-1',status:'ready',runsheet_date:'2026-09-01'};
    jobsById=new Map([['J1',window.__J1]]); lastFetchedJobs=[window.__J1];
    renderJobs=()=>{}; renderRunsheet=()=>{};
  `);
  const J1=w.__J1;

  // ── The bug being fixed: a real, visible input, no showPicker ──
  ok('a real date input exists in the DOM', !!input() && input().type==='date');
  ok('it is not the old 1px hidden hack',
     !/opacity:0/.test(input().getAttribute('style')||''));
  ok('no showPicker() call remains in the page',
     !/\.showPicker\s*\(/.test(html));

  // ── Opening ──
  w.openInstallDatePicker('J1', null);
  await sleep(40);
  ok('popover opens', pop().classList.contains('open'));
  ok('pre-filled with the current date', input().value==='2026-09-01');
  ok('titled with the job reference', /QU-1/.test(d.getElementById('date-popover-title').textContent));
  ok('remove offered when a date exists', d.getElementById('date-popover-remove').style.display!=='none');
  // Layout: three uppercase nowrap buttons overflowed a 300px box, so the
  // destructive action gets its own row and is allowed to wrap.
  const rm=d.getElementById('date-popover-remove');
  ok('remove sits on its own row', rm.parentElement.id==='date-popover');
  ok('and is not inside the Cancel/Save row',
     !rm.closest('.runsheet-popover-actions'));
  ok('and may wrap rather than overflow', /date-remove-row/.test(rm.className));

  // ── Saving a new date ──
  patched=null;
  input().value='2026-09-15';
  await w.saveDatePopover();
  await sleep(60);
  ok('save sends a PATCH', !!patched);
  ok('with the chosen date', patched && patched.body.runsheet_date==='2026-09-15');
  ok('typed as an install', patched && patched.body.runsheet_type==='install');
  ok('popover closes after saving', !pop().classList.contains('open'));

  // ── Empty date must not silently wipe the job ──
  patched=null; alerted='';
  w.openInstallDatePicker('J1', null); await sleep(30);
  input().value='';
  await w.saveDatePopover(); await sleep(40);
  ok('empty save is refused, not sent', patched===null);
  ok('and explains why', /Remove date/.test(alerted));
  ok('popover stays open to correct it', pop().classList.contains('open'));

  // ── Remove -> hold ──
  patched=null;
  await w.removeDateAndHold();
  await sleep(60);
  ok('remove sends a PATCH', !!patched);
  ok('clearing the date', patched && patched.body.runsheet_date===null);
  ok('local job goes on hold', J1.status==='on_hold');
  ok('popover closed', !pop().classList.contains('open'));

  // ── A job with no date: no Remove button offered ──
  w.eval(`
    window.__J3={id:'J3',status:'on_hold',runsheet_date:null};
    jobsById=new Map([['J3',window.__J3]]); lastFetchedJobs=[window.__J3];
  `);
  w.openInstallDatePicker('J3', null); await sleep(30);
  ok('undated job: input starts empty', input().value==='');
  ok('undated job: no remove button', d.getElementById('date-popover-remove').style.display==='none');
  w.closeDatePopover();

  // ── Installed job must not be dragged backwards ──
  w.eval(`
    window.__J2={id:'J2',status:'installed',runsheet_date:'2026-09-02'};
    jobsById=new Map([['J2',window.__J2]]); lastFetchedJobs=[window.__J2];
  `);
  w.openInstallDatePicker('J2', null); await sleep(30);
  await w.removeDateAndHold(); await sleep(50);
  ok('installed job keeps its status', w.__J2.status==='installed');
  ok('but still loses the date', w.__J2.runsheet_date===null);

  // ── Server failure must not show a state that did not happen ──
  alerted='';
  w.eval(`
    window.__J4={id:'J4',status:'ready',runsheet_date:'2026-09-03'};
    jobsById=new Map([['J4',window.__J4]]); lastFetchedJobs=[window.__J4];
    fetch=async()=>({ok:false,status:500,json:async()=>({success:false,error:'boom'})});
  `);
  w.openInstallDatePicker('J4', null); await sleep(30);
  await w.removeDateAndHold(); await sleep(50);
  ok('failure is reported', /boom/.test(alerted));
  ok('job keeps its date on failure', w.__J4.runsheet_date==='2026-09-03');
  ok('and keeps its status', w.__J4.status==='ready');

  // ── A network throw must not leave an unhandled rejection ──
  alerted='';
  w.eval(`fetch=async()=>{ throw new Error('offline'); };`);
  w.openInstallDatePicker('J4', null); await sleep(30);
  await w.removeDateAndHold(); await sleep(50);
  ok('network error is caught and shown', /offline/.test(alerted));

  // ── Cancel changes nothing ──
  w.eval(`
    window.__J5={id:'J5',status:'ready',runsheet_date:'2026-09-04'};
    jobsById=new Map([['J5',window.__J5]]); lastFetchedJobs=[window.__J5];
    fetch=async(u,o={})=>{ if(o.method==='PATCH'){ window.__leaked=true; } return {ok:true,status:200,json:async()=>({success:true})}; };
    window.__leaked=false;
  `);
  w.openInstallDatePicker('J5', null); await sleep(30);
  input().value='2026-12-25';
  w.closeDatePopover(); await sleep(30);
  ok('cancel sends nothing', w.__leaked===false);
  ok('and the job is untouched', w.__J5.runsheet_date==='2026-09-04');

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail?1:0);
})();
