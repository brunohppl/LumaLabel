// Warehouse navigation: same prompt as a job, Slack names the truck, and
// every tap is recorded.
const fs=require('fs'), {JSDOM}=require('jsdom');
let pass=0,fail=0;
const ok=(l,c)=>{ c?(pass++,console.log('✓ '+l)):(fail++,console.log('✗ FAIL '+l)); };
const sleep=ms=>new Promise(r=>setTimeout(r,ms));

const DATE='2026-08-20';
const PAYLOAD={teams:[{id:'T1',name:'Nemo',vehicle:'Nemo',function:'transport',date:DATE,members:['Jo']}],
  schedule:[{id:'E1',job_id:'J1',team_id:'T1',type:'install',date:DATE,start_time:'09:00',duration:60}],
  tasks:[], jobs:[{id:'J1',job_ref:'QU-1',address:'1 A St, Ascot'}], loads:[]};

(async()=>{
  // ── Team view ──
  let posted=null;
  const html=fs.readFileSync('/mnt/user-data/outputs/today.html','utf8');
  const dom=new JSDOM(html,{runScripts:'dangerously',pretendToBeVisual:true,url:'https://x/today',
    beforeParse(w){
      w.fetch=async(url,opt={})=>{
        const u=String(url);
        if(u.includes('/api/warehouse')) return {ok:true,json:async()=>({address:'63 Westgate St, Wacol QLD 4076'})};
        if(u.includes('/api/eta/warehouse')){ posted={url:u,body:JSON.parse(opt.body)};
          return {ok:true,json:async()=>({success:true,eta_text:'3:40pm'})}; }
        if(u.includes('/api/runsheet/')) return {ok:true,json:async()=>PAYLOAD};
        return {ok:true,json:async()=>({})};
      };
      w.open=()=>{};
      w.navigator.geolocation={getCurrentPosition:(cb)=>cb({coords:{latitude:-27.5,longitude:153.0}})};
    }});
  const w=dom.window,d=w.document;
  await sleep(400);

  const btns=[...d.querySelectorAll('.wh-nav')];
  ok('a warehouse button per crew column', btns.length===1);
  ok('it knows which crew', /T1/.test(btns[0].getAttribute('onclick')));
  ok('and which truck', /Nemo/.test(btns[0].getAttribute('onclick')));
  ok('it is not a page-level button', !d.querySelector('.fn-filter .wh-nav'));

  // the same prompt as a job
  w.navWarehouse('T1','Nemo'); await sleep(40);
  ok('the Slack prompt opens', d.getElementById('slack-overlay').classList.contains('open'));

  // declining still navigates, posts nothing
  posted=null;
  w.slackChoice('no'); await sleep(60);
  ok('"just navigate" posts nothing', posted===null);

  // accepting posts with the truck named
  w.navWarehouse('T1','Nemo'); await sleep(30);
  w.slackChoice('yes'); await sleep(120);
  ok('accepting calls the warehouse endpoint', posted && /eta\/warehouse/.test(posted.url));
  ok('naming the truck', posted && posted.body.vehicle==='Nemo');
  ok('and the crew', posted && posted.body.team_id==='T1');
  ok('with a position', posted && posted.body.lat===-27.5);

  // cancelling does nothing at all
  posted=null;
  w.navWarehouse('T1','Nemo'); await sleep(20);
  w.slackChoice('cancel'); await sleep(60);
  ok('cancel posts nothing', posted===null);
  ok('and closes the prompt', !d.getElementById('slack-overlay').classList.contains('open'));

  // ── Driver page ──
  let dposted=null;
  const dh=fs.readFileSync('/mnt/user-data/outputs/driver.html','utf8');
  const dd=new JSDOM(dh,{runScripts:'dangerously',pretendToBeVisual:true,url:'https://x/driver/J1',
    beforeParse(w2){
      w2.fetch=async(url,opt={})=>{
        const u=String(url);
        if(u.includes('/api/warehouse')) return {ok:true,json:async()=>({address:'63 Westgate St, Wacol QLD 4076'})};
        if(u.includes('/api/eta/warehouse')){ dposted={url:u,body:JSON.parse(opt.body)};
          return {ok:true,json:async()=>({success:true})}; }
        if(u.includes('/eta')){ dposted={url:u,body:JSON.parse(opt.body)}; return {ok:true,json:async()=>({})}; }
        return {ok:true,json:async()=>({job:{id:'J1',truck:'Nigel'},items:[],schedule:[]})};
      };
      w2.open=()=>{};
      w2.navigator.geolocation={getCurrentPosition:(cb)=>cb({coords:{latitude:-27.6,longitude:153.1}})};
    }});
  await sleep(400);
  const w3=dd.window, d3=dd.window.document;
  ok('driver page has the button', !!d3.getElementById('wh-nav'));
  w3.eval("job={id:'J1',truck:'Nigel'};");
  w3.navWarehouse(); await sleep(30);
  ok('it opens the same prompt the address uses',
     d3.getElementById('slack-confirm-overlay').style.display==='flex');
  w3.slackConfirmChoice('yes'); await sleep(150);
  ok('and posts to the warehouse endpoint', dposted && /eta\/warehouse/.test(dposted.url));
  ok('naming the truck', dposted && dposted.body.vehicle==='Nigel');

  // the flag must reset, or every later navigation would be treated as a
  // warehouse run. (A second ETA is blocked by the page's own cooldown, so
  // the flag is what to check.)
  ok('the warehouse flag resets after use', w3.eval('_warehouseNav')===false);
  w3.navWarehouse(); await sleep(20);
  w3.slackConfirmChoice('no'); await sleep(40);
  ok('and resets when the Slack post is declined', w3.eval('_warehouseNav')===false);

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail?1:0);
})();
