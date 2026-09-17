// Stylist renaming an item group, matching how rooms are renamed.
const fs=require('fs'), {JSDOM}=require('jsdom');
const html=fs.readFileSync('/mnt/user-data/outputs/stylist.html','utf8');
let pass=0,fail=0;
const ok=(l,c)=>{ c?(pass++,console.log('✓ '+l)):(fail++,console.log('✗ FAIL '+l)); };
let patched=[], failNext=false;

const dom=new JSDOM(html,{runScripts:'dangerously',pretendToBeVisual:true,url:'https://x.test/stylist/J1',
  beforeParse(w){
    w.fetch=async(url,opt={})=>{
      if(opt.method==='PATCH'){
        patched.push({url,body:JSON.parse(opt.body||'{}')});
        return failNext?{ok:false,status:400,json:async()=>({error:'nope'})}
                       :{ok:true,status:200,json:async()=>({success:true})};
      }
      return {ok:true,status:200,json:async()=>({job:{},items:[]})};
    };
    w.alert=()=>{};
  }});
const w=dom.window,d=w.document;
const sleep=ms=>new Promise(r=>setTimeout(r,ms));

(async()=>{
  await sleep(250);
  w.eval(`
    allItems=[{id:'i1',serial:'001',room:'Living',description:'Dining Chair',picked:false},
              {id:'i2',serial:'002',room:'Living',description:'Dining Chair',picked:false},
              {id:'i3',serial:'003',room:'Living',description:'Sofa',picked:false}];
    currentJob={id:'J1'};
    renderItems=()=>{};
  `);

  ok('rename function exists', typeof w.startRenameItem==='function');

  // a group of two shares one description
  patched=[];
  await w.saveRenameItem('Dining Chair','Oak Dining Chair',['i1','i2'],
                         {innerHTML:''}, {});
  await sleep(60);
  ok('renames every item in the group', patched.length===2);
  ok('sends the new description', patched.every(p=>p.body.description==='Oak Dining Chair'));
  ok('local copies updated',
     w.eval("allItems.filter(i=>i.description==='Oak Dining Chair').length")===2);
  ok('other items untouched', w.eval("allItems.find(i=>i.id==='i3').description")==='Sofa');

  // no change writes nothing
  patched=[];
  await w.saveRenameItem('Sofa','Sofa',['i3'],{innerHTML:''},{});
  await sleep(40);
  ok('an unchanged name writes nothing', patched.length===0);

  // empty is refused client-side too
  patched=[];
  await w.saveRenameItem('Sofa','',['i3'],{innerHTML:''},{});
  await sleep(40);
  ok('an empty name is refused', patched.length===0);
  ok('and the item keeps its name', w.eval("allItems.find(i=>i.id==='i3').description")==='Sofa');

  // a failed save must not leave a name that was never stored
  failNext=true; patched=[];
  await w.saveRenameItem('Sofa','Ghost Sofa',['i3'],{innerHTML:''},{});
  await sleep(60);
  ok('a failed save keeps the old name', w.eval("allItems.find(i=>i.id==='i3').description")==='Sofa');
  failNext=false;

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail?1:0);
})();
