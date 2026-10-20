// Damages: archive keeps everything and only hides the report.
const fs=require('fs'), {JSDOM}=require('jsdom');
let pass=0,fail=0;
const ok=(l,c)=>{ c?(pass++,console.log('✓ '+l)):(fail++,console.log('✗ FAIL '+l)); };
const sleep=ms=>new Promise(r=>setTimeout(r,ms));

let REPORTS=[
  {id:'D1',location:'12 Somers St',damage_type:'Scuff',furniture:'Sofa',repair_status:'to_schedule',created_at:'2026-10-01T00:00:00Z',archived:false},
  {id:'D2',location:'9 Vine St',damage_type:'Chip',furniture:'Table',repair_status:'fixed',created_at:'2026-09-01T00:00:00Z',archived:true}];
let asked=[], patched=[];

const html=fs.readFileSync('/mnt/user-data/outputs/damages.html','utf8');
const dom=new JSDOM(html,{runScripts:'dangerously',pretendToBeVisual:true,url:'https://x/damages',
  beforeParse(w){
    w.fetch=async(url,opt={})=>{
      const u=String(url); asked.push(u);
      if(opt.method==='PATCH'){ patched.push({u,body:JSON.parse(opt.body)}); 
        return {ok:true,status:200,json:async()=>({success:true})}; }
      if(u.includes('/api/damages')){
        const withArchived=u.includes('archived=1');
        return {ok:true,status:200,json:async()=>
          (withArchived?REPORTS:REPORTS.filter(r=>!r.archived))};
      }
      return {ok:true,status:200,json:async()=>([])};
    };
    w.alert=()=>{}; w.confirm=()=>true;
  }});
const w=dom.window,d=w.document;

(async()=>{
  await sleep(400);
  ok('a Show archived toggle exists', !!d.getElementById('show-archived'));
  ok('archived reports are hidden by default', !/9 Vine St/.test(d.body.textContent));
  ok('and live ones are shown', /12 Somers St/.test(d.body.textContent));
  ok('the default request does not ask for archived',
     asked.some(u=>u.includes('/api/damages') && !u.includes('archived=1')));

  // archiving
  patched=[];
  await w.setArchived('D1', true); await sleep(80);
  ok('archiving sends the flag', patched.length===1 && patched[0].body.archived===true);
  ok('and nothing else', Object.keys(patched[0].body).length===1);
  ok('it is a PATCH, never a DELETE', !patched[0].u.includes('DELETE'));

  // showing them
  d.getElementById('show-archived').checked=true;
  await w.loadReports(); await sleep(80);
  ok('asking for archived includes them',
     asked.slice(-1)[0].includes('archived=1'));
  ok('and they appear', /9 Vine St/.test(d.body.textContent));

  // restoring
  patched=[];
  await w.setArchived('D2', false); await sleep(80);
  ok('restoring sends the opposite', patched[0].body.archived===false);

  // delete is still there for genuine mistakes
  ok('delete remains available', /deleteReport/.test(html));

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail?1:0);
})();
