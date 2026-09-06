const fs=require('fs'), {JSDOM}=require('jsdom');
let pass=0,fail=0;
const ok=(l,c)=>{ c?(pass++,console.log('✓ '+l)):(fail++,console.log('✗ FAIL '+l)); };

// ── Labels page: the dropdown ──
const idx=fs.readFileSync('/mnt/user-data/outputs/index.html','utf8');
const store={};
const dom=new JSDOM(idx,{runScripts:'dangerously',url:'https://x.test/',
  beforeParse(w){
    w.localStorage.__proto__.getItem=k=>store[k]===undefined?null:store[k];
    w.localStorage.__proto__.setItem=(k,v)=>{store[k]=String(v);};
    w.fetch=async()=>({ok:true,status:200,json:async()=>({})});
  }});
const w=dom.window,d=w.document;

setTimeout(()=>{
  const sel=d.getElementById('label-format');
  ok('the sheet picker exists', !!sel);
  ok('offers both stocks', sel.options.length===2);
  ok('18-up is the first/default option', sel.options[0].value==='18');
  ok('18-up names the Avery stock', /62/.test(sel.options[0].textContent));
  ok('16-up names the current stock', /105/.test(sel.options[1].textContent));

  ok('defaults to 18 with nothing saved', w.labelFormat()===18);
  sel.value='16';
  sel.dispatchEvent(new w.Event('change'));
  ok('choosing 16 is sent', w.labelFormat()===16);
  ok('and remembered', store['luma_label_format']==='16');

  sel.value='18'; sel.dispatchEvent(new w.Event('change'));
  ok('switching back is remembered too', store['luma_label_format']==='18');

  // junk in storage must not produce an invalid format
  sel.value='';
  ok('an unset value falls back to 18', w.labelFormat()===18);

  // ── Jobs page: a reprint uses the same remembered choice ──
  const jobs=fs.readFileSync('/mnt/user-data/outputs/jobs.html','utf8');
  const store2={'luma_label_format':'16'};
  let confirmMsg='', href='';
  const d2=new JSDOM(jobs,{runScripts:'dangerously',url:'https://x.test/jobs',
    beforeParse(w2){
      w2.localStorage.__proto__.getItem=k=>store2[k]===undefined?null:store2[k];
      w2.localStorage.__proto__.setItem=(k,v)=>{store2[k]=String(v);};
      w2.confirm=m=>{confirmMsg=m; return true;};
      w2.fetch=async()=>({ok:true,status:200,json:async()=>([])});
    }});
  const w2=d2.window;
  setTimeout(async()=>{
    const origAppend=d2.window.document.body.appendChild.bind(d2.window.document.body);
    d2.window.document.body.appendChild=(el)=>{ if(el.tagName==='A') href=el.href; return origAppend(el); };
    await w2.downloadLabels('J1','QU-1');
    ok('reprint uses the remembered sheet', /format=16/.test(href));
    ok('and says which sheet before downloading', /105/.test(confirmMsg));
    console.log(`\n${pass} passed, ${fail} failed`);
    process.exit(fail?1:0);
  },250);
},250);
