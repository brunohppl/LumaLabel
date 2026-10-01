const fs=require('fs'), {JSDOM}=require('jsdom');
const html=fs.readFileSync('/mnt/user-data/outputs/freelabels.html','utf8');
let pass=0,fail=0;
const ok=(l,c)=>{ c?(pass++,console.log('✓ '+l)):(fail++,console.log('✗ FAIL '+l)); };
let posted=null;

const dom=new JSDOM(html,{runScripts:'dangerously',pretendToBeVisual:true,url:'https://x/freelabels',
  beforeParse(w){
    w.fetch=async(url,opt={})=>{ posted=JSON.parse(opt.body||'{}');
      return {ok:true,status:200,blob:async()=>({}),json:async()=>({})}; };
    w.URL.createObjectURL=()=>'blob:x'; w.URL.revokeObjectURL=()=>{};
  }});
const w=dom.window,d=w.document;
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const msg=()=>d.getElementById('msg').textContent;

(async()=>{
  await sleep(250);
  ok('sixteen label boxes, matching the sheet', d.querySelectorAll('.sheet textarea').length===16);
  ok('laid out two across', /grid-template-columns:1fr 1fr/.test(html));
  ok('each box is numbered', d.querySelectorAll('.cell-no').length===16);

  // nothing typed
  await w.makePdf(); await sleep(40);
  ok('refuses an empty sheet', /at least one/.test(msg()));
  ok('and sends nothing', posted===null);

  // bulk fill
  d.getElementById('bulk').value='Alpha\nBravo\nCharlie';
  w.fillFromBulk();
  ok('bulk fill puts one line per label', d.getElementById('l0').value==='Alpha' && d.getElementById('l2').value==='Charlie');
  ok('and leaves the rest empty', d.getElementById('l3').value==='');

  await w.makePdf(); await sleep(60);
  ok('all sixteen slots are sent', posted.labels.length===16);
  ok('with the typed text in order', posted.labels[0]==='Alpha' && posted.labels[2]==='Charlie');
  ok('empty slots stay empty so a part-used sheet works', posted.labels[3]==='');
  ok('and it reports what was made', /Downloaded 3/.test(msg()));

  // typing directly, including two lines on one label
  w.clearAll();
  d.getElementById('l5').value='Top line\nSecond line';
  await w.makePdf(); await sleep(60);
  ok('a label can hold two lines', posted.labels[5]==='Top line\nSecond line');
  ok('position is preserved', posted.labels[0]==='' && posted.labels[5]!=='');

  w.clearAll();
  ok('clear empties every box', [...d.querySelectorAll('.sheet textarea')].every(t=>t.value===''));

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail?1:0);
})();
