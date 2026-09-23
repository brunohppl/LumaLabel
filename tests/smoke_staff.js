// Adding a crew member from the runsheet's crew popover.
const fs=require('fs'), {JSDOM}=require('jsdom');
const html=fs.readFileSync('/mnt/user-data/outputs/runsheet.html','utf8');
let pass=0,fail=0;
const ok=(l,c)=>{ c?(pass++,console.log('✓ '+l)):(fail++,console.log('✗ FAIL '+l)); };
let posted=null, mode='ok';

const dom=new JSDOM(html,{runScripts:'dangerously',url:'http://localhost/scheduler',beforeParse(w){
  w.fetch=async(url,opt={})=>{
    if(String(url).includes('/api/staff') && opt.method==='POST'){
      posted=JSON.parse(opt.body||'{}');
      if(mode==='fail') return {ok:false,status:400,json:async()=>({success:false,error:'nope'})};
      if(mode==='dupe') return {ok:true,status:200,json:async()=>({success:true,name:posted.name,already:true})};
      return {ok:true,status:200,json:async()=>({success:true,name:posted.name})};
    }
    if(String(url).includes('/api/staff')) return {ok:true,status:200,json:async()=>({staff:['Addy','Jo','Zara']})};
    if(String(url).includes('/api/runsheet/'))
      return {ok:true,status:200,json:async()=>({teams:[],schedule:[],tasks:[],jobs:[],loads:[],jobs_nearby:[]})};
    return {ok:true,status:200,json:async()=>([])};
  };
  w.alert=()=>{};
}});
const w=dom.window,d=w.document;
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const msg=()=>d.getElementById('add-person-msg').textContent;

(async()=>{
  await sleep(400);
  ok('the add field exists', !!d.getElementById('new-person'));
  ok('the roster is fetched', w.eval('ALL_WORKERS').includes('Zara'));

  // seed the popover's chips
  w.eval("document.getElementById('member-chips').innerHTML='<button class=\"chip\">Jo</button>';");

  d.getElementById('new-person').value='Priya';
  await w.addPerson(); await sleep(60);
  ok('the name is sent', posted && posted.name==='Priya');
  ok('confirmed on screen', /Priya added/.test(msg()));
  ok('added to the roster', w.eval('ALL_WORKERS').includes('Priya'));
  const chips=[...d.querySelectorAll('#member-chips .chip')];
  ok('a chip appears', chips.some(c=>c.textContent==='Priya'));
  ok('and is already selected',
     chips.find(c=>c.textContent==='Priya').classList.contains('selected'));
  ok('offered as lead too',
     [...d.getElementById('team-lead').options].some(o=>o.textContent==='Priya'));
  ok('the field is cleared', d.getElementById('new-person').value==='');

  // someone already on the roster
  mode='dupe';
  d.getElementById('new-person').value='Jo';
  await w.addPerson(); await sleep(60);
  ok('an existing name is not duplicated',
     w.eval("ALL_WORKERS.filter(n=>n==='Jo').length")===1);
  ok('and says they were already there', /already on the roster/.test(msg()));

  // a blank does nothing
  posted=null;
  d.getElementById('new-person').value='   ';
  await w.addPerson(); await sleep(40);
  ok('a blank name sends nothing', posted===null);

  // failure is reported, not silent
  mode='fail';
  d.getElementById('new-person').value='Ghost';
  await w.addPerson(); await sleep(60);
  ok('a failed save is reported', /Could not add/.test(msg()));
  ok('and the name is not added', !w.eval('ALL_WORKERS').includes('Ghost'));

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail?1:0);
})();
