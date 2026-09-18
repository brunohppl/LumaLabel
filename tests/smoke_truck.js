// Driver page shows the truck the scheduler assigned — no picker.
const fs=require('fs'), {JSDOM}=require('jsdom');
const html=fs.readFileSync('/mnt/user-data/outputs/driver.html','utf8');
let pass=0,fail=0;
const ok=(l,c)=>{ c?(pass++,console.log('✓ '+l)):(fail++,console.log('✗ FAIL '+l)); };

const dom=new JSDOM(html,{runScripts:'dangerously',pretendToBeVisual:true,url:'https://x.test/driver/J1',
  beforeParse(w){
    w.fetch=async()=>({ok:true,status:200,json:async()=>({job:{id:'J1'},items:[]})});
    w.alert=()=>{};
  }});
const w=dom.window,d=w.document;
const val=()=>d.getElementById('truck-value');

setTimeout(()=>{
  ok('the picker is gone', !d.getElementById('truck-input'));
  ok('a read-only truck field is shown', !!val());

  // the load tile decides
  w.eval("job={id:'J1',truck:''};");
  w.renderTruck([{type:'install',vehicle:'Bruce'},{type:'to_load',vehicle:'Nemo'}]);
  ok('uses the LOAD tile, not the install tile', val().textContent.trim()==='Nemo');
  ok('and keeps job.truck in step', w.eval('job.truck')==='Nemo');

  // no load tile: fall back to the install tile
  w.eval("job={id:'J1',truck:''};");
  w.renderTruck([{type:'install',vehicle:'Bruce'}]);
  ok('falls back to the install tile', val().textContent.trim()==='Bruce');

  // two trucks on a big job
  w.eval("job={id:'J1',truck:''};");
  w.renderTruck([{type:'to_load',vehicle:'Nemo'},{type:'to_load',vehicle:'Nigel'}]);
  ok('names both when the load is split', /Nemo \+ Nigel/.test(val().textContent));

  // nothing assigned
  w.eval("job={id:'J1',truck:''};");
  w.renderTruck([]);
  ok('says so when no truck is assigned', /Not assigned/.test(val().textContent));
  ok('and is styled as unset', val().classList.contains('unset'));

  // an older job with only the stored truck
  w.eval("job={id:'J1',truck:'Bruce'};");
  w.renderTruck([]);
  ok('falls back to the stored truck', val().textContent.trim()==='Bruce');

  ok('setStatus no longer reads the picker', !/getElementById\('truck-input'\)/.test(html));
  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail?1:0);
},350);
