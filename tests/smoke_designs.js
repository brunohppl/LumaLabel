// Catalogue: design groups — one row per piece we own, not per photo.
const fs=require('fs'), {JSDOM}=require('jsdom');
const html=fs.readFileSync('/mnt/user-data/outputs/catalogue.html','utf8');
let pass=0,fail=0;
const ok=(l,c)=>{ c?(pass++,console.log('✓ '+l)):(fail++,console.log('✗ FAIL '+l)); };

const GROUPS={success:true,total_entries:412,ungrouped_count:18,groups:[
  {id:'G1',name:'Tate Console Table Mango Wood',type:'Storage & Consoles',entry_count:9,job_count:7,
   photos:['https://x/1.png','https://x/2.png'],descriptions:['Tate Console Table Mango Wood','Tate Console - Black']},
  {id:'G2',name:'Hampton 3 Seater Sofa',type:'Sofas',entry_count:4,job_count:4,photos:[],descriptions:[]}]};
let calls=[], dry={success:true,dry_run:true,total_entries:412,already_grouped:0,
                  would_create:37,would_assign:394,unmatched:18,
                  preview:[{name:'Tate Console Table',count:9,type:'Storage & Consoles'}]};

const dom=new JSDOM(html,{runScripts:'dangerously',pretendToBeVisual:true,url:'https://x/catalogue',
  beforeParse(w){
    w.fetch=async(url,opt={})=>{
      const u=String(url); calls.push({u,method:opt.method||'GET',
        body:opt.body?JSON.parse(opt.body):null});
      if(u.includes('/design-groups/seed')) return {ok:true,json:async()=>
        (JSON.parse(opt.body).dry_run?dry:{success:true,created:37,assigned:394})};
      if(u.includes('/merge'))  return {ok:true,json:async()=>({success:true,moved:4})};
      if(u.match(/design-groups\/G\d$/)) return {ok:true,json:async()=>({success:true})};
      if(u.includes('/api/design-groups')) return {ok:true,json:async()=>GROUPS};
      return {ok:true,json:async()=>({entries:[],success:true})};
    };
    w.alert=()=>{};
  }});
const w=dom.window,d=w.document;
const sleep=ms=>new Promise(r=>setTimeout(r,ms));

(async()=>{
  await sleep(300);
  ok('a Designs tab exists', !!d.getElementById('vt-designs'));
  ok('photos remain the default view',
     d.getElementById('photos-view').style.display!=='none');

  w.setView('designs'); await sleep(80);
  ok('switching shows the designs', d.getElementById('designs-view').style.display!=='none');
  ok('and hides the photo grid', d.getElementById('photos-view').style.display==='none');

  const rows=[...d.querySelectorAll('.dg')];
  ok('one row per design', rows.length===2);
  ok('named', /Tate Console Table Mango Wood/.test(rows[0].textContent));
  ok('with its type', /Storage & Consoles/.test(rows[0].textContent));
  ok('how many photos and jobs', /9 photos · 7 jobs/.test(rows[0].textContent));
  ok('photos shown', rows[0].querySelectorAll('.dg-photo').length===2);
  ok('other names it goes by are listed', /Also listed as/.test(rows[0].textContent));
  ok('the scale is stated up top',
     /412 catalogue photos/.test(d.getElementById('dg-stat').textContent));
  ok('including what is not yet grouped',
     /18 not yet grouped/.test(d.getElementById('dg-stat').textContent));

  // seeding: dry run, confirm, write
  let shown=''; w.confirm=m=>{shown=m; return false;};
  calls=[];
  await w.seedGroups(); await sleep(60);
  ok('a dry run happens first', calls[0].body.dry_run===true);
  ok('it says how many designs and photos', /37 design\(s\) and file 394/.test(shown));
  ok('and what stays ungrouped', /18 photo\(s\) have no usable description/.test(shown));
  ok('promising nothing is deleted', /Nothing is deleted/.test(shown));
  ok('declining writes nothing', !calls.some(c=>c.body&&c.body.dry_run===false));

  calls=[]; w.confirm=()=>true;
  await w.seedGroups(); await sleep(80);
  ok('accepting runs it for real', calls.some(c=>c.body&&c.body.dry_run===false));

  // nothing to do
  dry={...dry, would_assign:0, already_grouped:412};
  calls=[];
  await w.seedGroups(); await sleep(60);
  ok('nothing to group says so, and writes nothing',
     /already in a design/.test(d.getElementById('dg-stat').textContent)
     && !calls.some(c=>c.body&&c.body.dry_run===false));

  // rename and merge
  calls=[]; w.prompt=()=>'New name';
  await w.renameGroup('G1'); await sleep(60);
  ok('rename sends the new name',
     calls.some(c=>c.method==='PATCH' && c.body.name==='New name'));
  calls=[]; w.prompt=()=>'1';
  await w.mergeGroup('G1'); await sleep(60);
  ok('merge names the group being folded in',
     calls.some(c=>c.u.includes('/merge') && c.body.from_id==='G2'));

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail?1:0);
})();
