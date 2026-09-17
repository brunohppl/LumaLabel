// Loading readiness board: to-load tiles only, state from the job status.
const fs=require('fs'), {JSDOM}=require('jsdom');
const html=fs.readFileSync('/mnt/user-data/outputs/readiness.html','utf8');
let pass=0,fail=0;
const ok=(l,c)=>{ c?(pass++,console.log('✓ '+l)):(fail++,console.log('✗ FAIL '+l)); };

const D=['2026-09-16','2026-09-17','2026-09-18'];
const DATA={
  days:D,
  teams:[{id:'A1',date:D[0],name:'Nemo',vehicle:'Nemo',function:'transport',sort_order:1},
         {id:'A2',date:D[0],name:'Bruce',vehicle:'Bruce',function:'transport',sort_order:0},
         {id:'A3',date:D[0],name:'Marlin',vehicle:'Marlin',function:'styling',sort_order:5},
         {id:'B1',date:D[1],name:'Nemo',vehicle:'Nemo',function:'transport',sort_order:1}],
  jobs:[
    {job_id:'J1',ref:'QU-1351',address:'12 Somers St, Ascot',date:D[0],due_date:'2026-09-14',
     overdue:true,kind:'to_load',state:'overdue',label:'Not loaded',status:'ready_to_load',team_id:'A1'},
    {job_id:'J2',ref:'QU-1362',address:'9 Vine St, Clayfield',date:D[0],due_date:D[0],
     overdue:false,kind:'to_load',state:'late',label:'Not ready',status:'ready',team_id:'A2'},
    {job_id:'J3',ref:'QU-1370',address:'5 Kent Rd, Wooloowin',date:D[1],due_date:D[1],
     overdue:false,kind:'to_load',state:'warn',label:'Not ready yet',status:'ready',team_id:'B1'},
    {job_id:'J4',ref:'QU-1380',address:'7 Forfar St',date:D[0],due_date:D[0],
     overdue:false,kind:'to_load',state:'done',label:'Loaded',status:'loaded',team_id:'A1'},
    // same job, two crews — one tile each
    {job_id:'J5',ref:'QU-1390',address:'3 Split St',date:D[0],due_date:D[0],
     overdue:false,kind:'to_load',state:'due',label:'Ready to load',status:'ready_to_load',team_id:'A1'},
    {job_id:'J5',ref:'QU-1390',address:'3 Split St',date:D[0],due_date:D[0],
     overdue:false,kind:'to_load',state:'due',label:'Ready to load',status:'ready_to_load',team_id:'A2'}]};

const dom=new JSDOM(html,{runScripts:'dangerously',url:'http://x/readiness',
  beforeParse(w){ w.fetch=async()=>({ok:true,status:200,json:async()=>DATA}); }});
const w=dom.window,d=w.document;

setTimeout(()=>{
  const rows=[...d.querySelectorAll('.rowhead .crew')].map(x=>x.textContent.trim());
  const cells=[...d.querySelectorAll('.cell')];
  const chip=ref=>[...d.querySelectorAll('.chip')].filter(c=>c.textContent.includes(ref));

  // ── layout kept ──
  ok('three day columns', d.querySelectorAll('.hcell').length===4);
  ok('crews down the left', rows.includes('Nemo')&&rows.includes('Bruce'));
  ok('runsheet order kept', rows.indexOf('Bruce')<rows.indexOf('Nemo'));
  ok('styling crews still excluded', !rows.includes('Marlin'));

  // ── tiles are simple ──
  const c=chip('QU-1362')[0];
  ok('the action is named', /To Load/i.test(c.querySelector('.chip-kind').textContent));
  ok('the job number leads', c.querySelector('.chip-ref').textContent.trim()==='QU-1362');
  ok('the state is in words', /Not ready/.test(c.querySelector('.chip-status').textContent));
  ok('no item counts anywhere', ![...d.querySelectorAll('.chip')].some(x=>/\d+\/\d+/.test(x.textContent)));

  // ── states read differently ──
  ok('late is red with an exclamation',
     c.classList.contains('late') && c.querySelector('.bang').textContent==='!');
  const warn=chip('QU-1370')[0];
  ok('a warning is amber with a warning sign',
     warn.classList.contains('warn') && warn.querySelector('.bang').textContent==='⚠');
  const over=chip('QU-1351')[0];
  ok('overdue has its own colour', over.classList.contains('overdue'));
  ok('and says so', /overdue/i.test(over.textContent));
  ok('overdue sits in the Today column', cells.indexOf(over.closest('.cell'))%3===0);
  ok('and the tooltip says when it was due', /Was due/.test(over.getAttribute('title')));
  const done=chip('QU-1380')[0];
  ok('loaded is green with no mark',
     done.classList.contains('done') && !done.querySelector('.bang'));
  const due=chip('QU-1390')[0];
  ok('ready-to-load on the day is amber, no exclamation',
     due.classList.contains('due') && !due.querySelector('.bang'));

  // ── one tile per crew ──
  ok('a job on two crews shows twice', chip('QU-1390').length===2);
  ok('once on each crew',
     chip('QU-1390').map(x=>x.closest('.cell')).filter((v,i,a)=>a.indexOf(v)===i).length===2);

  // ── read-only ──
  const heads=[...d.querySelectorAll('.hcell')];
  ok('today counts what needs attention', /2 need attention/.test(heads[1].textContent));
  ok('a settled day says nothing', !/need attention/.test(heads[3].textContent));
  ok('no links', d.querySelectorAll('a[href]').length===0);
  ok('full screen available', !!d.getElementById('fs-btn'));

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail?1:0);
},350);
