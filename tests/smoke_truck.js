// Truck picker on the driver page: saves on choose, no Save button.
const fs=require('fs'), {JSDOM}=require('jsdom');
const html=fs.readFileSync('/mnt/user-data/outputs/driver.html','utf8');
let pass=0,fail=0;
const ok=(l,c)=>{ c?(pass++,console.log('✓ '+l)):(fail++,console.log('✗ FAIL '+l)); };
let patched=null, failNext=false;

const JOB={id:'J1',status:'ready_to_load',truck:''};
const dom=new JSDOM(html,{runScripts:'dangerously',pretendToBeVisual:true,url:'https://x.test/driver/J1',
  beforeParse(w){
    w.fetch=async(url,opt={})=>{
      if(opt&&opt.method==='PATCH'){
        patched={url,body:JSON.parse(opt.body||'{}')};
        if(failNext) return {ok:false,status:500,json:async()=>({})};
        return {ok:true,status:200,json:async()=>({success:true})};
      }
      return {ok:true,status:200,json:async()=>({job:JOB,items:[]})};
    };
    w.alert=()=>{};
  }});
const w=dom.window,d=w.document;
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const sel=()=>d.getElementById('truck-input');
const note=()=>d.getElementById('truck-saved');

(async()=>{
  await sleep(300);
  w.eval("job={id:'J1',status:'ready_to_load',truck:''}; items=[];");

  ok('no Save button remains', !d.querySelector('.truck-save'));
  ok('picker saves on change', (sel().getAttribute('onchange')||'').includes('saveTruck'));

  // choosing a truck saves straight away
  sel().value='Bruce';
  await w.saveTruck(); await sleep(50);
  ok('choosing a truck sends a PATCH', !!patched);
  ok('with the chosen truck', patched && patched.body.truck==='Bruce');
  ok('and keeps the current status', patched && patched.body.status==='ready_to_load');
  ok('confirms on screen', /Saved/.test(note().textContent));
  ok('local job updated', w.eval('job.truck')==='Bruce');

  // re-selecting the same truck writes nothing
  patched=null;
  sel().value='Bruce';
  await w.saveTruck(); await sleep(40);
  ok('re-picking the same truck writes nothing', patched===null);

  // changing to another truck saves again
  sel().value='Nemo';
  await w.saveTruck(); await sleep(50);
  ok('changing truck saves again', patched && patched.body.truck==='Nemo');

  // a failed save must not leave the wrong truck showing
  failNext=true;
  sel().value='Nigel';
  await w.saveTruck(); await sleep(60);
  ok('failure is shown', /Not saved/.test(note().textContent));
  ok('picker reverts to the stored truck', sel().value==='Nemo');
  ok('and the job keeps the stored truck', w.eval('job.truck')==='Nemo');
  ok('picker is usable again', sel().disabled===false);
  failNext=false;

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail?1:0);
})();
