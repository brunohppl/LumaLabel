// Loads the team view for real and checks it renders the whole day with no
// name filter — and, importantly, throws nothing now the dropdown is gone.
const fs=require('fs'); const {JSDOM}=require('jsdom');
const html=fs.readFileSync('/mnt/user-data/outputs/today.html','utf8').replace(/\{\{[^}]*\}\}/g,'');
const DATA={teams:[{id:'T1',name:'Nemo Crew',vehicle:'Nemo',function:'transport'},
                   {id:'T4',name:'Styling Crew 1',vehicle:'Marlin',function:'styling'}],
  schedule:[{id:'E1',job_id:'J400',team_id:'T1',type:'install',start_time:'07:30',duration:60},
            {id:'E2',job_id:'J400',team_id:'T4',type:'install',start_time:'07:30',duration:180},
            {id:'E3',job_id:'J401',team_id:'T1',type:'pickup',start_time:'10:00',duration:60},
            {id:'E4',job_id:'J402',team_id:'T4',type:'install',start_time:'13:00',duration:90}],
  tasks:[{id:'K1',team_id:'T1',title:'Lunch break',kind:'break',start_time:'12:00',duration:30}],
  jobs:[{id:'J402',job_ref:'#402',address:'4 Oxford St'},
        {id:'J400',job_ref:'#400',address:'12 Somers St, Ascot',access_notes:'Gate 4823',
         property_type:'Apartment',property_size:'3 bed',is_transfer:true,transfer_from_job_id:'J401'},
        {id:'J401',job_ref:'#401',address:'9 Hale St'}]};
let errs=[];
const dom=new JSDOM(html,{runScripts:'dangerously',url:'http://localhost/today',beforeParse(w){
  w.fetch=async()=>({ok:true,status:200,json:async()=>DATA});
  w.alert=()=>{}; w.addEventListener('error',e=>errs.push(String(e.error||e.message)));
}});
const w=dom.window,d=w.document;
let pass=0,fail=0; const ok=(l,c)=>{c?pass++:fail++;console.log((c?'✓ ':'✗ FAIL ')+l);};
setTimeout(()=>{
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
  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail?1:0);
},1200);
