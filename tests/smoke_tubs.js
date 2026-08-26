// Accessory tubs / cushion bags input on the stylist page.
const fs=require('fs'), {JSDOM}=require('jsdom');
const html=fs.readFileSync('/mnt/user-data/outputs/stylist.html','utf8');
let pass=0,fail=0;
const ok=(l,c)=>{ c?(pass++,console.log('✓ '+l)):(fail++,console.log('✗ FAIL '+l)); };
let patched=null;

const dom=new JSDOM(html,{runScripts:'dangerously',pretendToBeVisual:true,url:'https://x.test/stylist',
  beforeParse(w){
    w.fetch=async(url,opt={})=>{
      if(opt.method==='PATCH'){ patched={url,body:JSON.parse(opt.body||'{}')};
        return {ok:true,status:200,json:async()=>({success:true})}; }
      return {ok:true,status:200,json:async()=>({job:{},items:[]})};
    };
    w.alert=()=>{};
  }});
const w=dom.window,d=w.document;
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const $=id=>d.getElementById(id);

(async()=>{
  await sleep(250);
  w.eval("currentJob={id:'J1',accessory_tubs:null,cushion_bags:null};");

  ok('both tub inputs exist', !!$('accessory-tubs-input') && !!$('accessory-tubs-input-mobile'));

  // THE BUG: typing on mobile saved the empty desktop field
  $('accessory-tubs-input').value='';
  $('accessory-tubs-input-mobile').value='4';
  patched=null;
  await w.saveAccessoryTubs('mobile'); await sleep(40);
  ok('mobile entry is sent', patched && patched.body.accessory_tubs===4);
  ok('not the empty desktop field', patched && patched.body.accessory_tubs!==null);
  ok('desktop input kept in sync', $('accessory-tubs-input').value==='4');

  // desktop still works
  $('accessory-tubs-input').value='7';
  patched=null;
  await w.saveAccessoryTubs('desktop'); await sleep(40);
  ok('desktop entry is sent', patched && patched.body.accessory_tubs===7);
  ok('mobile input kept in sync', $('accessory-tubs-input-mobile').value==='7');

  // cushion bags, same shape
  $('cushion-bags-input').value='';
  $('cushion-bags-input-mobile').value='2';
  patched=null;
  await w.saveCushionBags('mobile'); await sleep(40);
  ok('cushion bags from mobile', patched && patched.body.cushion_bags===2);

  // clearing the field means "none"
  $('accessory-tubs-input-mobile').value='';
  patched=null;
  await w.saveAccessoryTubs('mobile'); await sleep(40);
  ok('an empty field clears the count', patched && patched.body.accessory_tubs===null);

  // junk must not be stored
  $('accessory-tubs-input-mobile').value='abc';
  patched=null;
  await w.saveAccessoryTubs('mobile'); await sleep(40);
  ok('non-numeric input is treated as none', patched && patched.body.accessory_tubs===null);

  // zero means none, not zero-the-number
  $('accessory-tubs-input-mobile').value='0';
  patched=null;
  await w.saveAccessoryTubs('mobile'); await sleep(40);
  ok('zero clears rather than storing 0', patched && patched.body.accessory_tubs===null);

  // no hint from the caller still works
  $('accessory-tubs-input').value='9';
  patched=null;
  await w.saveAccessoryTubs(); await sleep(40);
  ok('works without a source hint', patched && patched.body.accessory_tubs===9);

  // local state updated so a re-render does not revert it
  ok('job state updated', w.eval('currentJob.accessory_tubs')===9);

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail?1:0);
})();
