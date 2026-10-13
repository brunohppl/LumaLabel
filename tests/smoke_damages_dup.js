// Damages: a report must be filed once, however many times you tap.
const fs=require('fs'), {JSDOM}=require('jsdom');
const html=fs.readFileSync('/mnt/user-data/outputs/damages.html','utf8');
let pass=0,fail=0;
const ok=(l,c)=>{ c?(pass++,console.log('✓ '+l)):(fail++,console.log('✗ FAIL '+l)); };
let posts=0;

const dom=new JSDOM(html,{runScripts:'dangerously',pretendToBeVisual:true,url:'https://x/damages',
  beforeParse(w){
    w.fetch=async(url,opt={})=>{
      const m=(opt.method||'GET');
      if(m==='POST' && String(url).includes('/api/damages')){
        posts++;
        await new Promise(r=>setTimeout(r,120));   // a real save takes time
        return {ok:true,status:200,json:async()=>({success:true,report:{id:'D'+posts}})};
      }
      return {ok:true,status:200,json:async()=>({success:true,reports:[],jobs:[]})};
    };
    w.alert=()=>{}; w.confirm=()=>true;
  }});
const w=dom.window,d=w.document;
const sleep=ms=>new Promise(r=>setTimeout(r,ms));

(async()=>{
  await sleep(300);
  w.eval("_wizardData={job_id:'J1',item:'Sofa',description:'scuff'}; _photos=[]; _editingId=null;");

  // hammer both buttons, as someone would on a slow connection
  posts=0;
  w.submitReport(true); w.submitReport(true); w.submitReport(true);
  await sleep(400);
  ok(`three taps file one report (filed ${posts})`, posts===1);

  // and the other button doesn't slip through mid-save
  posts=0;
  w.submitReport(true); w.submitReport(false);
  await sleep(400);
  ok(`both buttons together still file one (filed ${posts})`, posts===1);

  // both are disabled while saving
  posts=0;
  const p=w.submitReport(true);
  await sleep(40);
  ok('the submit button is disabled while saving', d.querySelector('.btn-submit').disabled===true);
  ok('and so is skip-photos', d.querySelector('.btn-skip').disabled===true);
  await p; await sleep(60);
  ok('both are usable again afterwards',
     d.querySelector('.btn-submit').disabled===false && d.querySelector('.btn-skip').disabled===false);

  // a later, separate report still works
  posts=0;
  await w.submitReport(true); await sleep(200);
  ok('a subsequent report files normally', posts===1);

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail?1:0);
})();
