// A button back to the warehouse, on the team view and the driver page.
const fs=require('fs'), {JSDOM}=require('jsdom');
let pass=0,fail=0;
const ok=(l,c)=>{ c?(pass++,console.log('✓ '+l)):(fail++,console.log('✗ FAIL '+l)); };
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const ADDR='63 Westgate St, Wacol QLD 4076';

function page(file, opts={}){
  const html=fs.readFileSync(file,'utf8');
  return new JSDOM(html,{runScripts:'dangerously',pretendToBeVisual:true,url:'https://x/p',
    beforeParse(w){
      w.fetch=async(url)=>{
        if(String(url).includes('/api/warehouse')){
          if(opts.fail) throw new Error('offline');
          return {ok:true,status:200,json:async()=>(opts.empty?{}:{address:ADDR})};
        }
        return {ok:true,status:200,json:async()=>({teams:[],schedule:[],tasks:[],jobs:[],loads:[],job:{id:'J1'},items:[]})};
      };
      w.alert=()=>{};
    }});
}

(async()=>{
  for(const [name,file] of [['team view','/mnt/user-data/outputs/today.html'],
                            ['driver page','/mnt/user-data/outputs/driver.html']]){
    let dom=page(file); await sleep(350);
    const el=dom.window.document.getElementById('wh-nav');
    ok(`${name}: the button exists`, !!el);
    ok(`${name}: it points at the warehouse`,
       el && /63\+Westgate|63%20Westgate/.test(el.href));
    ok(`${name}: with the postcode`, el && /4076/.test(el.href));
    ok(`${name}: opens in a new tab`, el && el.target==='_blank');
    ok(`${name}: and safely`, el && /noopener/.test(el.rel||''));

    dom=page(file,{fail:true}); await sleep(350);
    const el2=dom.window.document.getElementById('wh-nav');
    ok(`${name}: hidden when the address can't be fetched`,
       el2 && el2.style.display==='none');

    dom=page(file,{empty:true}); await sleep(350);
    const el3=dom.window.document.getElementById('wh-nav');
    ok(`${name}: hidden when no address comes back`,
       el3 && el3.style.display==='none');
  }

  // one copy of the address
  const app=fs.readFileSync('/mnt/user-data/outputs/app.py','utf8');
  ok('the address lives in app.py once', (app.match(/63 Westgate St/g)||[]).length===1);
  for(const f of ['/mnt/user-data/outputs/today.html','/mnt/user-data/outputs/driver.html']){
    ok(`not hard-coded in ${f.split('/').pop()}`,
       !fs.readFileSync(f,'utf8').includes('Westgate'));
  }

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail?1:0);
})();
