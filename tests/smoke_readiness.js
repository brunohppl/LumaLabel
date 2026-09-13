// Readiness board: three days, worst first, stages in words.
const fs=require('fs'), {JSDOM}=require('jsdom');
const html=fs.readFileSync('/mnt/user-data/outputs/readiness.html','utf8');
let pass=0,fail=0;
const ok=(l,c)=>{ c?(pass++,console.log('✓ '+l)):(fail++,console.log('✗ FAIL '+l)); };

const DATA={
  days:['2026-09-08','2026-09-09','2026-09-10'],
  rules:{picked:2,staged:1,loaded:1},
  jobs:[
    {job_id:'J1',ref:'QU-1351',address:'12 Somers St, Ascot QLD 4007',date:'2026-09-08',
     time:'09:00',kind:'install',days_out:0,worst:'late',photo_time:'11:00 AM',items:42,
     stages:[{name:'Picked',done:18,total:42,state:'late'},
             {name:'Staged',done:0,total:1,state:'late',note:'no bay tile'},
             {name:'Loaded',done:0,total:40,state:'late'}]},
    {job_id:'J2',ref:'QU-1362',address:'9 Vine St, Clayfield',date:'2026-09-09',
     time:'08:00',kind:'install',days_out:1,worst:'due',
     stages:[{name:'Picked',done:30,total:30,state:'done'},
             {name:'Staged',done:1,total:1,state:'done'},
             {name:'Loaded',done:12,total:30,state:'due'}]},
    {job_id:'J3',ref:'QU-1370',address:'5 Kent Rd, Wooloowin',date:'2026-09-10',
     time:'10:00',kind:'install',days_out:2,worst:'done',
     stages:[{name:'Picked',done:20,total:20,state:'done'},
             {name:'Staged',done:1,total:1,state:'done'},
             {name:'Loaded',done:20,total:20,state:'done'}]},
    {job_id:'J4',ref:'QU-1330',address:'1 Hale St, Paddington',date:'2026-09-08',
     time:'13:00',kind:'pickup',days_out:0,worst:'ok',stages:[]},
    {job_id:'J2',ref:'QU-1362',address:'9 Vine St, Clayfield',date:'2026-09-08',
     time:'08:00',kind:'bay',days_out:0,worst:'due',install_date:'2026-09-09',
     stages:[{name:'Picked',done:30,total:30,state:'done'},
             {name:'Staged',done:0,total:1,state:'due'}]},
    {job_id:'J3',ref:'QU-1370',address:'5 Kent Rd, Wooloowin',date:'2026-09-09',
     time:'15:00',kind:'to_load',days_out:1,worst:'ok',install_date:'2026-09-10',
     stages:[{name:'Picked',done:20,total:20,state:'done'},
             {name:'Loaded',done:0,total:20,state:'ok'}]}]};

const dom=new JSDOM(html,{runScripts:'dangerously',url:'http://x/readiness',
  beforeParse(w){ w.fetch=async()=>({ok:true,status:200,json:async()=>DATA}); }});
const w=dom.window,d=w.document;

setTimeout(()=>{
  ok('three day columns', d.querySelectorAll('.col').length===3);
  ok('columns labelled Today / Tomorrow / Day after',
     /Today/.test(d.body.textContent) && /Tomorrow/.test(d.body.textContent) && /Day after/.test(d.body.textContent));

  // the answer before reading any row
  const sum=d.getElementById('summary').textContent.replace(/\s+/g,' ');
  ok('summary counts what is behind', /1\s*behind/.test(sum));
  ok('summary counts what is due', /1\s*due today/.test(sum));
  ok('a job is only ready when every one of its rows is', /0\s*ready/.test(sum));

  // rows land in the right day
  const cols=[...d.querySelectorAll('.col')];
  ok('today holds its jobs', /QU-1351/.test(cols[0].textContent) && /QU-1330/.test(cols[0].textContent));
  ok('tomorrow holds its job', /QU-1362/.test(cols[1].textContent));
  ok('day after holds its job', /QU-1370/.test(cols[2].textContent));

  // stages say what to do, not just a colour
  const late=[...d.querySelectorAll('.row')].find(r=>/QU-1351/.test(r.textContent));
  ok('a behind job is flagged', late.classList.contains('late'));
  ok('picking shortfall is stated in words', /18 of 42/.test(late.textContent));
  ok('unstaged is stated plainly', /Not staged/.test(late.textContent));
  ok('the reason is given', /no bay tile/.test(late.textContent));
  ok('a finished stage reads as done', /All 30/.test(d.body.textContent));

  // pickups are the reverse flow — no picking/staging/loading stages
  const pick=[...d.querySelectorAll('.row')].find(r=>/QU-1330/.test(r.textContent));
  ok('pickup shown', !!pick);
  ok('pickup has no install stages', pick.querySelectorAll('.stage').length===0);
  ok('pickup marked as such', /Pickup/.test(pick.textContent));

  // Every row now says what has to happen that day
  ok('install rows are labelled', [...d.querySelectorAll('.kind.install')].length>0);
  ok('load bay rows are labelled', [...d.querySelectorAll('.kind.bay')].length===1);
  ok('truck loading rows are labelled', [...d.querySelectorAll('.kind.to_load')].length===1);
  const bayRow=[...d.querySelectorAll('.row')].find(r=>r.querySelector('.kind.bay'));
  ok('a bay row sits on the day it must be done', cols[0].contains(bayRow));
  ok('and says when the job actually installs', /Installs/.test(bayRow.textContent));
  ok('a bay row shows only picking and staging', bayRow.querySelectorAll('.stage').length===2);
  const loadRow=[...d.querySelectorAll('.row')].find(r=>r.querySelector('.kind.to_load'));
  ok('a load row shows picking and loading', loadRow.querySelectorAll('.stage').length===2);

  // links to act on it
  ok('links to the picking list', !!d.querySelector('a[href="/stylist/J1"]'));
  ok('links to the loading list', !!d.querySelector('a[href="/driver/J1"]'));

  ok('address shows the suburb', /Somers St, Ascot/.test(d.body.textContent));
  ok('state and postcode dropped', !/QLD 4007/.test(d.body.textContent));
  ok('photo deadline shown', /11:00 AM/.test(d.body.textContent));
  ok('the rules are spelled out', /picking complete 2 days before/.test(d.body.textContent));

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail?1:0);
},350);
