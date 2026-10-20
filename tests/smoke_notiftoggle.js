// The home page notifications toggle.
const fs=require('fs'), {JSDOM}=require('jsdom');
let pass=0,fail=0;
const ok=(l,c)=>{ c?(pass++,console.log('✓ '+l)):(fail++,console.log('✗ FAIL '+l)); };
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const KEY='BBMUg6iz4-BVGfbQf_CyrQNwBcszeGx01k-dU-NFHKpb9x7r55TB4L6Qb67OlAFzAx8g27D7wzS28ZfRY7WrwQA';

function page({configured=true, permission='default', hasSub=false,
               pushSupported=true, calls=[]}={}){
  const html=fs.readFileSync('/mnt/user-data/outputs/home.html','utf8');
  let sub = hasSub ? {endpoint:'https://push/x', toJSON:()=>({endpoint:'https://push/x',keys:{p256dh:'k',auth:'a'}}),
                      unsubscribe:async()=>{calls.push('browser-unsub'); return true;}} : null;
  const dom=new JSDOM(html,{runScripts:'dangerously',pretendToBeVisual:true,url:'https://x/',
    beforeParse(w){
      w.fetch=async(url,opt={})=>{
        const u=String(url); calls.push(u.replace('https://x',''));
        if(u.includes('/api/push/key')) return {ok:true,json:async()=>({key:KEY,configured})};
        if(u.includes('/api/push/subscribe'))   return {ok:true,json:async()=>({success:true})};
        if(u.includes('/api/push/unsubscribe')) return {ok:true,json:async()=>({success:true})};
        return {ok:true,json:async()=>({})};
      };
      if(pushSupported){
        w.PushManager=function(){};
        w.Notification={permission, requestPermission:async()=>'granted'};
        const pm={ getSubscription:async()=>sub,
                   subscribe:async()=>{ sub={endpoint:'https://push/x',
                     toJSON:()=>({endpoint:'https://push/x',keys:{p256dh:'k',auth:'a'}}),
                     unsubscribe:async()=>{calls.push('browser-unsub'); return true;}};
                     return sub; } };
        w.navigator.serviceWorker={getRegistration:async()=>({pushManager:pm}),
                                   ready:Promise.resolve({pushManager:pm})};
      }else{
        delete w.PushManager;
      }
      w.atob=s=>Buffer.from(s,'base64').toString('binary');
    }});
  return dom;
}

(async()=>{
  // off by default
  let calls=[]; let dom=page({calls}); let d=dom.window.document;
  await sleep(300);
  const row=d.getElementById('notif-row'), tg=d.getElementById('notif-toggle');
  ok('the toggle is shown', row.style.display==='flex');
  ok('and reads as off', !tg.classList.contains('on'));
  ok('telling you what to do', /tap to turn on/i.test(d.getElementById('notif-sub').textContent));

  // turning on
  await dom.window.toggleNotifs(); await sleep(80);
  ok('tapping it subscribes', calls.some(c=>c.includes('/api/push/subscribe')));
  ok('and it reads as on', tg.classList.contains('on'));
  ok('saying so plainly', /On for this device/.test(d.getElementById('notif-sub').textContent));

  // turning off
  calls.length=0;
  await dom.window.toggleNotifs(); await sleep(80);
  ok('tapping again tells the server to forget it',
     calls.some(c=>c.includes('/api/push/unsubscribe')));
  ok('and unsubscribes the browser too', calls.includes('browser-unsub'));
  ok('the toggle reads as off', !tg.classList.contains('on'));

  // already subscribed on load
  dom=page({hasSub:true}); d=dom.window.document; await sleep(300);
  ok('an already-subscribed device shows on', d.getElementById('notif-toggle').classList.contains('on'));

  // blocked in settings
  dom=page({permission:'denied'}); d=dom.window.document; await sleep(300);
  ok('a blocked device explains itself',
     /Blocked in your phone settings/.test(d.getElementById('notif-sub').textContent));
  ok('and the toggle is not tappable', d.getElementById('notif-toggle').disabled===true);

  // server not configured — nothing to opt into
  dom=page({configured:false}); d=dom.window.document; await sleep(300);
  ok('no keys means the row stays hidden', d.getElementById('notif-row').style.display==='none');

  // a device that cannot do push at all
  dom=page({pushSupported:false}); d=dom.window.document; await sleep(300);
  ok('an unsupported device is not offered an inert toggle',
     d.getElementById('notif-row').style.display==='none');

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail?1:0);
})();
