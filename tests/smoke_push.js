// The notifications page: enabling, composing, sending.
const fs=require('fs'), {JSDOM}=require('jsdom');
let pass=0,fail=0;
const ok=(l,c)=>{ c?(pass++,console.log('✓ '+l)):(fail++,console.log('✗ FAIL '+l)); };
const sleep=ms=>new Promise(r=>setTimeout(r,ms));

const KEY='BBMUg6iz4-BVGfbQf_CyrQNwBcszeGx01k-dU-NFHKpb9x7r55TB4L6Qb67OlAFzAx8g27D7wzS28ZfRY7WrwQA';
let sent=null, configured=true, permission='default', hasSub=false;

function page(){
  const html=fs.readFileSync('/mnt/user-data/outputs/push.html','utf8');
  return new JSDOM(html,{runScripts:'dangerously',pretendToBeVisual:true,url:'https://x/push',
    beforeParse(w){
      w.fetch=async(url,opt={})=>{
        const u=String(url);
        if(u.includes('/api/push/key'))  return {ok:true,json:async()=>({key:KEY,configured})};
        if(u.includes('/api/push/send')){ sent=JSON.parse(opt.body);
          return {ok:true,json:async()=>({success:true,sent:3,failed:1,removed:1,errors:['500: boom']})}; }
        if(u.includes('/api/push/subscribe')) return {ok:true,json:async()=>({success:true})};
        if(u.includes('/api/push/subscriptions'))
          return {ok:true,json:async()=>({success:true,subscriptions:[
            {id:'1',label:'Bruno iPhone',created_at:'2026-10-18T00:00:00Z'},
            {id:'2',label:null,user_agent:'Mozilla/5.0 (Linux; Android 13)',created_at:'2026-10-17T00:00:00Z'}]})};
        return {ok:true,json:async()=>({})};
      };
      w.Notification={permission, requestPermission:async()=>'granted'};
      w.PushManager=function(){};
      const sub={toJSON:()=>({endpoint:'https://push/x',keys:{p256dh:'k',auth:'a'}})};
      w.navigator.serviceWorker={
        getRegistration:async()=>({pushManager:{getSubscription:async()=>(hasSub?sub:null)}}),
        ready:Promise.resolve({pushManager:{
          getSubscription:async()=>(hasSub?sub:null),
          subscribe:async()=>sub}}),
      };
      w.prompt=()=>'Bruno iPhone';
      w.atob=s=>Buffer.from(s,'base64').toString('binary');
    }});
}

(async()=>{
  let dom=page(); let d=dom.window.document;
  await sleep(350);
  ok('the page loads', !!d.getElementById('send-btn'));
  ok('it says this device is not subscribed yet', /Not subscribed/.test(d.getElementById('dev-state').textContent));
  ok('and lists who is', /Bruno iPhone/.test(d.getElementById('sub-list').textContent));
  ok('naming unlabelled devices by platform', /Android/.test(d.getElementById('sub-list').textContent));
  ok('with a count', d.getElementById('sub-count').textContent==='2');

  // enabling
  await dom.window.enable(); await sleep(80);
  ok('enabling subscribes the device', /is subscribed/.test(d.getElementById('dev-state').textContent));

  // sending needs a message
  sent=null;
  await dom.window.send(); await sleep(60);
  ok('an empty message is refused here too', sent===null);
  ok('and says why', /Write a message/.test(d.getElementById('send-state').textContent));

  // a real send
  d.getElementById('body').value='Test from the office';
  d.getElementById('title').value='LUMA';
  await dom.window.send(); await sleep(80);
  ok('the message is sent', sent && sent.body==='Test from the office');
  ok('with the title', sent.title==='LUMA');
  ok('and where it should open', sent.url==='/today');
  const res=d.getElementById('send-result').textContent;
  ok('the result says how many reached', /3 sent/.test(res));
  ok('how many failed', /1 failed/.test(res));
  ok('and that a dead device was dropped', /1 removed/.test(res));
  ok('errors are shown, not hidden', /500: boom/.test(res));

  // an already-subscribed device
  hasSub=true;
  dom=page(); d=dom.window.document; await sleep(350);
  ok('an already-subscribed device says so', /is subscribed/.test(d.getElementById('dev-state').textContent));

  // the server without keys
  configured=false; hasSub=false;
  dom=page(); d=dom.window.document; await sleep(350);
  ok('a server with no key explains itself',
     /no notification key/.test(d.getElementById('dev-state').textContent));
  ok('and says what to set', /VAPID/.test(d.getElementById('dev-hint').textContent));
  ok('with the button disabled', d.getElementById('enable-btn').disabled===true);

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail?1:0);
})();
