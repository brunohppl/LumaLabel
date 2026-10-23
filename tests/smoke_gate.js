// The sign-in page itself.
const fs=require('fs');
let pass=0,fail=0;
const ok=(l,c)=>{ c?(pass++,console.log('✓ '+l)):(fail++,console.log('✗ FAIL '+l)); };
const h=fs.readFileSync('/mnt/user-data/outputs/signin.html','utf8');

ok('it posts to the sign-in route', /action="\/signin"/.test(h) && /method="POST"/i.test(h));
ok('the password field is masked', /type="password"/.test(h));
ok('a numeric keypad opens on a phone', /inputmode="numeric"/.test(h));
ok('it focuses the field on load', /autofocus/.test(h));
ok('where you were going is carried through', /name="next"/.test(h) && /\{\{NEXT\}\}/.test(h));
ok('errors have somewhere to appear', /\{\{ERROR\}\}/.test(h));
ok('the password is NOT in the page', !/2118/.test(h));
ok('no javascript needed to sign in', !/<script/.test(h));

const app=fs.readFileSync('/mnt/user-data/outputs/app.py','utf8');
ok('the password comes from the environment', /os\.environ\.get\('APP_PASSWORD'/.test(app));
ok('and is not written in the code', !/APP_PASSWORD[^)]*2118/.test(app));
ok('sessions last 30 days', /permanent_session_lifetime\s*=\s*timedelta\(days=30\)/.test(app));
ok('everything is protected except a named few',
   /OPEN_PATHS = /.test(app) && /return not any\(path == p/.test(app));
ok('the browser can still fetch its own files', /'\/static'/.test(app) && /'\/sw\.js'/.test(app));
ok('and the health check', /'\/health'/.test(app));
ok('wrong attempts are limited', /SIGNIN_MAX_TRIES/.test(app) && /SIGNIN_LOCK_MIN/.test(app));
ok('and compared in constant time', /compare_digest/.test(app));
ok('with no password set, nothing is gated', /if not APP_PASSWORD:\s*\n\s*return False/.test(app));

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail?1:0);
