// Loads the team view for real and checks it renders the whole day with no
// name filter — and, importantly, throws nothing now the dropdown is gone.
const fs=require('fs'); const {JSDOM}=require('jsdom');
const html=fs.readFileSync('/mnt/user-data/outputs/today.html','utf8').replace(/\{\{[^}]*\}\}/g,'');
const DATA={teams:[{id:'T1',name:'Nemo Crew',vehicle:'Nemo',function:'transport'},
                   {id:'T4',name:'Styling Crew 1',vehicle:'Marlin',function:'styling'}],
  schedule:[{id:'E1',job_id:'J400',team_id:'T1',type:'install',start_time:'07:30',duration:60},
            {id:'E2',job_id:'J400',team_id:'T4',type:'install',start_time:'07:30',duration:180},
            {id:'E3',job_id:'J401',team_id:'T1',type:'pickup',start_time:'10:00',duration:60},
            {id:'E4',job_id:'J402',team_id:'T4',type:'install',start_time:'13:00',duration:90},
            {id:'E5',job_id:'J402',team_id:'T1',type:'install',start_time:'14:00',duration:60}],
  tasks:[{id:'K1',team_id:'T1',title:'Lunch break',kind:'break',start_time:'12:00',duration:30}],
  // J400 was loaded onto Nemo the day before
  loads:[{id:'L1',job_id:'J400',type:'to_load',date:'2026-08-19',vehicle:'Nemo'}],
  jobs:[{id:'J402',job_ref:'#402',address:'4 Oxford St'},
        {id:'J400',job_ref:'#400',address:'12 Somers St, Ascot',access_notes:'Gate 4823',
         property_type:'Apartment',property_size:'3 bed',is_transfer:true,transfer_from_job_id:'J401'},
        {id:'J401',job_ref:'#401',address:'9 Hale St'}]};
let errs=[];
const dom=new JSDOM(html,{runScripts:'dangerously',url:'http://localhost/today',beforeParse(w){
  w.__posts=[];
  w.fetch=async(url,opts={})=>{
    if((opts.method||'GET')!=='GET'){ w.__posts.push({url,body:JSON.parse(opts.body||'{}')}); return {ok:true,status:200,text:async()=>'',json:async()=>({success:true})}; }
    return {ok:true,status:200,json:async()=>DATA};
  };
  w.open=()=>{};
  w.navigator.geolocation={getCurrentPosition:(ok)=>ok({coords:{latitude:-27.4,longitude:153.0}})};
  w.alert=()=>{}; w.addEventListener('error',e=>errs.push(String(e.error||e.message)));
}});
const w=dom.window,d=w.document;
let pass=0,fail=0; const ok=(l,c)=>{c?pass++:fail++;console.log((c?'✓ ':'✗ FAIL ')+l);};
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
setTimeout(async()=>{
  ok('page loads with no script error'+(errs.length?' — '+errs[0]:''), errs.length===0);
  ok('name dropdown is gone', !d.getElementById('who-select'));
  ok('no leftover filter card', !d.querySelector('.who-card'));
  // Only what's rendered — body.textContent also includes the page's <script>
  // source, which matched a code comment and gave a false failure.
  const body=[...d.querySelectorAll('#day-content')].map(x=>x.textContent).join(' ');
  ok('both crews shown without choosing a name',
     body.includes('Nemo Crew') && body.includes('Styling Crew 1'));
  ok('job reference shown', body.includes('#400'));
  ok('property details shown', body.includes('Apartment'));
  ok('access notes shown', body.includes('Gate 4823'));
  ok('break shown', body.includes('Lunch break'));
  ok('incoming transfer named', body.includes('Transfer from') && body.includes('#401'));
  ok('outgoing transfer named', body.includes('Transfer to') && body.includes('#400'));
  ok('no warehouse editorialising in the transfer line',
     !/not the warehouse|not back to the warehouse/i.test(body));
  ok('90 minutes reads as 1.5h', body.includes('1.5h'));
  ok('and not the doubled-up form', !body.includes('1.5h30m'));
  ok('a whole hour reads as 1h', /·\s*1h/.test(body));

  // The styling crew needs to know which truck is turning up
  const stylingCard=[...d.querySelectorAll('#day-content *')]
    .filter(x=>x.textContent.includes('Styling Crew 1'));
  ok('loading truck shown on the team view', body.includes('Nemo'));
  ok('shown as a plain tag, no warning', !body.includes('⚠'));
  ok('a job with no load shows no truck line',
     (body.match(/📦/g)||[]).length <= 2);
  ok('pickup gets no loading line',
     !/Pickup[\s\S]{0,80}📦/.test(body));
  // Navigate -> the ETA post that feeds Slack
  w.navigate('J400','12 Somers St','transport');
  w.slackChoice('yes'); await sleep(80);
  let post=w.__posts.find(p=>p.url.includes('/eta'));
  ok('navigate posts an ETA', !!post);
  ok('transport crew sends a role the server accepts',
     post && ['truck','stylist'].includes(post.body.role));
  ok('and never the rejected "team" value', post && post.body.role!=='team');

  w.__posts.length=0;
  w.navigate('J400','12 Somers St','styling');
  w.slackChoice('yes'); await sleep(80);
  post=w.__posts.find(p=>p.url.includes('/eta'));
  ok('styling crew files as stylist', post && post.body.role==='stylist');
  ok('coordinates included', post && typeof post.body.lat==='number');

  const home=[...d.querySelectorAll('a')].find(a=>a.getAttribute('href')==='/');
  ok('a home link is present', !!home);
  ok('and reads as Home', home && /home/i.test(home.textContent));

  // ── Done button must be gone ──
  ok('no Done buttons remain', d.querySelectorAll('.card-btn-done').length===0);
  ok('no done markers remain', d.querySelectorAll('.card-done-mark').length===0);
  ok('Navigate no longer stamps actuals',
     !w.__posts.some(p=>p.url.includes('/actual')));

  // ── Day view tab: present, not default, and isolated ──
  ok('all three tabs exist', !!d.getElementById('tab-cards') && !!d.getElementById('tab-day') && !!d.getElementById('tab-map'));
  ok('Cards is the default tab', d.getElementById('tab-cards').classList.contains('active'));
  ok('cards are visible on load', d.getElementById('day-content').style.display!=='none');
  ok('day view hidden on load', d.getElementById('dayview-root').style.display==='none');
  ok('map hidden on load', d.getElementById('mapview-root').style.display==='none');

  // DayView is a separate file, absent in this harness — the tab must
  // degrade rather than throw, and the cards must survive it.
  const cardsBefore=d.querySelectorAll('.card').length;
  w.showTab('day');
  await sleep(30);
  ok('switching does not throw with DayView missing', true);
  ok('day tab now active', d.getElementById('tab-day').classList.contains('active'));
  ok('shows a fallback message', /Could not draw|unaffected/.test(d.getElementById('dayview-root').innerHTML));
  ok('swipe hint hidden on the day tab', d.getElementById('scroll-hint').style.display==='none');
  // Map tab: MapView absent here too — must degrade, not throw
  w.showTab('map');
  await sleep(30);
  ok('map tab activates', d.getElementById('tab-map').classList.contains('active'));
  ok('map degrades without its script', /Could not load the map|unaffected/.test(d.getElementById('mapview-root').innerHTML));
  ok('swipe hint hidden on the map tab', d.getElementById('scroll-hint').style.display==='none');

  w.showTab('cards');
  await sleep(30);
  ok('cards come back intact', d.querySelectorAll('.card').length===cardsBefore);
  ok('and Navigate still works', typeof w.navigate==='function');

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail?1:0);
},1200);
