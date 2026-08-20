// Smoke test for the deliveries page: loads it, stubs the network, opens a
// saved project and drives the line editor.
const fs=require('fs'); const {JSDOM}=require('jsdom');
const html=fs.readFileSync('/mnt/user-data/outputs/deliveries.html','utf8').replace(/\{\{[^}]*\}\}/g,'');
const PROJECT={id:'P1',name:'Somers Residence',line_count:3};
const LINES=[
 {id:'L1',project_id:'P1',section:'Living',product_name:'Arc Sofa',brand:'Calibre',sku:'DT12137-BB',
  qty_expected:2,qty_received:0,is_service:false,programma_status:'paid'},
 {id:'L2',project_id:'P1',section:'Living',product_name:'Side Table',brand:'Globe West',sku:null,
  qty_expected:1,qty_received:1,is_service:false,programma_status:'delivered'},
 {id:'L3',project_id:'P1',section:null,product_name:null,item_label:'Delivery fee',
  qty_expected:1,qty_received:0,is_service:true,programma_status:'paid'}];
let sent=[], failNext=false;
const dom=new JSDOM(html,{runScripts:'dangerously',url:'http://localhost/deliveries',beforeParse(w){
  w.fetch=async(url,opts={})=>{
    const m=(opts.method||'GET').toUpperCase();
    if(m!=='GET'){ sent.push({url,method:m,body:opts.body?JSON.parse(opts.body):null}); }
    if(failNext) return {ok:false,status:400,json:async()=>({success:false,error:'database said no'})};
    if(m==='PATCH'){
      const body=JSON.parse(opts.body);
      const id=url.split('/').pop();
      const base=LINES.find(l=>l.id===id);
      return {ok:true,status:200,json:async()=>({success:true,line:Object.assign({},base,body)})};
    }
    if(m==='DELETE') return {ok:true,status:200,json:async()=>({success:true})};
    if(url.includes('/projects/P1')) return {ok:true,status:200,json:async()=>({success:true,project:PROJECT,lines:LINES})};
    if(url.includes('/projects')) return {ok:true,status:200,json:async()=>({success:true,projects:[PROJECT]})};
    return {ok:true,status:200,json:async()=>({success:true})};
  };
  w.alert=m=>{(w.__alerts=w.__alerts||[]).push(m);};
  w.confirm=()=>true;
}});
const w=dom.window,d=w.document;
let pass=0,fail=0; const ok=(l,c)=>{c?pass++:fail++;console.log((c?'✓ ':'✗ FAIL ')+l);};
const sleep=ms=>new Promise(r=>setTimeout(r,ms));

(async()=>{
  await sleep(900);
  if(typeof w.openProject==='function'){ await w.openProject('P1'); await sleep(300); }
  else { w.PROJ_LINES=LINES; w.renderProjectLines && w.renderProjectLines(); await sleep(100); }

  ok('edit buttons rendered on saved lines ('+d.querySelectorAll('.edit-btn').length+')',
     d.querySelectorAll('.edit-btn').length>0);

  w.openEdit('L1'); await sleep(50);
  ok('editor opens', d.getElementById('edit-pop').classList.contains('open'));
  ok('fields pre-filled from the line', d.getElementById('e-product').value==='Arc Sofa');
  ok('quantity pre-filled', d.getElementById('e-qty').value==='2');
  ok('shows how many already arrived', /0 already received/.test(d.getElementById('e-received').textContent));

  sent=[];
  d.getElementById('e-sku').value='CDT12137-BB';
  d.getElementById('e-section').value='Lounge';
  await w.saveEdit(); await sleep(80);
  const patch=sent.find(s=>s.method==='PATCH');
  ok('saves a PATCH', !!patch);
  ok('sends the corrected SKU', patch && patch.body.sku==='CDT12137-BB');
  ok('sends the corrected room', patch && patch.body.section==='Lounge');
  ok('editor closes after saving', !d.getElementById('edit-pop').classList.contains('open'));
  ok('table shows the new value', d.body.textContent.includes('CDT12137-BB'));

  // validation
  w.openEdit('L1'); await sleep(30);
  d.getElementById('e-qty').value='0';
  await w.saveEdit(); await sleep(30);
  ok('rejects a zero quantity', /at least 1/i.test(d.getElementById('edit-err').textContent));
  ok('and does not close on error', d.getElementById('edit-pop').classList.contains('open'));

  // server failure surfaces
  d.getElementById('e-qty').value='2'; failNext=true;
  await w.saveEdit(); await sleep(50);
  ok('server error is shown', /database said no/.test(d.getElementById('edit-err').textContent));
  failNext=false;

  // delete
  sent=[];
  await w.deleteLine(); await sleep(50);
  ok('delete sends a DELETE', sent.some(s=>s.method==='DELETE'));
  ok('line removed from the table', !d.body.textContent.includes('Arc Sofa'));

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail?1:0);
})().catch(e=>{console.log('HARNESS ERROR: '+e.message);process.exit(2);});
