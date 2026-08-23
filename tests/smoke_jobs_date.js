// Focused check on the calendar-icon menu in jobs.html.
// The page declares its state with top-level `let`, which is not a window
// property — so the fixtures are injected via eval in the page's own scope.
const fs=require('fs'), {JSDOM}=require('jsdom');
const html=fs.readFileSync('/mnt/user-data/outputs/jobs.html','utf8');
let pass=0,fail=0;
const ok=(l,c)=>{ c?(pass++,console.log('✓ '+l)):(fail++,console.log('✗ FAIL '+l)); };
let patched=null;

const dom=new JSDOM(html,{
  runScripts:'dangerously', pretendToBeVisual:true, url:'https://x.test/jobs',
  beforeParse(w){
    w.fetch=async(url,opt={})=>{
      if(opt.method==='PATCH'){ patched={url,body:JSON.parse(opt.body||'{}')};
        return {ok:true,status:200,json:async()=>({success:true})}; }
      return {ok:true,status:200,json:async()=>([])};
    };
    w.confirm=()=>true; w.alert=()=>{};
  }
});
const w=dom.window, d=w.document;
const sleep=ms=>new Promise(r=>setTimeout(r,ms));

(async()=>{
  await sleep(250);
  // Assignments (not declarations) reach the page's own let-bindings
  w.eval(`
    window.__JOB={id:'J1',job_ref:'QU-1',status:'ready',runsheet_date:'2026-09-01',address:'1 Test St'};
    jobsById=new Map([['J1',window.__JOB]]);
    lastFetchedJobs=[window.__JOB];
    renderJobs=()=>{}; renderRunsheet=()=>{};
  `);
  const JOB=w.__JOB;
  const btn={getBoundingClientRect:()=>({left:100,bottom:200})};

  w.openInstallDatePicker('J1', btn);
  await sleep(30);
  const menu=d.getElementById('date-menu');
  ok('menu opens for a dated job', !!menu && menu.classList.contains('open'));
  ok('offers changing the date', menu && /Change date/.test(menu.innerHTML));
  ok('offers removing it', menu && /Remove date/.test(menu.innerHTML));
  ok('names the outcome plainly', menu && /on hold/i.test(menu.innerHTML));

  await w.removeDateAndHold('J1');
  await sleep(50);
  ok('sends a PATCH', !!patched);
  ok('clearing the date', patched && patched.body.runsheet_date===null);
  ok('to the right job', patched && patched.url.includes('J1'));
  ok('local job goes on hold', JOB.status==='on_hold');
  ok('menu closes after', !menu.classList.contains('open'));

  // An installed job must not be dragged backwards
  w.eval(`
    window.__INST={id:'J2',status:'installed',runsheet_date:'2026-09-02'};
    jobsById=new Map([['J2',window.__INST]]); lastFetchedJobs=[window.__INST];
  `);
  await w.removeDateAndHold('J2');
  await sleep(50);
  ok('installed job keeps its status', w.__INST.status==='installed');
  ok('but still loses the date', w.__INST.runsheet_date===null);

  // No date yet -> straight to the picker, still one tap
  let picked=false;
  w.eval(`
    window.__NEW={id:'J3',status:'on_hold',runsheet_date:null};
    jobsById=new Map([['J3',window.__NEW]]); lastFetchedJobs=[window.__NEW];
    window.__pickedFlag=false;
    openNativeDatePicker=()=>{ window.__pickedFlag=true; };
  `);
  w.openInstallDatePicker('J3', btn);
  await sleep(20);
  ok('undated job opens the picker directly', w.__pickedFlag===true);
  ok('and shows no menu', !d.getElementById('date-menu').classList.contains('open'));

  // A failed save must not lie about the outcome
  patched=null;
  w.eval(`
    window.__ERR={id:'J4',status:'ready',runsheet_date:'2026-09-03'};
    jobsById=new Map([['J4',window.__ERR]]); lastFetchedJobs=[window.__ERR];
    window.__alerted='';
    window.alert=m=>{ window.__alerted=m; };
    fetch=async()=>({ok:false,status:500,json:async()=>({success:false,error:'boom'})});
  `);
  await w.removeDateAndHold('J4');
  await sleep(40);
  ok('failure is reported', /boom/.test(w.__alerted||''));
  ok('and the job keeps its date', w.__ERR.runsheet_date==='2026-09-03');
  ok('and its status', w.__ERR.status==='ready');

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail?1:0);
})();
