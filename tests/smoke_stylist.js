// Assigning a stylist from the jobs page.
const fs=require('fs'), {JSDOM}=require('jsdom');
const html=fs.readFileSync('/mnt/user-data/outputs/jobs.html','utf8');
let pass=0,fail=0;
const ok=(l,c)=>{ c?(pass++,console.log('✓ '+l)):(fail++,console.log('✗ FAIL '+l)); };
let patched=null, alerted='';

const dom=new JSDOM(html,{runScripts:'dangerously',pretendToBeVisual:true,url:'https://x.test/jobs',
  beforeParse(w){
    w.fetch=async(url,opt={})=>{
      if(String(url).includes('/api/stylists'))
        return {ok:true,status:200,json:async()=>({stylists:['Addy','Montie','India','Lyndall']})};
      if(opt.method==='PATCH'){ patched={url,body:JSON.parse(opt.body||'{}')};
        return {ok:true,status:200,json:async()=>({success:true})}; }
      return {ok:true,status:200,json:async()=>([])};
    };
    w.confirm=()=>true; w.alert=m=>{alerted=String(m);};
  }});
const w=dom.window,d=w.document;
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const opts=()=>[...d.querySelectorAll('.stylist-opt')];

(async()=>{
  await sleep(250);
  w.eval(`
    window.__J={id:'J1',job_ref:'QU-1',status:'ready',job_owner:''};
    jobsById=new Map([['J1',window.__J]]); lastFetchedJobs=[window.__J];
    renderJobs=()=>{};
  `);
  const J=w.__J;

  await w.openStylistPicker('J1'); await sleep(60);
  ok('picker opens', d.getElementById('stylist-popover').classList.contains('open'));
  ok('lists the roster ('+opts().length+')', opts().length===4);
  ok('names the job', /QU-1/.test(d.getElementById('stylist-popover-title').textContent));
  ok('no remove option when unassigned', !opts().some(o=>/Remove/.test(o.textContent)));

  await w.setStylist('India'); await sleep(50);
  ok('assigning sends a PATCH', !!patched);
  ok('with the stylist name', patched && patched.body.job_owner==='India');
  ok('to the right job', patched && patched.url.includes('J1'));
  ok('card state updated', J.job_owner==='India');
  ok('picker closed', !d.getElementById('stylist-popover').classList.contains('open'));

  // reopening shows the current one and a way to clear it
  await w.openStylistPicker('J1'); await sleep(60);
  ok('current stylist highlighted', opts().some(o=>o.classList.contains('current') && o.textContent==='India'));
  ok('offers removal', opts().some(o=>/Remove India/.test(o.textContent)));

  patched=null;
  await w.setStylist(''); await sleep(50);
  ok('clearing sends an empty value', patched && patched.body.job_owner==='');
  ok('and clears the card', J.job_owner==='');

  // roster fetched once, not on every open
  let calls=0;
  w.eval(`
    window.__calls=0;
    fetch=async(u,o={})=>{
      if(String(u).includes('/api/stylists')){ window.__calls++; return {ok:true,status:200,json:async()=>({stylists:['Addy']})}; }
      return {ok:true,status:200,json:async()=>({success:true})};
    };
  `);
  await w.openStylistPicker('J1'); await sleep(50);
  w.closeStylistPicker();
  await w.openStylistPicker('J1'); await sleep(50);
  ok('roster is not refetched every time', w.__calls===0);
  w.closeStylistPicker();

  // failures must not show an assignment that did not save
  w.eval(`
    window.__J2={id:'J2',job_ref:'QU-2',job_owner:'Addy'};
    jobsById=new Map([['J2',window.__J2]]); lastFetchedJobs=[window.__J2];
    fetch=async()=>({ok:false,status:500,json:async()=>({success:false,error:'boom'})});
  `);
  await w.openStylistPicker('J2'); await sleep(50);
  await w.setStylist('Montie'); await sleep(50);
  ok('failure is reported', /boom/.test(alerted));
  ok('and the old stylist is kept', w.__J2.job_owner==='Addy');

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail?1:0);
})();
