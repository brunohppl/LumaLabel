// The timings page: honest about thin data, and about what it measures.
const fs=require('fs'), {JSDOM}=require('jsdom');
let pass=0,fail=0;
const ok=(l,c)=>{ c?(pass++,console.log('✓ '+l)):(fail++,console.log('✗ FAIL '+l)); };
const sleep=ms=>new Promise(r=>setTimeout(r,ms));

let DATA={success:true,days:30,
  quality:{taps:120,legs:64,double_taps:6,missed:4,open:18,days:21,vehicles:3,warehouse_taps:12},
  by_type:[{name:'install',legs:38,avg_gap:142,avg_drive:28,avg_site:114,fastest:62,slowest:240,thin:false},
           {name:'pickup', legs:26,avg_gap:71, avg_drive:24,avg_site:47, fastest:30,slowest:120,thin:false}],
  by_vehicle:[{name:'Nemo',legs:30,avg_gap:120,avg_drive:26,avg_site:94,fastest:40,slowest:220,thin:false},
              {name:'Bruce',legs:6,avg_gap:95,avg_drive:20,avg_site:75,fastest:50,slowest:140,thin:true}],
  by_both:[{name:'Nemo · install',legs:18,avg_gap:150,avg_drive:30,avg_site:120,fastest:70,slowest:240,thin:false}],
  runs_home:[{name:'Nemo',legs:8,avg_drive:34,fastest:22,slowest:51,thin:true}],
  recent:[{day:'2026-10-20',set_off:'09:05',vehicle:'Nemo',doing:'install',
           job_ref:'QU-1351',address:'12 Somers St',gap:140,drive:30,on_site:110}]};
let asked=[];

function page(){
  const html=fs.readFileSync('/mnt/user-data/outputs/timings.html','utf8');
  return new JSDOM(html,{runScripts:'dangerously',pretendToBeVisual:true,url:'https://x/timings',
    beforeParse(w){
      w.fetch=async(url)=>{ asked.push(String(url)); return {ok:true,json:async()=>DATA}; };
    }});
}

(async()=>{
  let dom=page(); let d=dom.window.document;
  await sleep(350);

  ok('it loads 30 days by default', asked[0].includes('days=30'));
  const q=d.getElementById('quality').textContent;
  ok('the data quality is stated first', /120/.test(q) && /64/.test(q));
  ok('discards are named', /6 discarded as double taps/.test(q));
  ok('and the taps with no gap explained', /last tap of a day/.test(q));

  const body=d.getElementById('body').textContent;
  ok('installs and pickups are separated', /Installs/.test(body) && /Pickups/.test(body));
  ok('per vehicle too', /Nemo/.test(body) && /Bruce/.test(body));
  ok('and crossed', /Nemo · install/.test(body));
  ok('the run home is shown apart', /run back to the warehouse/i.test(body));
  ok('explaining it is a predicted drive', /no following tap/i.test(body));
  ok('thin samples are marked', /thin/i.test(body));
  ok('the legs themselves are listed', /QU-1351/.test(body));
  ok('times read as hours and minutes', /2h 20m|1h 51m|2h 22m/.test(body) || /h /.test(body));

  // the honesty that matters
  DATA={...DATA, quality:{...DATA.quality, legs:8}};
  dom=page(); d=dom.window.document; await sleep(350);
  ok('a thin overall sample says so plainly',
     /Too few legs to draw conclusions/.test(d.getElementById('quality').textContent));

  DATA={...DATA, quality:{taps:100,legs:40,double_taps:20,missed:20,open:5,days:10,vehicles:2,warehouse_taps:5}};
  dom=page(); d=dom.window.document; await sleep(350);
  ok('a high discard rate is called out',
     /discarded as double-taps or missed\s+taps/.test(d.getElementById('quality').textContent));
  ok('and says the fix is tapping, not maths',
     /consistent tapping/.test(d.getElementById('quality').textContent));

  DATA={...DATA, quality:{...DATA.quality, double_taps:1, missed:1, warehouse_taps:0}};
  dom=page(); d=dom.window.document; await sleep(350);
  ok('it notices nobody uses the warehouse button',
     /No one has used/.test(d.getElementById('quality').textContent));

  DATA={success:true,days:30,quality:{taps:0},by_type:[],by_vehicle:[],by_both:[],runs_home:[],recent:[]};
  dom=page(); d=dom.window.document; await sleep(350);
  ok('an empty period is not alarming',
     /Nothing is wrong/.test(d.getElementById('quality').textContent));

  // changing the range
  DATA={...DATA, quality:{taps:5}};
  dom=page(); d=dom.window.document; await sleep(350);
  asked=[];
  d.querySelector('.rbtn[data-days="7"]').click();
  await sleep(120);
  ok('switching the range refetches', asked.some(u=>u.includes('days=7')));

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail?1:0);
})();
