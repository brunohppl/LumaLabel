const fs=require('fs'), {JSDOM}=require('jsdom');
const html=fs.readFileSync('/mnt/user-data/outputs/driver.html','utf8');
let pass=0,fail=0;
const ok=(l,c)=>{ c?(pass++,console.log('✓ '+l)):(fail++,console.log('✗ FAIL '+l)); };

function run(payload, src){
  return new Promise(res=>{
    const dom=new JSDOM(html,{runScripts:'dangerously',pretendToBeVisual:true,url:'https://x/driver/J1',
      beforeParse(w){
        w.fetch=async(url)=>({ok:true,status:200,json:async()=>
          String(url).includes('/SRC')?{job:src,items:[]}:payload});
        w.alert=()=>{};
      }});
    setTimeout(async()=>{ try{ await dom.window.loadJob(); }catch(e){} res(dom.window.document); },300);
  });
}

(async()=>{
  // transfer IN
  let d=await run({job:{id:'J1',job_ref:'QU-1',address:'1 A St',is_transfer:true,transfer_from_job_id:'SRC'},
                   items:[],transferring_to:[],schedule:[]},
                  {id:'SRC',job_ref:'QU-0',address:'9 B St'});
  let a=d.querySelector('#transfer-banner a.transfer-link');
  ok('the source job is a link', !!a);
  ok('pointing at its driver page', a.getAttribute('href')==='/driver/SRC');
  ok('labelled with its reference', a.textContent.trim()==='QU-0');
  ok('the address still shown', /9 B St/.test(d.getElementById('transfer-banner').textContent));

  // transfer OUT, two destinations
  d=await run({job:{id:'J1',job_ref:'QU-1',address:'1 A St'},items:[],
               transferring_to:[{id:'J2',job_ref:'QU-2',address:'2 B St'},
                                {id:'J3',job_ref:'QU-3',address:'3 C St'}],schedule:[]},{});
  const outs=[...d.querySelectorAll('#transfer-out-banner a.transfer-link')];
  ok('every destination is a link', outs.length===2);
  ok('each points at its own job',
     outs[0].getAttribute('href')==='/driver/J2' && outs[1].getAttribute('href')==='/driver/J3');

  // a job reference containing markup must not become markup
  d=await run({job:{id:'J1',job_ref:'QU-1',address:'1 A St'},items:[],
               transferring_to:[{id:'J4',job_ref:'<img src=x onerror=1>',address:'x'}],schedule:[]},{});
  ok('a reference is escaped, not rendered',
     !d.querySelector('#transfer-out-banner img'));

  // no source job available — no broken link
  d=await run({job:{id:'J1',job_ref:'QU-1',address:'1 A St',is_transfer:true},items:[],
               transferring_to:[],schedule:[]},{});
  ok('no link when there is no source job',
     !d.querySelector('#transfer-banner a.transfer-link'));
  ok('but the banner still explains the transfer',
     /already in the truck/.test(d.getElementById('transfer-banner').textContent));

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail?1:0);
})();
