// Readiness board: crews as rows, three days as columns, one screen, read-only.
const fs=require('fs'), {JSDOM}=require('jsdom');
const html=fs.readFileSync('/mnt/user-data/outputs/readiness.html','utf8');
let pass=0,fail=0;
const ok=(l,c)=>{ c?(pass++,console.log('✓ '+l)):(fail++,console.log('✗ FAIL '+l)); };

const D=['2026-09-08','2026-09-09','2026-09-10'];
const DATA={
  days:D, rules:{picked:2,staged:1,loaded:1},
  teams:[{id:'A1',date:D[0],name:'Nemo',vehicle:'Nemo',function:'transport',sort_order:1},
         {id:'A2',date:D[0],name:'Warehouse',vehicle:null,function:'warehouse',sort_order:9},
         // same crews again on later days — must stay ONE row each
         {id:'B1',date:D[1],name:'Nemo',vehicle:'Nemo',function:'transport',sort_order:1},
         {id:'B2',date:D[1],name:'Warehouse',vehicle:null,function:'warehouse',sort_order:9},
         {id:'C1',date:D[2],name:'Bruce',vehicle:'Bruce',function:'transport',sort_order:0},
         {id:'S1',date:D[0],name:'Marlin',vehicle:'Marlin',function:'styling'},
         {id:'S2',date:D[1],name:'VUG',vehicle:'VUG',function:'styling'}],
  jobs:[
    {job_id:'J1',ref:'QU-1351',address:'12 Somers St, Ascot',date:D[0],kind:'install',
     team_id:'A1',worst:'late',
     stages:[{name:'Picked',done:18,total:42,state:'late'},
             {name:'In bay',done:0,total:40,state:'late'},
             {name:'Loaded',done:0,total:40,state:'late'}]},
    {job_id:'J2',ref:'QU-1362',address:'9 Vine St, Clayfield',date:D[0],kind:'bay',
     team_id:'A2',worst:'due',install_date:D[1],
     stages:[{name:'Picked',done:30,total:30,state:'done'},
             {name:'In bay',done:10,total:30,state:'due'}]},
    {job_id:'J3',ref:'QU-1370',address:'5 Kent Rd, Wooloowin',date:D[1],kind:'install',
     team_id:'B1',worst:'done',
     stages:[{name:'Picked',done:20,total:20,state:'done'},
             {name:'In bay',done:20,total:20,state:'done'},
             {name:'Loaded',done:20,total:20,state:'done'}]},
    {job_id:'J4',ref:'QU-1399',address:'7 Forfar St',date:D[2],kind:'install',
     team_id:null,worst:'ok',
     stages:[{name:'Picked',done:0,total:12,state:'ok'}]},
    {job_id:'J5',ref:'QU-1380',address:'2 Styling Ave',date:D[0],kind:'install',
     team_id:'S1',worst:'late',
     stages:[{name:'Picked',done:1,total:9,state:'late'}]},
    {job_id:'J6',ref:'QU-1390',address:'4 Legacy St',date:D[0],kind:'install',
     team_id:null,vehicle:'Nemo',worst:'due',
     stages:[{name:'Picked',done:5,total:10,state:'due'}]}]};

const dom=new JSDOM(html,{runScripts:'dangerously',url:'http://x/readiness',
  beforeParse(w){ w.fetch=async()=>({ok:true,status:200,json:async()=>DATA}); }});
const w=dom.window,d=w.document;

setTimeout(()=>{
  // ── Layout: crews down, days across ──
  const heads=[...d.querySelectorAll('.hcell')];
  ok('a corner plus three day headers', heads.length===4);
  ok('days across the top in order',
     /Today/.test(heads[1].textContent)&&/Tomorrow/.test(heads[2].textContent)&&/Day after/.test(heads[3].textContent));
  const rows=[...d.querySelectorAll('.rowhead')].map(x=>x.querySelector('.crew').textContent);
  ok('crews down the left', rows.includes('Nemo')&&rows.includes('Warehouse'));
  ok('one row per crew across all days', rows.filter(r=>r==='Nemo').length===1);
  ok('unassigned work gets its own row', rows.includes('Unassigned'));
  ok('three cells per crew row', d.querySelectorAll('.cell').length===rows.length*3);

  // ── Chips land in the right crew and day ──
  const cells=[...d.querySelectorAll('.cell')];
  const nemoIdx=rows.indexOf('Nemo');
  const nemoToday=cells[nemoIdx*3];
  ok('a job sits in its crew and day', /QU-1351/.test(nemoToday.textContent));
  const whIdx=rows.indexOf('Warehouse');
  ok('a bay job sits with the warehouse', /QU-1362/.test(cells[whIdx*3].textContent));
  ok('tomorrow column holds tomorrow work', /QU-1370/.test(cells[nemoIdx*3+1].textContent));
  const unIdx=rows.indexOf('Unassigned');
  ok('unassigned work is visible', /QU-1399/.test(cells[unIdx*3+2].textContent));

  // ── Colour and wording ──
  const c1=[...d.querySelectorAll('.chip')].find(c=>/QU-1351/.test(c.textContent));
  ok('behind is red', c1.classList.contains('late'));
  ok('and marked with an exclamation', !!c1.querySelector('.bang'));
  ok('states the shortfall', /Picked 18\/42/.test(c1.textContent));
  ok('full detail in the tooltip', /Loaded: 0\/40/.test(c1.getAttribute('title')));
  const c2=[...d.querySelectorAll('.chip')].find(c=>/QU-1362/.test(c.textContent));
  ok('due is amber', c2.classList.contains('due'));
  ok('due carries no exclamation', !c2.querySelector('.bang'));
  const c3=[...d.querySelectorAll('.chip')].find(c=>/QU-1370/.test(c.textContent));
  ok('ready is green', c3.classList.contains('done'));

  // ── Nothing to touch ──
  ok('no times shown', !/\b\d{1,2}:\d{2}\b/.test(d.querySelector('.grid').textContent));
  ok('no links', d.querySelectorAll('a[href]').length===0);
  ok('no scrollable timeline', d.querySelectorAll('.lane, .ruler').length===0);
  ok('day header counts what needs rescuing', /1 need rescuing/.test(heads[1].textContent));
  ok('a clean day says nothing', !/need rescuing/.test(heads[3].textContent));
  ok('full screen available', !!d.getElementById('fs-btn'));

  // Styling crews have nothing to pick, stage or load
  ok('no styling crew rows', !rows.includes('Marlin') && !rows.includes('VUG'));
  ok('their work is not shown', !/QU-1380/.test(d.querySelector('.grid').textContent));
  ok('and is not dumped into Unassigned',
     !/QU-1380/.test(cells[rows.indexOf('Unassigned')*3].textContent));
  ok('nor counted as needing rescuing', /1 need rescuing/.test(heads[1].textContent));

  // Crew resolution and order follow the runsheet
  ok('a vehicle-only tile lands on its crew, not Unassigned',
     /QU-1390/.test(cells[rows.indexOf('Nemo')*3].textContent));
  ok('and is kept out of Unassigned',
     !/QU-1390/.test(cells[rows.indexOf('Unassigned')*3].textContent));
  ok('rows follow the runsheet column order (Bruce, Nemo, Warehouse)',
     rows.indexOf('Bruce')<rows.indexOf('Nemo') && rows.indexOf('Nemo')<rows.indexOf('Warehouse'));
  ok('Unassigned stays last', rows[rows.length-1]==='Unassigned');

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail?1:0);
},350);
