// The packed rows (cushion bags / accessory tubs) on the driver page.
const fs=require('fs'), {JSDOM}=require('jsdom');
const html=fs.readFileSync('/mnt/user-data/outputs/driver.html','utf8');
let pass=0,fail=0;
const ok=(l,c)=>{ c?(pass++,console.log('✓ '+l)):(fail++,console.log('✗ FAIL '+l)); };
let patched=null, failNext=false;

const JOB={id:'J1',status:'ready_to_load',cushion_bags:2,accessory_tubs:3,
           cushion_bags_loaded:false,accessory_tubs_loaded:false};
const ITEMS=[
  {id:'1',description:'3 Seater Sofa',room:'Living',on_truck:true},
  {id:'2',description:'Dining Table',room:'Dining',on_truck:true},
  {id:'3',description:'Table Lamp',room:'Living'},
  {id:'4',description:'Cushions / Throw',room:'Living'},
  {id:'5',description:'Accessories',room:'Kitchen'}];

const dom=new JSDOM(html,{runScripts:'dangerously',pretendToBeVisual:true,url:'https://x.test/driver/J1',
  beforeParse(w){
    w.fetch=async(url,opt={})=>{
      if(opt&&opt.method==='PATCH'){
        patched={url,body:JSON.parse(opt.body||'{}')};
        if(failNext) return {ok:false,status:500,json:async()=>({success:false})};
        return {ok:true,status:200,json:async()=>({success:true})};
      }
      return {ok:true,status:200,json:async()=>({job:JOB,items:ITEMS})};
    };
    w.alert=()=>{};
  }});
const w=dom.window,d=w.document;
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const txt=()=>d.getElementById('prog-txt').textContent;

(async()=>{
  await sleep(300);
  w.eval(`job=${JSON.stringify(JOB)}; items=${JSON.stringify(ITEMS)}; renderRooms(); updateProgress();`);

  // 2 real items + 1 bag row + 1 tub row
  ok('packed rows counted as two items ('+txt()+')', /\/ 4$/.test(txt().trim()));
  ok('bag row rendered', !!d.getElementById('cushion-bags-row'));
  ok('tub row rendered', !!d.getElementById('accessory-tubs-row'));
  ok('bag row shows the count', /2× Cushion Bag/.test(d.getElementById('cushion-bags-row').textContent));
  ok('tub row shows the count', /3 Accessory Box/.test(d.getElementById('accessory-tubs-row').textContent));

  // ticking persists
  patched=null;
  await w.togglePacked('cushion'); await sleep(50);
  ok('ticking bags saves', patched && patched.body.cushion_bags_loaded===true);
  ok('and counts toward loaded ('+txt()+')', /^3 \/ 4/.test(txt().trim()));
  ok('row shows as checked', d.getElementById('cushion-bags-row').classList.contains('checked'));

  await w.togglePacked('tubs'); await sleep(50);
  ok('ticking tubs saves', patched && patched.body.accessory_tubs_loaded===true);
  ok('everything ticked reaches the total ('+txt()+')', /^4 \/ 4/.test(txt().trim()));

  // untick
  await w.togglePacked('cushion'); await sleep(50);
  ok('unticking saves false', patched && patched.body.cushion_bags_loaded===false);

  // a failed save must not leave a tick that was never stored
  failNext=true;
  const before=w.eval('job.accessory_tubs_loaded');
  await w.togglePacked('tubs'); await sleep(60);
  ok('a failed save reverts the tick', w.eval('job.accessory_tubs_loaded')===before);
  ok('and the row reverts too',
     d.getElementById('accessory-tubs-row').classList.contains('checked')===before);
  failNext=false;

  // no counts set: rows must not be counted
  w.eval(`job={...job,cushion_bags:null,accessory_tubs:0}; renderRooms(); updateProgress();`);
  ok('unset counts are not counted ('+txt()+')', /\/ 2$/.test(txt().trim()));
  ok('the tub row still shows a prompt to ask the stylist',
     /check with the Stylist/i.test(d.getElementById('accessory-tubs-row').textContent));

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail?1:0);
})();
