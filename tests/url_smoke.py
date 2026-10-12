"""Fresh-browser URL checks with real frontend assets and deterministic API fixtures."""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parents[1]
PROFILE = {'row_count':2,'column_count':1,'missing_values':{'count':0,'percentage':0},'duplicate_row_count':0,'memory_bytes':100,'columns':[{'name':'id','physical_type':'int64','pyspark_type':'LongType','null_count':0,'null_percentage':0,'distinct_count':2,'uniqueness_percentage':100,'statistics':{},'semantic_candidates':[],'top_values':[]}]}
PAYLOAD = {'dataset':{'dataset_id':'sample','name':'sample'},'profile':PROFILE,'recommendations':[{'rule_id':'dup','name':'Duplicate rows','predicate':'unique_rows','applicability':'OPTIONAL','confidence':1,'evidence':[],'description':'Duplicates'}]}
RUN = {'run_id':'sample-run','dataset_id':'sample','dataset_name':'sample','row_count':2,'accepted_count':2,'rejected_count':0,'score':{'value':100},'dataset_profile':PROFILE,'rules_selected':[],'failures':[],'failure_count':0,'warnings':[],'validated_at':'2026-10-09T00:00:00Z'}
CASES = [('/', 'overview'),('/login','login'),('/signup','signup'),('/upload-file','overview'),('/choose-checks?dataset=sample','review'),('/file-details?dataset=sample','profile'),('/results?run=sample-run','results'),('/history','history'),('/available-checks','rules'),('/admin','admin'),('/choose-checks','overview'),('/file-details','overview'),('/results','overview'),('/file-details?dataset=missing','overview'),('/results?run=missing','overview'),('/history/','history'),('/results?run=sample-run&signedout=1','login-return'),('/signup?next=https%3A%2F%2Fevil.example','signup'),('/history?expired=1','login'),('/login?session=active','login'),('/signup?session=active','signup'),('/upload-file?dataset=sample','overview'),('/admin?session=active','admin-active')]

MOCK = r"""
let loggedIn = CASE.sessionActive || (!CASE.signedout && !['/login','/signup'].includes(location.pathname));
window.fetch = async (path, options={}) => {
  path=String(path); let status=200, data={};
  if(path==='/api/auth/countries') data=COUNTRIES;
  else if(path==='/api/auth/me') {status=loggedIn?200:401;data=loggedIn?{user:{first_name:'Test',last_name:'User'}}:{detail:'Please log in to continue.'};}
  else if(path==='/api/auth/login') {loggedIn=true;data={user:{first_name:'Test',last_name:'User'}};}
  else if(path==='/api/health') data={engine_version:'test'};
  else if(!loggedIn || CASE.expired) {status=401;data={detail:'Please log in to continue.'};}
  else if(path==='/api/rules') data=[];
  else if(path.includes('/missing/')) {status=404;data={detail:'Not found'};}
  else if(path.endsWith('/profile')) data=PAYLOAD;
  else if(path.includes('/rows?')) data={columns:['id'],rows:[{id:1},{id:2}],total_count:2};
  else if(path==='/api/runs') data=[RUN];
  else if(path.startsWith('/api/runs/')) data=RUN;
  else if(path==='/api/admin/me') {status=CASE.sessionActive?200:401;data=CASE.sessionActive?{}:{detail:'Sign in as administrator'};}
  else if(path==='/api/admin/users' || path==='/api/admin/logs') data=[];
  return {ok:status===200,status,json:async()=>data};
};
"""
TEST = r"""
window.addEventListener('DOMContentLoaded', async()=>{
  const assert=(value,msg)=>{if(!value)throw Error(msg);};
  const wait=async(condition)=>{for(let i=0;i<150;i++){if(condition())return;await new Promise(r=>setTimeout(r,10));}throw Error('Timed out waiting for page');};
  try {
    if(CASE.expected==='admin-active') {await wait(()=>document.querySelector('#admin-workspace') && !document.querySelector('#admin-workspace').hidden);assert(location.pathname==='/admin','Authenticated admin duplicate keeps URL');}
    else if(CASE.expected==='admin') {await wait(()=>document.querySelector('#admin-login'));assert(!document.querySelector('#admin-login').hidden,'Admin login visible');}
    else if(['login','signup','login-return'].includes(CASE.expected)) {
      await wait(()=>location.pathname===(CASE.expected==='signup'?'/signup':'/login'));
      assert(document.body.classList.contains('auth-pending'),'Authentication screen visible');
      assert(document.querySelector('#auth-page').classList.contains('signup-mode')===(CASE.expected==='signup'),'Correct auth mode');
      if(CASE.expected==='login-return') {
        assert(new URLSearchParams(location.search).get('next').startsWith('/results?run=sample-run'),'Return URL retained');
        await wait(()=>document.querySelector('#country-code').options.length>200);
        const form=document.querySelector('#auth-form');form.elements.email.value='test@example.com';form.elements.password.value='test-password';
        await form.onsubmit({preventDefault(){}});
        await wait(()=>location.pathname==='/results'&&!document.body.classList.contains('auth-pending'));
        assert(new URLSearchParams(location.search).get('run')==='sample-run','Requested result restored after sign-in');
        await wait(()=>document.querySelector('#dq-summary-report'));
      }
      if(CASE.expected==='signup') {
        document.querySelector('#switch-auth').click();assert(location.pathname==='/login','Signup to login URL');
        document.querySelector('#switch-auth').click();assert(location.pathname==='/signup','Login to signup URL');
      }
    } else {
      await wait(()=>!document.body.classList.contains('auth-pending') && document.querySelector('.page.active')?.dataset.pageContent===CASE.expected && location.pathname!== '/');
      assert(location.pathname===pagePaths[CASE.expected],'Canonical page URL');
      if(CASE.expected==='review'||CASE.expected==='profile') {assert(state.dataset?.dataset_id==='sample','Dataset loaded on fresh browser');assert(state.profile.row_count===2,'Profile restored');}
      if(CASE.expected==='results') {await wait(()=>document.querySelector('#dq-summary-report'));assert(state.lastRun?.run_id==='sample-run','Result loaded on fresh browser');}
      if(CASE.expected==='history') await wait(()=>document.querySelector('.history-open'));
    }
    document.body.innerHTML='<pre id="url-result">PASS</pre>';
  }catch(error){document.body.innerHTML='<pre id="url-result"></pre>';document.querySelector('pre').textContent='FAIL: '+error.stack;}
});
"""

def main():
    chrome=Path(os.environ.get('PROGRAMFILES','C:/Program Files'))/'Google/Chrome/Application/chrome.exe'
    case_by_url=dict(CASES)
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed=urlparse(self.path)
            if parsed.path.startswith('/static/'):
                asset=ROOT/'frontend'/Path(parsed.path).name
                if not asset.exists():self.send_error(404);return
                data=asset.read_bytes(); content_type='text/javascript' if asset.suffix=='.js' else 'text/css' if asset.suffix=='.css' else 'image/svg+xml'
            else:
                expected=case_by_url.get(self.path,'overview')
                case={'expected':expected,'signedout': 'signedout' in parse_qs(parsed.query),'expired':'expired' in parse_qs(parsed.query),'sessionActive':parse_qs(parsed.query).get('session')==['active']}
                html=(ROOT/'frontend'/('admin.html' if parsed.path=='/admin' else 'index.html')).read_text(encoding='utf-8')
                setup='const CASE='+json.dumps(case)+';const PAYLOAD='+json.dumps(PAYLOAD)+';const RUN='+json.dumps(RUN)+';const COUNTRIES='+(ROOT/'frontend/country-codes.json').read_text(encoding='utf-8')+';'
                html=html.replace('<head>','<head><script>'+setup+MOCK+TEST+'</script>',1)
                data=html.encode();content_type='text/html; charset=utf-8'
            self.send_response(200);self.send_header('Content-Type',content_type);self.end_headers();self.wfile.write(data)
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);Thread(target=server.serve_forever,daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(prefix='dq-url-test-') as tmp:
            for path,expected in CASES:
                result=subprocess.run([str(chrome),'--headless','--disable-gpu','--no-first-run','--no-default-browser-check','--user-data-dir='+str(Path(tmp)/'profile'),'--virtual-time-budget=5000','--dump-dom',f'http://127.0.0.1:{server.server_port}'+path],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=30)
                match=re.search(r'<pre id="url-result">(.*?)</pre>',result.stdout,re.S)
                output=match.group(1) if match else 'FAIL: browser did not complete'
                print(path+': '+output,flush=True)
                if output!='PASS':raise SystemExit(1)
        print(f'{len(CASES)} fresh-browser URL scenarios passed.',flush=True)
    finally:server.shutdown();server.server_close()

if __name__=='__main__':main()
