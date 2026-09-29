// Cross-project item search, receiving from the results, and archiving.
const fs=require('fs'), {JSDOM}=require('jsdom');
const html=fs.readFileSync('/mnt/user-data/outputs/deliveries.html','utf8');
let pass=0,fail=0;
const ok=(l,c)=>{ c?(pass++,console.log('✓ '+l)):(fail++,console.log('✗ FAIL '+l)); };

const PROJECTS=[{id:'P1',name:'Somers Residence',units_received:2,units_expected:10,item_count:5,pct:20,complete:false,archived:false},
                {id:'P2',name:'Hillview St',units_received:8,units_expected:8,item_count:3,pct:100,complete:true,archived:false}];
const HITS=[{id:'L1',project_id:'P1',project_name:'Somers Residence',product_name:'Tate Console Table',
             sku:'AB1',brand:'Globe West',section:'Living',qty_expected:2,qty_received:0,
             photo_url:'https://x/t.png'},
            {id:'L2',project_id:'P2',project_name:'Hillview St',product_name:'Tate Side Table',
             sku:'AB2',brand:'Globe West',section:'Bed 1',qty_expected:1,qty_received:1}];
let asked=[], patched=[];

const dom=new JSDOM(html,{runScripts:'dangerously',pretendToBeVisual:true,url:'https://x/deliveries',
  beforeParse(w){
    w.fetch=async(url,opt={})=>{
      const u=String(url); asked.push(u);
      if(u.includes('/search'))   return {ok:true,status:200,json:async()=>({success:true,lines:HITS,query:'tate'})};
      if(u.includes('/archive')){ patched.push(JSON.parse(opt.body)); return {ok:true,status:200,json:async()=>({success:true})}; }
      if(u.includes('/check'))    return {ok:true,status:200,json:async()=>({success:true,
                                    line:{id:'L1',qty_expected:2,qty_received:1}})};
      if(u.includes('/projects')) return {ok:true,status:200,json:async()=>({success:true,projects:PROJECTS})};
      return {ok:true,status:200,json:async()=>({success:true})};
    };
    w.alert=()=>{}; w.confirm=()=>true;
  }});
const w=dom.window,d=w.document;
const sleep=ms=>new Promise(r=>setTimeout(r,ms));

(async()=>{
  await sleep(300);
  ok('a search box sits above the projects', !!d.getElementById('gq'));

  // too short to search
  d.getElementById('gq').value='t';
  await w.runGlobalSearch(); await sleep(40);
  ok('one character does not query', !asked.some(u=>u.includes('/search')));
  ok('and the project list stays visible',
     d.getElementById('project-list').style.display!=='none');

  // a real search
  d.getElementById('gq').value='tate';
  await w.runGlobalSearch(); await sleep(60);
  ok('searching queries across projects', asked.some(u=>u.includes('/search?q=tate')));
  const res=[...d.querySelectorAll('.gres')];
  ok('results are listed', res.length===2);
  ok('each names its project',
     /Somers Residence/.test(res[0].textContent) && /Hillview St/.test(res[1].textContent));
  ok('the sku and brand are shown', /AB1/.test(res[0].textContent) && /Globe West/.test(res[0].textContent));
  ok('a photo is shown when there is one', !!res[0].querySelector('.gres-thumb'));
  ok('progress is shown', /0 \/ 2/.test(res[0].textContent));
  ok('a complete line reads as done', res[1].querySelector('.gres-count').classList.contains('done'));
  ok('the project list is hidden while searching',
     d.getElementById('project-list').style.display==='none');

  // receive straight from the results
  await w.gCheck('L1', 1); await sleep(60);
  ok('receiving uses the normal check endpoint',
     asked.some(u=>u.includes('/lines/L1/check')));
  ok('the count comes back from the server, not assumed',
     /1 \/ 2/.test(d.getElementById('gcount-L1').textContent));

  // clearing the box brings the projects back
  d.getElementById('gq').value='';
  await w.runGlobalSearch(); await sleep(40);
  ok('clearing the search restores the project list',
     d.getElementById('project-list').style.display!=='none');

  // archiving
  ok('each project offers an archive button', d.querySelectorAll('.arch-btn').length===2);
  await w.setArchived('P1', true); await sleep(60);
  ok('archiving sends the flag', patched.length===1 && patched[0].archived===true);
  ok('restoring sends the opposite', (await w.setArchived('P2', false), patched[1].archived===false));
  ok('archived projects are hidden by default',
     !asked.filter(u=>u.includes('/api/deliveries/projects')).slice(-1)[0].includes('archived=1'));
  d.getElementById('show-archived').checked=true;
  await w.loadProjects(); await sleep(60);
  ok('and shown when asked for',
     asked.filter(u=>u.includes('/api/deliveries/projects')).slice(-1)[0].includes('archived=1'));

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail?1:0);
})();
