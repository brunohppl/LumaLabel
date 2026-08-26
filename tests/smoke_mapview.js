// Map tab with the real mapview.js. Leaflet is stubbed: the point is to
// check what we ask it to draw, not to test Leaflet itself.
const fs=require('fs'), {JSDOM}=require('jsdom');
const html=fs.readFileSync('/mnt/user-data/outputs/today.html','utf8');
const mvjs=fs.readFileSync('/mnt/user-data/outputs/static/mapview.js','utf8');
let pass=0,fail=0;
const ok=(l,c)=>{ c?(pass++,console.log('✓ '+l)):(fail++,console.log('✗ FAIL '+l)); };

const MAPDATA={
  points:[
    {job_id:'J1',ref:'QU-1351',address:'66 Hope St, South Brisbane',type:'install',time:'09:00',crew:'Nemo',crew_function:'transport',lat:-27.48,lng:153.01},
    {job_id:'J2',ref:'QU-1330',address:'5 Kent Rd, Wooloowin',type:'pickup',time:'13:00',crew:'Marlin',crew_function:'styling',lat:-27.42,lng:153.04},
    {job_id:'J3',ref:'QU-1399',address:'9 Vine St',type:'install',time:'07:30',crew:'Nigel',lat:-27.55,lng:152.93}],
  warehouse:{address:'63 Westgate St, Wacol QLD',lat:-27.58,lng:152.93},
  unmapped:1, geocoding_available:true
};
let mapData=MAPDATA;

const dom=new JSDOM(html,{runScripts:'dangerously',pretendToBeVisual:true,url:'https://x.test/today',
  beforeParse(w){
    w.fetch=async(u)=>{
      if(String(u).includes('/api/map/')) return {ok:true,status:200,json:async()=>mapData};
      return {ok:true,status:200,json:async()=>({teams:[],schedule:[],tasks:[],jobs:[]})};
    };
  }});
const w=dom.window,d=w.document;
const sleep=ms=>new Promise(r=>setTimeout(r,ms));

// Stub Leaflet, recording what gets drawn
const drawn={markers:[],view:null,bounds:null,tiles:0};
w.L={
  map:()=>({ setView:(c,z)=>{drawn.view={c,z};}, fitBounds:(b)=>{drawn.bounds=b;},
             removeLayer:()=>{}, invalidateSize:()=>{} }),
  tileLayer:()=>({ addTo:()=>{ drawn.tiles++; } }),
  layerGroup:()=>({ addTo:()=>({}) }),
  divIcon:(o)=>o,
  marker:(pos,opt)=>({ bindPopup:function(html){ drawn.markers.push({pos,html,icon:opt&&opt.icon}); return this; },
                       addTo:function(){ return this; } })
};

(async()=>{
  await sleep(250);
  const s=d.createElement('script'); s.textContent=mvjs; d.body.appendChild(s);
  await sleep(40);
  ok('mapview.js registers itself', typeof w.MapView==='object');

  w.showTab('map');
  await sleep(150);

  ok('a tile layer is added', drawn.tiles>0);
  ok('every stop is plotted plus the warehouse ('+drawn.markers.length+')', drawn.markers.length===4);
  ok('the warehouse is marked', drawn.markers.some(m=>/Warehouse/.test(m.html)));
  ok('warehouse address shown', drawn.markers.some(m=>/63 Westgate St/.test(m.html)));
  ok('installs plotted', drawn.markers.some(m=>/QU-1351/.test(m.html)));
  ok('pickups plotted', drawn.markers.some(m=>/QU-1330/.test(m.html)));
  // Icons must be readable without tapping
  const icons=drawn.markers.map(m=>(m.icon&&m.icon.html)||'').join('');
  ok('crew names labelled on the pins', /Nemo/.test(icons)&&/Marlin/.test(icons));
  ok('installs marked I', />I</.test(icons));
  ok('pickups marked P', />P</.test(icons));
  ok('warehouse uses a house icon, not a job pin', /Warehouse<\/div>/.test(icons));
  ok('popups carry crew and time', drawn.markers.some(m=>/Nemo/.test(m.html)&&/09:00/.test(m.html)));
  ok('the view fits all the points', Array.isArray(drawn.bounds)&&drawn.bounds.length===4);
  ok('unmapped jobs are reported', /without a location/.test(d.getElementById('mv-note').textContent));

  // ── Navigate from a pin ──
  ok('every pin offers Navigate', drawn.markers.filter(m=>/mv-nav/.test(m.html)).length===4);
  let navCall=null;
  w.navigate=(jobId,addr,fn)=>{ navCall={jobId,addr,fn}; };
  w.MapView.nav(0);
  ok('uses the card view navigate', !!navCall);
  ok('passes the job', navCall && navCall.jobId==='J1');
  ok('passes the address', navCall && /Hope St/.test(navCall.addr));
  ok('passes the crew function so the ETA is filed right',
     navCall && navCall.fn==='transport');
  w.MapView.nav(1);
  ok('a styling crew is filed as styling', navCall.fn==='styling');

  // The warehouse has no job, so it opens plain directions
  let opened=null;
  w.open=(url)=>{ opened=url; return null; };
  w.MapView.navTo('63 Westgate St, Wacol QLD');
  ok('warehouse opens Maps directly', opened && /Westgate/.test(decodeURIComponent(opened)));

  // If the host page changed and navigate() vanished, still give directions.
  // (delete does not work on a global function declaration — assign instead.)
  w.navigate=undefined; opened=null;
  w.MapView.nav(0);
  ok('falls back to Maps when navigate is gone', opened && /Hope St/.test(decodeURIComponent(opened)));

  // A bad index must not throw
  let threw=false;
  try{ w.MapView.nav(99); }catch(e){ threw=true; }
  ok('an unknown pin index is harmless', !threw);

  // A day with nothing on it must still show a map, not a blank tab
  drawn.markers.length=0; drawn.bounds=null; drawn.view=null;
  mapData={points:[],warehouse:null,unmapped:0,geocoding_available:true};
  w.MapView.render('2026-08-26', new Date());
  await sleep(120);
  ok('empty day still centres somewhere sensible', !!drawn.view);

  // Key missing on the server
  mapData={points:[],warehouse:null,unmapped:0,geocoding_available:false};
  w.MapView.render('2026-08-26', new Date());
  await sleep(120);
  ok('missing key is explained', /Google Maps key/.test(d.getElementById('mapview-root').innerHTML));

  // Endpoint failure must not break anything
  w.fetch=async()=>{ throw new Error('offline'); };
  w.MapView.render('2026-08-26', new Date());
  await sleep(120);
  ok('network failure handled', /Could not load/.test(d.getElementById('mapview-root').innerHTML));

  w.showTab('cards');
  await sleep(30);
  ok('cards unaffected throughout', d.querySelectorAll('.card, .team-col, .empty').length>0);

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail?1:0);
})();
