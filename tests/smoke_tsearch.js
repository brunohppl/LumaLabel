// Stylist: find an item and mark it transferring, without opening rooms.
const fs=require('fs'), {JSDOM}=require('jsdom');
const html=fs.readFileSync('/mnt/user-data/outputs/stylist.html','utf8');
let pass=0,fail=0;
const ok=(l,c)=>{ c?(pass++,console.log('✓ '+l)):(fail++,console.log('✗ FAIL '+l)); };
let patched=[];

const dom=new JSDOM(html,{runScripts:'dangerously',pretendToBeVisual:true,url:'https://x/stylist/J1',
  beforeParse(w){
    w.fetch=async(url,opt={})=>{
      if(opt.method==='PATCH') patched.push({url:String(url),body:JSON.parse(opt.body||'{}')});
      return {ok:true,status:200,json:async()=>({job:{id:'J1'},items:[]})};
    };
    w.alert=()=>{};
  }});
const w=dom.window,d=w.document;
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const q=()=>d.getElementById('tsearch-q');
const rows=()=>[...d.querySelectorAll('.tsr')];

(async()=>{
  await sleep(300);
  ok('the search box exists', !!q());
  ok('it sits after the driver notes',
     d.body.innerHTML.indexOf('driver-notes-tablet') < d.body.innerHTML.indexOf('tsearch-q'));

  w.eval(`
    allItems=[{id:'i1',description:'Tate Console Table',room:'Living',serial:'001'},
              {id:'i2',description:'Tate Side Table',room:'Bed 1',serial:'002',is_transfer_item:true},
              {id:'i3',description:'Rug',room:'Living',serial:'003',not_transferring:true},
              {id:'i4',description:'Artwork',room:'Study',serial:'004',
               is_transfer_item:true,not_transferring:true}];
    renderItems=()=>{};
  `);

  q().value='t'; w.transferSearch();
  ok('one character shows nothing', rows().length===0);

  q().value='tate'; w.transferSearch();
  ok('searching finds matching items', rows().length===2);
  ok('the room is shown on each result', /Living/.test(rows()[0].textContent));
  ok('an already-transferring item shows ticked',
     rows()[1].querySelectorAll('input')[0].checked===true);
  ok('and is tinted', rows()[1].classList.contains('is-transfer'));

  // searching by room, not just name
  q().value='study'; w.transferSearch();
  ok('searching by room works', rows().length===1);
  ok('an item with both flags is called out', /both ticked/.test(rows()[0].textContent));

  // ticking from the search saves through the normal path
  q().value='tate'; w.transferSearch();
  patched=[];
  await w.searchToggle('i1','transfer',true);
  await sleep(60);
  ok('ticking transfer saves it',
     patched.some(p=>p.url.includes('/items/i1/check') && p.body.is_transfer_item===true));
  ok('the local item is updated', w.eval("allItems.find(i=>i.id==='i1').is_transfer_item")===true);
  ok('the row repaints as transferring',
     rows().find(r=>r.id==='tsr-i1').classList.contains('is-transfer'));

  patched=[];
  await w.searchToggle('i1','staying',true);
  await sleep(60);
  ok('ticking not-transferring saves it',
     patched.some(p=>p.body.not_transferring===true));
  ok('and the contradiction is flagged rather than hidden',
     /both ticked/.test(rows().find(r=>r.id==='tsr-i1').textContent));

  patched=[];
  await w.searchToggle('i1','transfer',false);
  await sleep(60);
  ok('unticking saves the change too',
     patched.some(p=>p.body.is_transfer_item===false));

  // nothing else is written
  ok('only the transfer flags are ever sent',
     patched.every(p=>Object.keys(p.body).every(k=>['is_transfer_item','not_transferring'].includes(k))));

  q().value=''; w.transferSearch();
  ok('clearing the box clears the results', rows().length===0);

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail?1:0);
})();
