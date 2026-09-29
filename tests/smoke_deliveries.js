// Smoke test for the deliveries page: loads it, stubs the network, opens a
// saved project and drives the line editor.
const fs=require('fs'); const {JSDOM}=require('jsdom');
const html=fs.readFileSync('/mnt/user-data/outputs/deliveries.html','utf8').replace(/\{\{[^}]*\}\}/g,'');
const PROJECT={id:'P1',name:'Somers Residence',line_count:3};
const LINES=[
 {id:'L1',project_id:'P1',section:'Living',product_name:'Arc Sofa',brand:'Calibre',sku:'DT12137-BB',
  qty_expected:2,qty_received:0,is_service:false,programma_status:'paid',
  photo_url:'https://example.test/p/sofa.png'},
 {id:'L4',project_id:'P1',section:'Bed 1',product_name:'Lamp',brand:'Cult',sku:'LM-1',
  qty_expected:1,qty_received:0,is_service:false,programma_status:'awaiting_freight'},
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

  // Product photos from the export
  const thumbs=[...d.querySelectorAll('img.line-thumb')];
  ok('a line with a photo shows a thumbnail', thumbs.length===1);
  ok('pointing at the stored image', /sofa\.png/.test(thumbs[0].getAttribute('src')));
  ok('lines without a photo leave the cell empty',
     d.querySelectorAll('td.thumb-cell').length>thumbs.length);
  ok('the header has a column for it',
     d.querySelectorAll('thead th').length===d.querySelectorAll('tbody tr:first-child td').length);


  w.openEdit('L1'); await sleep(50);
  ok('editor opens', d.getElementById('edit-pop').classList.contains('open'));
  ok('fields pre-filled from the line', d.getElementById('e-product').value==='Arc Sofa');
  ok('quantity pre-filled', d.getElementById('e-qty').value==='2');
  ok('shows how many already arrived', /0 already received/.test(d.getElementById('e-received').textContent));
  ok('status pre-selected from the line', d.getElementById('e-status').value==='paid');
  ok('the usual statuses are offered',
     ['draft','quoting','in_review','payment_due','paid','delivered']
       .every(v=>[...d.getElementById('e-status').options].some(o=>o.value===v)));

  sent=[];
  d.getElementById('e-status').value='delivered';
  d.getElementById('e-sku').value='CDT12137-BB';
  d.getElementById('e-section').value='Lounge';
  await w.saveEdit(); await sleep(80);
  const patch=sent.find(s=>s.method==='PATCH');
  ok('saves a PATCH', !!patch);
  ok('sends the corrected SKU', patch && patch.body.sku==='CDT12137-BB');
  ok('sends the corrected room', patch && patch.body.section==='Lounge');
  ok('sends the new status', patch && patch.body.programma_status==='delivered');

  // A status the export produced that isn't in our list must survive
  w.openEdit('L4'); await sleep(40);
  ok('unknown status kept and selected', d.getElementById('e-status').value==='awaiting_freight');
  sent=[];
  await w.saveEdit(); await sleep(60);
  const p4=sent.find(s=>s.method==='PATCH');
  ok('saving does not change it', p4 && p4.body.programma_status==='awaiting_freight');
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

  const home=[...d.querySelectorAll('a')].find(a=>a.getAttribute('href')==='/');
  ok('a home link is present', !!home);
  ok('and reads as Home', home && /home/i.test(home.textContent));

  // ── Photo backfill: dry run, confirm, write ──
  ok('the add-photos control exists', !!d.querySelector('.photo-backfill button'));
  let sentPh=[]; let dryReply={success:true,dry_run:true,to_update:3,already_had:1,unmatched:2,unmatched_names:[]};
  let wetReply={success:true,written:3};
  w.fetch=async(url,opt={})=>{
    if(String(url).includes('/photos')){
      const dry=opt.body && opt.body.get && opt.body.get('dry_run');
      sentPh.push(dry?'dry':'write');
      return {ok:true,status:200,json:async()=>(dry?dryReply:wetReply)};
    }
    return {ok:true,status:200,json:async()=>({project:{id:'P1'},lines:[]})};
  };
  w.eval("PROJ={id:'P1'};");

  // declining the confirmation must write nothing
  w.confirm=()=>false;
  await w.checkPhotos(new w.File(['x'],'e.xlsx'));
  await sleep(60);
  ok('a dry run happens first', sentPh[0]==='dry');
  ok('declining writes nothing', !sentPh.includes('write'));
  ok('and says so', /Cancelled/.test(d.getElementById('photo-msg').textContent));

  // accepting writes
  sentPh=[]; w.confirm=()=>true;
  await w.checkPhotos(new w.File(['x'],'e.xlsx'));
  await sleep(60);
  ok('accepting writes', sentPh.includes('write'));
  ok('and reports how many', /Added 3 photos/.test(d.getElementById('photo-msg').textContent));

  // nothing to do is stated, not silently 'done'
  sentPh=[]; dryReply={success:true,dry_run:true,to_update:0,already_had:5,unmatched:0};
  await w.checkPhotos(new w.File(['x'],'e.xlsx'));
  await sleep(60);
  ok('nothing to do writes nothing', !sentPh.includes('write'));
  ok('and explains why', /already have a photo/.test(d.getElementById('photo-msg').textContent));

  // ── Adding new lines from a newer export ──
  ok('the add-items control exists',
     [...d.querySelectorAll('.photo-backfill button')].length===2);
  let sentAdd=[];
  let addDry={success:true,dry_run:true,to_add:3,already_here:90,with_photos:3,
              names:['Side Table','Vase','Runner'],possible_renames:[]};
  w.fetch=async(url,opt={})=>{
    if(String(url).includes('/add-lines')){
      const dry=opt.body && opt.body.get && opt.body.get('dry_run');
      sentAdd.push(dry?'dry':'write');
      return {ok:true,status:200,json:async()=>(dry?addDry:{success:true,added:3})};
    }
    return {ok:true,status:200,json:async()=>({project:{id:'P1'},lines:[]})};
  };
  w.eval("PROJ={id:'P1'};");

  let shown='';
  w.confirm=(m)=>{ shown=m; return false; };
  await w.checkNewLines(new w.File(['x'],'new.xlsx'));
  await sleep(60);
  ok('a dry run runs first', sentAdd[0]==='dry');
  ok('the names to be added are listed', /Side Table/.test(shown) && /Runner/.test(shown));
  ok('it promises existing lines are untouched', /received counts stay/.test(shown));
  ok('declining adds nothing', !sentAdd.includes('write'));

  // a likely rename must be called out
  sentAdd=[]; shown='';
  addDry={...addDry, possible_renames:[{sku:'AB1',new_name:'Grey Sofa',existing_names:['sofa']}]};
  await w.checkNewLines(new w.File(['x'],'new.xlsx'));
  await sleep(60);
  ok('a possible rename is flagged before writing', /may be renames/.test(shown));
  ok('naming both versions', /Grey Sofa/.test(shown) && /sofa/.test(shown));

  // accepting writes
  sentAdd=[]; w.confirm=()=>true;
  await w.checkNewLines(new w.File(['x'],'new.xlsx'));
  await sleep(60);
  ok('accepting adds them', sentAdd.includes('write'));
  ok('and reports the count', /Added 3 lines/.test(d.getElementById('photo-msg').textContent));

  // nothing new
  sentAdd=[]; addDry={success:true,dry_run:true,to_add:0,already_here:95,names:[]};
  await w.checkNewLines(new w.File(['x'],'new.xlsx'));
  await sleep(60);
  ok('nothing new writes nothing', !sentAdd.includes('write'));
  ok('and says the file is already here', /Nothing new/.test(d.getElementById('photo-msg').textContent));

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail?1:0);
})().catch(e=>{console.log('HARNESS ERROR: '+e.message);process.exit(2);});
