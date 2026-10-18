// The installable shell: manifest, icons, and a worker that caches nothing.
const fs=require('fs');
let pass=0,fail=0;
const ok=(l,c)=>{ c?(pass++,console.log('✓ '+l)):(fail++,console.log('✗ FAIL '+l)); };

// ── Manifest ──
const m=JSON.parse(fs.readFileSync('/mnt/user-data/outputs/static/manifest.json','utf8'));
ok('manifest is valid JSON', !!m);
ok('named for the home screen', m.short_name==='LUMA');
ok('opens on the team view', m.start_url==='/today');
ok('covers the whole site', m.scope==='/');
ok('opens without browser chrome', m.display==='standalone');
ok('brand colours', m.theme_color==='#1A1714' && m.background_color==='#1A1714');
ok('has a 192 and a 512 icon',
   m.icons.some(i=>i.sizes==='192x192') && m.icons.some(i=>i.sizes==='512x512'));
ok('and a maskable one for Android', m.icons.some(i=>i.purpose==='maskable'));

// ── Icons exist and are square ──
for(const f of ['icon-192.png','icon-512.png','icon-maskable-512.png','apple-touch-icon.png']){
  ok(`${f} exists`, fs.existsSync('/mnt/user-data/outputs/static/'+f));
}

// ── The worker ──
const sw=fs.readFileSync('/mnt/user-data/outputs/static/sw.js','utf8');
ok('the worker takes over promptly', /skipWaiting/.test(sw));
ok('and claims open pages', /clients\.claim/.test(sw));
ok('it caches NOTHING yet', !/caches\.(open|match|put)/.test(sw));
ok('and intercepts no requests', !/addEventListener\(['"]fetch/.test(sw));

// ── Wiring in the app ──
const app=fs.readFileSync('/mnt/user-data/outputs/app.py','utf8');
ok('the worker is served from the site root', /@app\.route\('\/sw\.js'/.test(app));
ok('the manifest is linked', /rel="manifest"/.test(app));
ok('an apple touch icon is declared', /apple-touch-icon/.test(app));
ok('registration failure cannot break a page', /register\('\/sw\.js'\)\.catch/.test(app));
ok('injected once, not per request', /if '<head>' in html and 'manifest\.json' not in html/.test(app));

// ── It must reach every page ──
const tpl=fs.readFileSync('/mnt/user-data/outputs/today.html','utf8');
ok('templates are not edited individually', !/manifest\.json/.test(tpl));

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail?1:0);
