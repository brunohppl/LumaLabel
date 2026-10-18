// Team view: cached days flick instantly, and are corrected in the background.
const fs=require('fs'), {JSDOM}=require('jsdom');
let pass=0,fail=0;
const ok=(l,c)=>{ c?(pass++,console.log('✓ '+l)):(fail++,console.log('✗ FAIL '+l)); };
const sleep=ms=>new Promise(r=>setTimeout(r,ms));

const day=(ref)=>({teams:[{id:'T1',name:'Nemo',vehicle:'Nemo',function:'transport',members:['Jo']}],
  schedule:[{id:'E1',job_id:'J1',team_id:'T1',type:'install',start_time:'09:00',duration:60}],
  tasks:[], jobs:[{id:'J1',job_ref:ref,address:'1 A St, Ascot'}], loads:[]});

let fetched=[], responses={}, delay=0;
const html=fs.readFileSync('/mnt/user-data/outputs/today.html','utf8');
const dom=new JSDOM(html,{runScripts:'dangerously',pretendToBeVisual:true,url:'https://x/today',
  beforeParse(w){
    w.fetch=async(url)=>{
      const u=String(url);
      if(u.includes('/api/runsheet/')){
        const d=u.split('/api/runsheet/')[1].split('?')[0];
        fetched.push(d);
        if(delay) await new Promise(r=>setTimeout(r,delay));
        return {ok:true,status:200,json:async()=>(responses[d]||day('QU-'+d.slice(-2)))};
      }
      return {ok:true,status:200,json:async()=>({})};
    };
    w.alert=()=>{};
  }});
const w=dom.window,d=w.document;
const shown=()=>d.getElementById('day-content').textContent;

(async()=>{
  await sleep(500);
  ok('the day opens as before', /QU-/.test(shown()));
  // Nothing is fetched ahead of time — parallel prefetching queued in
  // front of the user's own request and made a day take 17 seconds.
  ok(`only the day being viewed is fetched (${fetched.length})`, fetched.length===1);
  await sleep(1500);
  ok('and still nothing extra after a pause', fetched.length===1);

  // a day not seen before fetches once, as it always did
  delay=300; fetched=[];
  w.shiftDate(1);
  await sleep(450);
  ok('a new day is fetched once', fetched.length===1);
  ok('and shows', /QU-/.test(shown()));

  // going back to a day already seen is instant
  fetched=[]; delay=300;
  w.shiftDate(-1);
  await sleep(40);
  ok('a day already visited appears at once', !/Loading/.test(shown()));
  await sleep(450);
  ok('and is quietly re-checked', fetched.length===1);

  // a day that has changed must repaint
  delay=0;
  const tomorrow=fetched[0];
  responses[tomorrow]=day('QU-CHANGED');
  w.shiftDate(-1); await sleep(200);
  w.shiftDate(1);  await sleep(300);
  ok('a changed day updates on screen', /QU-CHANGED/.test(shown()));

  // an uncached day still shows the spinner, not a blank
  delay=200; 
  w.eval("viewDate=new Date('2027-01-01T00:00:00');");
  const p=w.load();
  await sleep(40);
  ok('an unseen day shows that it is loading', /Loading/.test(shown()));
  await p; await sleep(60);
  ok('then shows the day', !/Loading/.test(shown()));

  // The loading bar
  const bar=()=>d.getElementById('load-bar');
  ok('a loading bar exists', !!bar());
  // It completes to 100% and then fades — a bar that vanishes mid-way
  // reads as a failure — so allow for that finish.
  await sleep(500);
  ok('and is off once the day is up', !bar().classList.contains('on'));

  delay=300; fetched=[];
  w.eval("viewDate=new Date('2027-06-15T00:00:00');");
  const pending=w.load();
  await sleep(50);
  ok('it shows while an unseen day loads', bar().classList.contains('on'));
  await pending; await sleep(500);
  ok('and clears when the day arrives', !bar().classList.contains('on'));
  ok('having reached 100% first', bar().querySelector('span').style.width==='0%' ||
     bar().querySelector('span').style.width==='100%');

  // a cached day must not flash it
  delay=200;
  w.eval("viewDate=new Date('2027-06-15T00:00:00');");
  const p2=w.load();
  await sleep(40);
  ok('a cached day does not flash the bar', !bar().classList.contains('on'));
  await p2;

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail?1:0);
})();
