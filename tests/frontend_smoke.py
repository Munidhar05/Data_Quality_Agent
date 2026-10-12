"""Run frontend interaction smoke checks in an installed headless Chrome."""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

ROOT = Path(__file__).resolve().parents[1]
TESTS = r"""
(async()=>{
  const assert=(value,msg)=>{if(!value)throw Error(msg);};
  document.body.classList.remove('auth-pending');
  state.dataset={dataset_id:'sample',name:'sample'};
  state.profile={row_count:2,column_count:2,missing_values:{count:1,percentage:25},duplicate_row_count:0,memory_bytes:100,columns:[{name:'id',physical_type:'int64',null_count:0,null_percentage:0,distinct_count:2,uniqueness_percentage:100,statistics:{mean:1},semantic_candidates:[],top_values:[]},{name:'amount',physical_type:'float64',null_count:1,null_percentage:50,distinct_count:1,uniqueness_percentage:100,statistics:{mean:10},semantic_candidates:[],top_values:[]}]};
  state.rules=supportedCatalog;
  state.recommendations=supportedCatalog.map(rule=>({...rule,applicability:rule.catalog_number===5?'RECOMMENDED':'REQUIRES_USER_INPUT',confidence:1,evidence:[]}));
  renderRegistry();assert(document.querySelectorAll('.catalog-category').length===7,'Seven categories');
  renderOverview();
  assert(!document.querySelector('.readiness-panel'),'Large next-step card removed');
  document.querySelector('#page-overview .step-navigation [data-page="review"]').click();
  assert(byId('page-review').classList.contains('active'),'Upload Next opens checks');
  document.querySelector('[data-step-previous]').click();
  assert(byId('page-overview').classList.contains('active'),'Checks Previous opens upload');
  showPage('review');
  assert(document.querySelectorAll('.catalog-subcheck').length===19,'19 subchecks');
  assert(document.querySelectorAll('.catalog-subcheck:not(:disabled)').length===18,'18 addable checks plus automatic duplicates');
  assert(!document.querySelector('.mandatory-checks .delete-test'),'Mandatory duplicate stays locked');
  document.querySelector('[data-rule-id="DQ-CATALOG-014"].add-optional-check').click();
  document.querySelector('[data-step-business]').click();
  assert(byId('page-review').classList.contains('active'),'Incomplete check blocks Next');
  const countCard=document.querySelector('.rule-card[data-rule-id="DQ-CATALOG-014"]');
  assert(countCard.querySelector('.status-chip').textContent==='Needs settings','Missing expectation badge');
  const expected=countCard.querySelector('[data-param-key="expected"]');
  expected.value='0'; expected.dispatchEvent(new Event('input',{bubbles:true}));
  assert(countCard.querySelector('.status-chip').textContent==='Ready','Zero is a valid completed expectation');
  document.querySelector('[data-step-business]').click();
  assert(byId('page-business').classList.contains('active'),'Complete settings allow Next: '+byId('toast').textContent);
  showPage('review');
  assert(document.querySelector('.rule-card[data-rule-id="DQ-CATALOG-014"] .status-chip').textContent==='Ready','Ready badge survives restoring draft');
  document.querySelector('.rule-card[data-rule-id="DQ-CATALOG-014"] .delete-test').click();
  document.querySelector('[data-rule-id="DQ-CATALOG-003"].add-optional-check').click();
  assert(byId('selected-checks').firstElementChild.dataset.ruleId==='DQ-CATALOG-003','New check appears first');
  saveReviewDraft();renderReview();
  assert(byId('selected-checks').firstElementChild.dataset.ruleId==='DQ-CATALOG-003','New check order survives rerender');
  const originalColumns=state.profile.columns;
  state.profile.columns=[...originalColumns,...Array.from({length:28},(_,i)=>({...originalColumns[0],name:'extra_'+i}))];
  saveReviewDraft();renderReview();
  const picker=document.querySelector('.column-picker');
  assert(!picker.open,'Column list collapsed initially');
  assert(picker.querySelector('.column-selection-count').textContent==='1 of 30 selected','Compact 30-column count');
  picker.open=true;
  assert(getComputedStyle(picker.querySelector('.column-options')).maxHeight==='200px','Column list bounded');
  const search=picker.querySelector('.column-search');search.value='amount';search.dispatchEvent(new Event('input',{bubbles:true}));
  assert(picker.querySelectorAll('.column-option:not([hidden])').length===1,'Column search');
  picker.querySelector('[data-column-action="clear"]').click();assert(picker.querySelector('.column-selection-count').textContent==='0 of 30 selected','Clear all');
  picker.querySelector('[data-column-action="all"]').click();assert(picker.querySelector('.column-selection-count').textContent==='30 of 30 selected','Select all');
  picker.querySelectorAll('[data-param-column]').forEach(input=>{input.checked=input.dataset.paramColumn==='amount';});
  syncSuggestedParameters(picker.querySelector('[data-param-column]'));
  state.profile.columns=originalColumns;saveReviewDraft();renderReview();
  const edit=document.querySelector('[data-value-type="json"]');assert(edit.hidden,'JSON hidden from user');assert(JSON.parse(edit.value).columns[0]==='amount','Missing column suggested automatically');const choice=document.querySelector('[data-param-column="id"]');choice.checked=true;choice.dispatchEvent(new Event('change',{bubbles:true}));
  state.reviewDraft.acceptedName='custom';
  showPage('overview');showPage('review');
  assert(JSON.parse(document.querySelector('[data-value-type="json"]').value).columns.includes('amount')&&JSON.parse(document.querySelector('[data-value-type="json"]').value).columns.includes('id'),'Settings persist');
  assert(state.reviewDraft.acceptedName==='custom','Download name persists');
  document.querySelector('.delete-test').click();assert(!document.querySelector('.delete-test'),'Delete check');
  document.querySelector('[data-rule-id="DQ-CATALOG-010"].add-optional-check').click();
  window.fetch=async(path,options={})=>({ok:true,status:200,json:async()=>({dataset:{dataset_id:'reference',name:'reference'},profile:state.profile,recommendations:state.recommendations})});
  const input=document.querySelector('.reference-file-input');const files=new DataTransfer();files.items.add(new File(['id\n1'],'ref.csv',{type:'text/csv'}));input.files=files.files;
  await uploadReferenceFile(input);
  assert(JSON.parse(document.querySelector('[data-value-type="json"]').value).reference_dataset_id==='reference','Reference upload');
  document.querySelector('.delete-test').click();
  const run={dataset_name:'sample',run_id:'run',validated_at:'2026-10-09T00:00:00Z',row_count:2,accepted_count:2,rejected_count:0,score:{value:100},dataset_profile:state.profile,rules_selected:[supportedCatalog.find(r=>r.catalog_number===5)],failures:[],failure_count:0,warnings:[],check_results:[{rule_id:'DQ-CATALOG-005',status:'PASS'}]};
  let submitted;
  window.fetch=async(path,options={})=>{if(path==='/api/runs'){submitted=JSON.parse(options.body);return{ok:true,status:200,json:async()=>run};}return{ok:true,status:200,json:async()=>({columns:['id','amount'],rows:[{id:1,amount:'2026-10-09T00:00:00'}],total_count:1})};};
  await runValidation({currentTarget:byId('approve-run')});
  assert(submitted.selected_rules[0].rule_id==='DQ-CATALOG-005','Mandatory duplicate included');
  assert(byId('page-results').classList.contains('active'),'Results opens');
  assert(byId('back-to-review').closest('.step-navigation'),'Results Previous at bottom');
  byId('back-to-review').click();assert(byId('page-business').classList.contains('active'),'Results Previous opens business rules');
  assert(byId('business-form').closest('#page-business'),'Business form in separate page');
  byId('business-input').value='amount greater than 0';
  document.querySelector('#page-business [data-step-previous]').click();
  assert(byId('page-review').classList.contains('active'),'Business Previous opens checks');
  document.querySelector('[data-step-business]').click();
  assert(byId('page-business').classList.contains('active'),'Checks Next opens business rules');
  assert(byId('business-input').value==='amount greater than 0','Business draft persists');
  assert(byId('business-input').tagName==='TEXTAREA','Large multiline rules editor');
  assert(!byId('business-document').hasAttribute('accept'),'Any file can be selected');
  assert(parseInt(getComputedStyle(byId('business-input')).minHeight)>=280,'Spacious editor');
  let batch;
  window.fetch=async(path,options)=>{batch=JSON.parse(options.body);return{ok:true,status:200,json:async()=>({rules:[{status:'PARSED',type:'comparison',column:'amount',operator:'>',value:0,original_requirement:'amount greater than 0'}],unresolved:[],message:'Review rules'})};};
  byId('business-input').value='1. amount greater than 0\n2. id greater than 0';
  await parseBusinessRule({preventDefault(){}});
  assert(batch.text.includes('2. id'),'Multiple points sent in one request');
  assert(state.businessRules.length===1,'Generated business rules added');
  await parseBusinessRule({preventDefault(){}});
  assert(state.businessRules.length===1,'Repeated batch does not duplicate rules');
  window.fetch=async()=>({ok:true,status:200,json:async()=>({columns:['id','amount'],rows:[{id:1,amount:'2026-10-09T00:00:00'}],total_count:1})});

  showPage('results');
  assert(!byId('review-page-content').querySelector('#accepted-name'),'File downloads moved out of checks');
  assert(document.querySelectorAll('[data-report-format]').length===8,'Report format menu');
  assert(document.querySelectorAll('[data-download-records]').length===2,'Record downloads in Results');
  assert(document.querySelectorAll('.report-metric').length===6,'Six summary cards');
  assert(document.querySelectorAll('.report-donut').length===2,'Record and severity charts');
  assert(document.querySelectorAll('.dq-check-table th').length===10,'Detailed report columns');
  assert(document.querySelector('.report-badge.passed'),'Passing check status');
  const failedRun={...run,row_count:4,accepted_count:2,rejected_count:2,rules_selected:[{rule_id:'a',name:'Required ID',category:'completeness',severity:'critical'}],check_results:[{rule_id:'a',status:'FAIL',failure_count:3}],failures:[{rule_id:'a',row_index:0,severity:'critical'},{rule_id:'a',row_index:0,severity:'critical'},{rule_id:'a',row_index:1,severity:'critical'}],failure_count:3};
  renderResults(failedRun);
  let cells=document.querySelector('.dq-check-table tbody tr').cells;
  assert(cells[7].textContent==='2'&&cells[8].textContent==='50.0%','Distinct record counts');
  assert(document.querySelector('.report-badge.failed'),'Failed status');
  renderResults({...failedRun,failure_count:5001});
  assert(document.querySelector('.dq-check-table tbody tr').cells[7].textContent==='Unavailable','Truncated audit avoids false totals');
  renderResults({...run,row_count:0,accepted_count:0,rejected_count:0,rules_selected:[],check_results:[]});
  assert(!byId('dq-summary-report').innerHTML.includes('NaN'),'Empty run renders safely');
  renderResults(run);
  let imageDownload; const originalSaveDownload=saveDownload; saveDownload=(blob,name)=>{imageDownload={blob,name};};
  renderResults({...failedRun,rules_selected:Array.from({length:35},(_,i)=>({...failedRun.rules_selected[0],rule_id:'long-'+i,name:'Long check '+i,description:'Detailed description '.repeat(12),configuration:{original_requirement:'Complete condition '.repeat(15)}}))});
  await downloadQualityReport('png');
  assert(imageDownload?.blob.type==='image/png'&&imageDownload.blob.size>10000,'PNG report export contains image content');
  for(const [format,mime] of [['jpeg','image/jpeg'],['webp','image/webp']]) {
    await downloadQualityReport(format); assert(imageDownload.blob.type===mime&&imageDownload.blob.size>10000,format+' complete report image');
  }
  await downloadQualityReport('html'); assert((await imageDownload.blob.text()).includes('Long check 34'),'HTML includes last check');
  await downloadQualityReport('svg'); assert(imageDownload.name.endsWith('.svg'),'SVG report export');
  const exportedSvg=await imageDownload.blob.text();
  assert(exportedSvg.includes('Long check 34'),'Last check included in SVG');
  const svgDoc=new DOMParser().parseFromString(exportedSvg,'image/svg+xml');
  assert(!svgDoc.querySelector('parsererror'),'Valid SVG export');
  assert(Number(svgDoc.documentElement.getAttribute('height'))>2000,'Full long report height');
  await downloadQualityReport('csv'); const csv=await imageDownload.blob.text();
  assert(csv.includes('Total Records')&&csv.includes('Data Quality Issues by Severity')&&csv.includes('Long check 34'),'CSV contains summary, charts, and all check rows');
  renderResults(run);
  saveDownload=originalSaveDownload;
  renderProfile();await loadExplorer();
  assert(getComputedStyle(byId('explorer-table').querySelector('td')).whiteSpace==='nowrap','Single-line records');
  document.body.innerHTML='<pre id="smoke-result">PASS: 19-check catalog, add/delete, settings, reference upload, validation and report</pre>';
})().catch(error=>{document.body.innerHTML='<pre id="smoke-result"></pre>';document.querySelector('pre').textContent='FAIL: '+error.stack;});
"""


def main():
    chrome = Path(os.environ.get('PROGRAMFILES', 'C:/Program Files')) / 'Google/Chrome/Application/chrome.exe'
    if not chrome.exists():
        raise SystemExit('Chrome not installed')
    html = (ROOT / 'frontend/index.html').read_text(encoding='utf-8')
    js = (ROOT / 'frontend/app.js').read_text(encoding='utf-8').replace('\ninitialize();', '\n')
    html = re.sub(r'<link rel="stylesheet"[^>]*>', lambda _: '<style>'+(ROOT/'frontend/styles.css').read_text(encoding='utf-8')+'</style>',html)
    html = re.sub(r'<script src="/static/check-catalog.js[^"]*" defer></script>', lambda _: '<script>'+(ROOT/'frontend/check-catalog.js').read_text(encoding='utf-8')+'</script>', html)
    import sys
    sys.path.insert(0,str(ROOT))
    from backend.rule_plugins.catalog_validation import RULE_PLUGINS
    profile = {'columns':[{'name':'id','pyspark_type':'LongType','null_count':0,'semantic_candidates':[{'type':'IDENTIFIER','confidence':1}], 'statistics':{'mean':1}}, {'name':'amount','pyspark_type':'DoubleType','null_count':1,'semantic_candidates':[], 'statistics':{'mean':10}}]}
    catalog_rules = json.dumps([{**plugin.get_metadata(), 'configuration_defaults':plugin.generate_configuration(profile)} for plugin in RULE_PLUGINS])
    mock = 'window.onerror=(message,source,line)=>{document.body.insertAdjacentHTML("beforeend","<pre id=smoke-result>FAIL: "+message+" at "+line+"</pre>");};const supportedCatalog='+catalog_rules+';' + "window.fetch = async () => ({ok:true, json:async()=>({columns:['id','amount'], rows:[], total_count:0})});"
    html = re.sub(r'<script src="/static/app.js[^"]*" defer></script>', lambda _: '<script>'+mock+'\n'+js+'\n'+TESTS+'</script>', html)
    html = re.sub(r'<script src="/static/auth.js[^"]*" defer></script>', '', html)
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.end_headers(); self.wfile.write(html.encode('utf-8'))
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1',0), Handler)
    Thread(target=server.serve_forever,daemon=True).start()
    with tempfile.TemporaryDirectory(prefix='dq-smoke-') as tmp:
        page = Path(tmp)/'smoke.html'; page.write_text(html, encoding='utf-8')
        result = subprocess.run([str(chrome),'--headless','--disable-gpu','--no-first-run','--no-default-browser-check', '--user-data-dir='+str(Path(tmp)/'profile'),'--virtual-time-budget=15000','--dump-dom',f'http://127.0.0.1:{server.server_port}/upload-file'],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=45)
        server.shutdown(); server.server_close()
        rendered = re.sub(r'<script\b[^>]*>.*?</script>', '', result.stdout, flags=re.S)
        matches = list(re.finditer(r'<pre id="smoke-result">(.*?)</pre>',rendered,re.S))
        match = matches[-1] if matches else None
        if not match: raise SystemExit('Browser did not complete checks: '+rendered[-2500:]+result.stderr[-1000:])
        print(match.group(1))
        if not match.group(1).startswith('PASS'): raise SystemExit(1)

if __name__ == '__main__':
    main()
