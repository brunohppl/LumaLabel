// The scheduler grid must follow the space it actually has.
const fs=require('fs'), {JSDOM}=require('jsdom');
let pass=0,fail=0;
const ok=(l,c)=>{ c?(pass++,console.log('✓ '+l)):(fail++,console.log('✗ FAIL '+l)); };
const sleep=ms=>new Promise(r=>setTimeout(r,ms));

const DATE='2026-08-20';
const PAYLOAD={teams:[{id:'T1',name:'Nemo',vehicle:'Nemo',function:'transport',date:DATE,sort_order:0},
                      {id:'T2',name:'Bruce',vehicle:'Bruce',function:'transport',date:DATE,sort_order:1}],
  schedule:[],tasks:[],jobs:[],loads:[],jobs_nearby:[]};

const html=fs.readFileSync('/mnt/user-data/outputs/runsheet.html','utf8');
let renders=0;
const dom=new JSDOM(html,{runScripts:'dangerously',pretendToBeVisual:true,url:'http://localhost/scheduler',
  beforeParse(w){
    w.fetch=async(url)=>String(url).includes('/api/runsheet/')
      ? {ok:true,status:200,json:async()=>PAYLOAD}
      : {ok:true,status:200,json:async()=>([])};
    w.alert=()=>{};
  }});
const w=dom.window,d=w.document;

(async()=>{
  await sleep(500);
  ok('the grid renders', !!d.getElementById('rs-inner'));

  // count renders from here
  const origRender=w.render;
  w.render=function(){ renders++; return origRender.apply(this, arguments); };

  // a resize must redraw
  renders=0;
  Object.defineProperty(w, 'innerWidth', {value:390, configurable:true});
  w.dispatchEvent(new w.Event('resize'));
  await sleep(400);
  ok('resizing redraws the grid', renders>=1);

  // rotation too
  renders=0;
  Object.defineProperty(w, 'innerWidth', {value:844, configurable:true});
  w.dispatchEvent(new w.Event('orientationchange'));
  await sleep(400);
  ok('rotating redraws it', renders>=1);

  // but a tiny jitter must not — an address bar sliding away shouldn't rebuild
  renders=0;
  w.dispatchEvent(new w.Event('resize'));
  await sleep(400);
  ok('an identical width does not redraw', renders===0);

  // and a burst of resize events should settle into one redraw
  renders=0;
  for(let i=0;i<10;i++){
    Object.defineProperty(w,'innerWidth',{value:400+i*30,configurable:true});
    w.dispatchEvent(new w.Event('resize'));
  }
  await sleep(500);
  ok(`a burst settles into one redraw (${renders})`, renders===1);

  // the width used comes from the container, with a floor
  ok('a sensible minimum width is enforced',
     /Math\.max\(280,/.test(html.replace(/\s+/g,' ')) ||
     /Math\.max\(280/.test(fs.readFileSync('/mnt/user-data/outputs/runsheet.html','utf8')));

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail?1:0);
})();
