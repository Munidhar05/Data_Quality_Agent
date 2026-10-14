"""Check phone/tablet layouts in real Chrome iframe viewports."""
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

ROOT = Path(__file__).resolve().parents[1]
CHECKS = r"""
const assert=(ok,msg)=>{if(!ok)throw Error(msg)};
const fits=(label)=>assert(document.documentElement.scrollWidth<=innerWidth+1,label+' overflows '+innerWidth+'px');
const touch=(selector)=>document.querySelectorAll(selector).forEach(el=>assert(el.getBoundingClientRect().height>=(innerHeight>=500?44:24),selector+' too small'));
document.body.classList.remove('auth-pending');
document.querySelector('#account-name').textContent='Very long account display name for mobile';
renderOverview(); fits('Upload');
const toggle=document.querySelector('#navigation-toggle'),menu=document.querySelector('#site-navigation');
assert(getComputedStyle(menu).display==='none','Menu starts collapsed');
assert(menu.contains(document.querySelector('#account-name'))&&menu.contains(document.querySelector('#logout-button')),'Account controls inside menu');
assert(document.querySelector('#logout-button').getBoundingClientRect().height===0,'Account controls hidden with menu');
const contentTop=document.querySelector('.workspace').getBoundingClientRect().top;
touch('#navigation-toggle');toggle.click();
assert(toggle.getAttribute('aria-expanded')==='true'&&getComputedStyle(menu).display==='flex','Hamburger opens menu');
fits('Open navigation');touch('.rail-link');touch('#logout-button');
assert(document.querySelector('.workspace').getBoundingClientRect().top===contentTop,'Menu does not push content down');
assert(menu.getBoundingClientRect().width<=270&&menu.getBoundingClientRect().width<innerWidth,'Compact menu width');
assert(Math.abs(menu.getBoundingClientRect().height-innerHeight)<=1&&menu.getBoundingClientRect().top===0,'Drawer uses full viewport height');
assert(Math.abs(menu.getBoundingClientRect().right-innerWidth)<=1,'Drawer aligned to right edge');
assert(getComputedStyle(menu).borderRadius==='0px','Drawer has no popup corners');
assert(document.querySelector('#site-navigation .rail-bottom').getBoundingClientRect().bottom>=innerHeight-20,'Account controls at drawer bottom');
assert(getComputedStyle(menu).overflowY==='hidden','Drawer does not scroll');
assert(menu.scrollHeight<=menu.clientHeight+1,'All drawer content fits without scrolling');
const links=[...menu.querySelectorAll('.rail-link')];
links.slice(1).forEach((link,i)=>assert(link.getBoundingClientRect().top>=links[i].getBoundingClientRect().bottom,'Sections stack vertically'));
assert(document.querySelector('#account-name').getBoundingClientRect().height>0,'Username visible in open menu');
document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
assert(toggle.getAttribute('aria-expanded')==='false'&&document.activeElement===toggle,'Escape closes menu and returns focus');
toggle.click();document.querySelector('#site-navigation [data-page="overview"]').click();
assert(toggle.getAttribute('aria-expanded')==='false','Page selection closes menu');
toggle.click();document.querySelector('.workspace').click();
assert(toggle.getAttribute('aria-expanded')==='false','Outside click closes menu');
assert(getComputedStyle(document.querySelector('.rail')).position==='relative','Mobile navigation');
assert(getComputedStyle(document.querySelector('.step-navigation')).position==='fixed','Mobile action bar matches desktop');
const checkActionBar=()=>{
 const bar=document.querySelector('.page.active .step-navigation');if(!bar)return;
 const rect=bar.getBoundingClientRect();
 assert(Math.abs(rect.bottom-innerHeight)<=1&&rect.left===0,'Action bar anchored to viewport bottom: '+JSON.stringify({page:document.querySelector('.page.active').id,left:rect.left,bottom:rect.bottom,height:innerHeight}));
 assert(getComputedStyle(bar).borderRadius==='0px','Action bar has desktop shape');
 assert(parseFloat(getComputedStyle(document.querySelector('.page.active')).paddingBottom)>=rect.height,'Content clears action bar');
 const buttons=[...bar.querySelectorAll('.button')];
 buttons.slice(1).forEach((button,i)=>assert(Math.abs(button.getBoundingClientRect().top-buttons[i].getBoundingClientRect().top)<=1,'Next and Previous side by side'));
};
showPage('review'); const add=document.querySelector('.add-optional-check:not(:disabled)');if(add)add.click();fits('Checks');checkActionBar();
showPage('business'); fits('Business rules');checkActionBar();
showPage('profile'); fits('File details');
showPage('rules'); fits('Catalog');
showPage('history'); await renderHistory(); fits('History');
const run={dataset_id:'sample',dataset_name:'Very_long_uploaded_file_name_'.repeat(5)+'.csv',run_id:'run',validated_at:'2026-10-09T00:00:00Z',row_count:2,accepted_count:2,rejected_count:0,score:{value:100},dataset_profile:state.profile,rules_selected:[],check_results:[],failures:[],failure_count:0,warnings:[]};
renderResults(run); showPage('results'); fits('Report');checkActionBar();
document.body.classList.add('auth-pending'); fits('Sign in');
const auth=document.querySelector('.auth-page');auth.classList.add('signup-mode');
document.querySelector('#signup-fields').hidden=false;fits('Sign up');
if(innerWidth<=600)assert(getComputedStyle(document.querySelector('.auth-fields')).gridTemplateColumns.split(' ').length===1,'Signup columns');
"""
ADMIN_CHECKS = r"""
const assert=(ok,msg)=>{if(!ok)throw Error(msg)};
const fits=(label)=>assert(document.documentElement.scrollWidth<=innerWidth+1,label+' overflows '+innerWidth+'px');
fits('Admin login');document.querySelector('#admin-login').hidden=true;
document.querySelector('#admin-workspace').hidden=false;fits('Admin workspace');
document.querySelector('#users-body').innerHTML='<tr><td>'+('long-email-address@company.com '.repeat(10))+'</td><td>Active</td></tr>';fits('Admin table');
document.querySelector('#user-dialog').showModal();fits('Admin dialog');
if(innerWidth<=480)assert(getComputedStyle(document.querySelector('.form-grid')).gridTemplateColumns.split(' ').length===1,'Admin form columns');
"""

def main():
    spec=importlib.util.spec_from_file_location('frontend_smoke',ROOT/'tests/frontend_smoke.py')
    smoke=importlib.util.module_from_spec(spec);spec.loader.exec_module(smoke)
    fixture=smoke.TESTS.split('  renderRegistry();')[0].split("document.body.classList.remove('auth-pending');")[1]
    pages={}
    for kind in ['workspace','admin']:
        html=(ROOT/('frontend/index.html' if kind=='workspace' else 'frontend/admin.html')).read_text(encoding='utf-8')
        css=(ROOT/('frontend/styles.css' if kind=='workspace' else 'frontend/admin.css')).read_text(encoding='utf-8')
        html=re.sub(r'<link rel="stylesheet"[^>]*>',lambda _: '<style>'+css+'</style>',html)
        html=re.sub(r'<script[^>]*>.*?</script>','',html,flags=re.S)
        code=''
        if kind=='workspace':
            code=(ROOT/'frontend/check-catalog.js').read_text(encoding='utf-8')
            code+='\n'+(ROOT/'frontend/app.js').read_text(encoding='utf-8').replace('\ninitialize();','\n')
            import sys
            sys.path.insert(0,str(ROOT))
            from backend.rule_plugins.catalog_validation import RULE_PLUGINS
            catalog=json.dumps([plugin.get_metadata() for plugin in RULE_PLUGINS])
            code+='\nconst supportedCatalog='+catalog+';window.fetch=async(path)=>({ok:true,json:async()=>path==="/api/runs"?[{run_id:"mobile-run",dataset_name:"Long_filename_".repeat(20),validated_at:"2026-10-09T00:00:00Z",accepted_count:2,rejected_count:0}]:({columns:[],rows:[],total_count:0})});'+fixture
        checks=CHECKS if kind=='workspace' else ADMIN_CHECKS
        code+='\n(async()=>{try{'+checks+"parent.postMessage({ok:true,kind:'"+kind+"',width:innerWidth},'*')}catch(e){parent.postMessage({ok:false,error:e.message,kind:'"+kind+"',width:innerWidth},'*')}})();"
        pages['/'+kind]=html.replace('</body>','<script>'+code+'</script></body>')
    parent="""<html><body><pre id="result">RUNNING</pre><script>
let count=0, failures=[];
window.addEventListener('message',e=>{count++;if(!e.data.ok)failures.push(JSON.stringify(e.data));if(count===12)document.querySelector('#result').textContent=failures.length?'FAIL: '+failures.join('; '):'PASS: mobile/tablet layouts and non-scrolling drawer at 600px and 400px heights';});
for(const kind of ['workspace','admin'])for(const width of [320,390,600,768,900]){const frame=document.createElement('iframe');frame.style.cssText='display:block;border:0;height:900px;width:'+width+'px';frame.src='/'+kind;document.body.append(frame);}
for(const [width,height] of [[390,600],[900,400]]){const frame=document.createElement('iframe');frame.style.cssText='display:block;border:0;height:'+height+'px;width:'+width+'px';frame.src='/workspace';document.body.append(frame);}
</script></body></html>"""
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.end_headers();self.wfile.write((pages.get(self.path,parent)).encode('utf-8'))
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    Thread(target=server.serve_forever,daemon=True).start()
    chrome=Path(os.environ.get('PROGRAMFILES','C:/Program Files'))/'Google/Chrome/Application/chrome.exe'
    try:
        with tempfile.TemporaryDirectory(prefix='dq-mobile-') as tmp:
            result=subprocess.run([str(chrome),'--headless','--disable-gpu','--no-first-run','--no-default-browser-check','--user-data-dir='+tmp,'--virtual-time-budget=10000','--dump-dom',f'http://127.0.0.1:{server.server_port}/'],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=45)
        match=re.search(r'<pre id="result">(.*?)</pre>',result.stdout,re.S)
        message=match.group(1) if match else 'FAIL: Chrome did not return results'
        print(message)
        if not message.startswith('PASS:'):raise SystemExit(1)
    finally:server.shutdown();server.server_close()

if __name__=='__main__':main()
