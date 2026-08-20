// Smoke test for the damages page: loads it for real, stubs the network, and
// drives the repair-status control including its failure path.
const fs=require('fs'); const {JSDOM}=require('jsdom');
const html=fs.readFileSync('/mnt/user-data/outputs/damages.html','utf8').replace(/\{\{[^}]*\}\}/g,'');
const REPORTS=[
 {id:'R1',report_number:1,created_at:'2026-08-18T02:00:00Z',location:'install',damage_type:'scratch',
  furniture:'sofa',report_category:'furniture',photo_url:null,repair_status:'to_schedule'},
 {id:'R2',report_number:2,created_at:'2026-08-18T03:00:00Z',location:'transport',damage_type:'crack',
  property_element:'wall',report_category:'property',damage_origin:'caused',photo_url:null,repair_status:'fixed'},
 {id:'R3',report_number:3,created_at:'2026-08-18T04:00:00Z',location:'warehouse',damage_type:'dent',
  furniture:'table',report_category:'furniture',photo_url:null},   // no status at all
];
let patches=[], failNext=false;
const dom=new JSDOM(html,{runScripts:'dangerously',url:'http://localhost/damages',beforeParse(w){
  w.fetch=async(url,opts={})=>{
    if(opts.method==='PATCH'){
      patches.push({url,body:JSON.parse(opts.body)});
      if(failNext) return {ok:false,status:400,json:async()=>({success:false,error:'nope'})};
      return {ok:true,status:200,json:async()=>({success:true})};
    }
    return {ok:true,status:200,json:async()=>REPORTS};
  };
  w.alert=m=>{(w.__alerts=w.__alerts||[]).push(m);};
  w.confirm=()=>true;
}});
const w=dom.window,d=w.document;
let pass=0,fail=0;
const ok=(l,c)=>{c?pass++:fail++;console.log((c?'✓ ':'✗ FAIL ')+l);};
const sleep=ms=>new Promise(r=>setTimeout(r,ms));

(async()=>{
  await sleep(1200);
  const sels=[...d.querySelectorAll('.status-select')];
  // Rows are grouped (furniture, then property), so find by report id rather
  // than assuming an order.
  const byId=id=>sels.find(x=>x.getAttribute('onchange').includes(`'${id}'`));
  // Change it the way a user does: set the value, then fire change.
  const choose=async(id,val)=>{ const s=byId(id); s.value=val; s.dispatchEvent(new w.Event('change')); await sleep(80); return s; };
  ok('a status control on every report ('+sels.length+')', sels.length===3);
  ok('four options in workflow order',
     sels.length && [...sels[0].options].map(o=>o.value).join()==='to_schedule,scheduled,fixed,discard');
  ok('existing status is preselected', byId('R2').value==='fixed');
  ok('a report with no status defaults to "to be scheduled"', byId('R3').value==='to_schedule');
  ok('statuses are colour-coded differently',
     byId('R1').style.background!==byId('R2').style.background);

  patches=[];
  const s1=await choose('R1','scheduled');
  ok('changing status saves once', patches.length===1);
  ok('sends the new status', patches[0] && patches[0].body.repair_status==='scheduled');
  ok('targets the right report', patches[0] && patches[0].url.includes('R1'));
  ok('control shows the new value', s1.value==='scheduled');
  ok('no alert on success', !(w.__alerts||[]).length);

  // failure must revert, not silently show the wrong status
  failNext=true; patches=[];
  await choose('R1','discard');
  ok('failed save reverts the control', byId('R1').value==='scheduled');
  ok('and tells the user', (w.__alerts||[]).length===1);
  ok('control is usable again after failure', byId('R1').disabled===false);

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail?1:0);
})().catch(e=>{console.log('HARNESS ERROR: '+e.message);process.exit(2);});
