// The Day view tab, with the real dayview.js loaded alongside today.html.
const fs=require('fs'), {JSDOM}=require('jsdom');
const html=fs.readFileSync('/mnt/user-data/outputs/today.html','utf8');
const dvjs=fs.readFileSync('/mnt/user-data/outputs/static/dayview.js','utf8');
let pass=0,fail=0;
const ok=(l,c)=>{ c?(pass++,console.log('✓ '+l)):(fail++,console.log('✗ FAIL '+l)); };

const DATA={
  teams:[{id:'T1',name:'Nemo',vehicle:'Nemo',function:'transport',lead:'Savio',members:['Savio','Nick']},
         {id:'T2',name:'Marlin',vehicle:'Marlin',function:'styling',lead:'Addy',members:['Addy','Montie']},
         {id:'T3',name:'Warehouse',vehicle:null,function:'warehouse',lead:'Jo',members:['Jo']}],
  schedule:[{id:'E1',job_id:'J1',team_id:'T1',type:'load',start_time:'07:30',duration:60},
            {id:'E2',job_id:'J2',team_id:'T1',type:'install',start_time:'09:00',duration:90},
            {id:'E3',job_id:'J2',team_id:'T2',type:'install',start_time:'09:00',duration:180},
            {id:'E4',job_id:'J3',vehicle:'Nemo',type:'collect',start_time:'13:00',duration:45}, // legacy, no team_id
            {id:'E5',job_id:'J4',team_id:'T1',type:'install',start_time:null,duration:60}],     // unplaced
  tasks:[{id:'K1',team_id:'T3',title:'Pick QU-1370',start_time:'08:00',duration:90},
         {id:'K2',team_id:'T1',kind:'break',title:'Lunch',start_time:'11:00',duration:30}],
  jobs:[{id:'J1',job_ref:'QU-1351',address:'66 Hope St, South Brisbane'},
        {id:'J2',job_ref:'QU-1348',address:'12 Somers St, Indooroopilly'},
        {id:'J3',job_ref:'QU-1330',address:'5 Kent Rd, Wooloowin'},
        {id:'J4',job_ref:'QU-1399',address:'9 Vine St'}]
};

const dom=new JSDOM(html,{runScripts:'dangerously',pretendToBeVisual:true,url:'https://x.test/today',
  beforeParse(w){ w.fetch=async()=>({ok:true,status:200,json:async()=>DATA}); }});
const w=dom.window,d=w.document;
const sleep=ms=>new Promise(r=>setTimeout(r,ms));

(async()=>{
  await sleep(300);
  // load the real day view script the way the browser would
  const s=d.createElement('script'); s.textContent=dvjs; d.body.appendChild(s);
  await sleep(50);
  ok('dayview.js registers itself', typeof w.DayView==='object');

  w.eval('dayData = '+JSON.stringify(DATA)+';');
  w.showTab('day');
  await sleep(80);

  const root=d.getElementById('dayview-root');
  ok('timeline renders', root.querySelectorAll('.dv-row').length===3);
  ok('one row per crew', root.querySelectorAll('.rowhead .crew-name').length===3);
  ok('crew members listed', /Savio/.test(root.innerHTML));

  const blocks=[...root.querySelectorAll('.blk')];
  ok('blocks drawn for scheduled work ('+blocks.length+')', blocks.length===6);
  ok('unplaced entries are excluded', !/QU-1399/.test(root.innerHTML));
  ok('legacy vehicle-only entry still placed', /QU-1330/.test(root.innerHTML));
  ok('tasks appear', /Pick QU-1370/.test(root.innerHTML));
  ok('breaks styled separately', root.querySelectorAll('.blk.brk').length===1);
  ok('breaks not counted as stops', d.getElementById('dv-stops').textContent==='5');
  ok('idle time surfaced', root.querySelectorAll('.idle').length>0);
  ok('idle total shown', /[hm]/.test(d.getElementById('dv-idle').textContent));

  // zoom
  const w1=root.querySelector('.lane').style.width;
  w.DayView.render(DATA,new Date());
  root.querySelector('#dv-in').click();
  await sleep(30);
  const w2=root.querySelector('.lane').style.width;
  ok('zoom in widens the lane ('+w1+' -> '+w2+')', parseFloat(w2)>parseFloat(w1));
  root.querySelector('#dv-out').click(); await sleep(20);
  ok('zoom out narrows it again', parseFloat(root.querySelector('.lane').style.width)<parseFloat(w2));
  [...root.querySelectorAll('#dv-presets button')].find(b=>b.dataset.z==='4.5').click();
  await sleep(30);
  ok('preset applies', root.querySelector('#dv-presets button.on').dataset.z==='4.5');

  // block detail
  root.querySelectorAll('.blk')[0].dispatchEvent(new w.MouseEvent('click',{bubbles:true}));
  await sleep(30);
  ok('tapping a block opens details', d.getElementById('dv-panel').classList.contains('open'));
  ok('details name the job', d.getElementById('dv-p-title').textContent.length>1);
  d.getElementById('dv-panel-close').click();
  ok('panel closes', !d.getElementById('dv-panel').classList.contains('open'));

  // empty day must not look broken
  w.DayView.render({teams:[],schedule:[],tasks:[],jobs:[]}, new Date());
  ok('empty day shows a message', /No crews/.test(root.innerHTML));

  // malformed data must not throw
  let threw=false;
  try{ w.DayView.render({teams:[{id:'T9'}],schedule:[{team_id:'T9',start_time:'bad'}],tasks:null,jobs:null}, null); }
  catch(e){ threw=true; }
  ok('malformed data does not throw', !threw);

  // and the cards are still fine underneath
  w.showTab('cards');
  await sleep(30);
  ok('cards still render after all that', d.querySelectorAll('.card').length>0);

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail?1:0);
})();
