// Readiness board: three day timelines, crew rows, colour = readiness.
const fs=require('fs'), {JSDOM}=require('jsdom');
const html=fs.readFileSync('/mnt/user-data/outputs/readiness.html','utf8');
let pass=0,fail=0;
const ok=(l,c)=>{ c?(pass++,console.log('✓ '+l)):(fail++,console.log('✗ FAIL '+l)); };

const D=['2026-09-08','2026-09-09','2026-09-10'];
const DATA={
  days:D, rules:{picked:2,staged:1,loaded:1},
  teams:[{id:'T1',date:D[0],name:'Nemo',vehicle:'Nemo',function:'transport'},
         {id:'T2',date:D[0],name:'Warehouse',vehicle:null,function:'warehouse'},
         {id:'T3',date:D[1],name:'Bruce',vehicle:'Bruce',function:'transport'},
         {id:'T4',date:D[2],name:'Nigel',vehicle:'Nigel',function:'transport'}],
  jobs:[
    {job_id:'J1',ref:'QU-1351',address:'12 Somers St, Ascot',date:D[0],time:'09:00',
     duration:90,kind:'install',team_id:'T1',worst:'late',
     stages:[{name:'Picked',done:18,total:42,state:'late'},
             {name:'In bay',done:0,total:40,state:'late'},
             {name:'Loaded',done:0,total:40,state:'late'}]},
    {job_id:'J2',ref:'QU-1362',address:'9 Vine St, Clayfield',date:D[0],time:'08:00',
     duration:60,kind:'bay',team_id:'T2',worst:'due',install_date:D[1],
     stages:[{name:'Picked',done:30,total:30,state:'done'},
             {name:'In bay',done:10,total:30,state:'due'}]},
    {job_id:'J3',ref:'QU-1370',address:'5 Kent Rd, Wooloowin',date:D[1],time:'10:00',
     duration:120,kind:'install',team_id:'T3',worst:'done',
     stages:[{name:'Picked',done:20,total:20,state:'done'},
             {name:'In bay',done:20,total:20,state:'done'},
             {name:'Loaded',done:20,total:20,state:'done'}]},
    {job_id:'J4',ref:'QU-1399',address:'7 Forfar St',date:D[2],time:null,
     duration:null,kind:'install',team_id:null,worst:'ok',
     stages:[{name:'Picked',done:0,total:12,state:'ok'}]}]};

const dom=new JSDOM(html,{runScripts:'dangerously',url:'http://x/readiness',
  beforeParse(w){ w.fetch=async()=>({ok:true,status:200,json:async()=>DATA}); }});
const w=dom.window,d=w.document;

setTimeout(()=>{
  const days=[...d.querySelectorAll('.day')];
  ok('three day sections', days.length===3);
  ok('labelled Today / Tomorrow / Day after',
     /Today/.test(days[0].textContent)&&/Tomorrow/.test(days[1].textContent)&&/Day after/.test(days[2].textContent));
  ok('crew rows for the day', /Nemo/.test(days[0].textContent));
  ok('warehouse crew shown too', /Warehouse/.test(days[0].textContent));
  ok('each day has its own crews', /Bruce/.test(days[1].textContent) && !/Bruce/.test(days[0].textContent));
  ok('a time axis is drawn', days[0].querySelectorAll('.tick').length>=8);

  const b1=[...days[0].querySelectorAll('.blk')].find(b=>/QU-1351/.test(b.textContent));
  ok('job block drawn on its crew row', !!b1);
  ok('positioned by start time', /left:\s*18\.75%/.test(b1.getAttribute('style')));
  ok('a behind job is red', b1.classList.contains('late'));
  ok('and carries the alert mark', !!b1.querySelector('.bang'));
  const b2=[...d.querySelectorAll('.blk')].find(b=>/QU-1362/.test(b.textContent));
  ok('a due job is amber', b2.classList.contains('due'));
  ok('due jobs get no alert mark', !b2.querySelector('.bang'));
  const b3=[...d.querySelectorAll('.blk')].find(b=>/QU-1370/.test(b.textContent));
  ok('a ready job is green', b3.classList.contains('done'));

  ok('block states the shortfall', /Picked 18\/42/.test(b1.textContent));
  ok('detail sits in the tooltip', /Loaded: 0\/40/.test(b1.getAttribute('title')));
  ok('a load-bay block says when it installs', /Installs/.test(b2.getAttribute('title')));

  ok('unassigned work is still shown', /Unassigned/.test(days[2].textContent));
  ok('and its block is drawn', !!days[2].querySelector('.blk'));

  ok('the day header counts what needs rescuing', /1 need rescuing/.test(days[0].textContent));
  ok('a clean day says nothing', !/need rescuing/.test(days[1].textContent));
  ok('full screen control present', !!d.getElementById('fs-btn'));

  ok('no pickups', !/Pickup/.test(d.body.textContent));
  ok('no links at all — read only', d.querySelectorAll('a[href]').length===0);

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail?1:0);
},350);
