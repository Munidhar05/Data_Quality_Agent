"""Manual browser integration: virtualenv python tests/browser_sources.py."""
import json
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import uvicorn
import backend.app as api
import backend.auth as auth
import backend.storage as storage
from backend import connectors
from playwright.sync_api import sync_playwright, expect


def run():
    with tempfile.TemporaryDirectory(prefix='dq-sources-') as temp:
        root=Path(temp)
        auth.DB_PATH=root/'accounts.sqlite3'
        storage.UPLOAD_DIR=root/'uploads'
        storage.RUN_DIR=root/'runs'
        api.UPLOAD_DIR=storage.UPLOAD_DIR
        api.storage=storage.LocalFileStorage()
        api.llm_provider.configured=False
        os.environ['DQ_SOURCES_JSON']=json.dumps({'browser':{'kind':'rest','config':{}}})
        data=[b'id,amount\n001,10\n002,20\n']
        def import_databricks(config,limit):
            assert connectors.secret(config,'token_env')=='browser-private-token'
            return connectors.ImportedData('records.csv',data[0])
        connectors.HANDLERS['databricks']=import_databricks
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
        base=f'http://127.0.0.1:{port}'
        server=uvicorn.Server(uvicorn.Config(api.app,host='127.0.0.1',port=port,log_level='error'))
        thread=threading.Thread(target=server.run,daemon=True);thread.start()
        try:
            for _ in range(100):
                if server.started:break
                time.sleep(.1)
            assert server.started
            with sync_playwright() as p:
                browser=p.chromium.launch(channel='msedge',headless=True)
                try:
                    page=browser.new_page(viewport={'width':1440,'height':900})
                    errors=[]
                    page.on('pageerror',lambda error:errors.append(str(error)))
                    credentials={'email':'browser-sources@example.com','password':'Browser-test-123'}
                    assert page.request.post(base+'/api/auth/signup',data={**credentials,'first_name':'Browser','last_name':'Tester','company':'Test','phone':'9876543210'}).status==201
                    page.goto(base+'/login')
                    page.locator('[name=email]').fill(credentials['email'])
                    page.locator('[name=password]').fill(credentials['password'])
                    page.locator('#auth-submit').click()
                    expect(page.locator('#welcome-upload')).to_be_visible()
                    expect(page.locator('#source-select option')).to_have_count(17)
                    picker=page.locator('.file-picker').bounding_box()
                    dropdown=page.locator('#source-select').bounding_box()
                    assert abs(picker['y']-dropdown['y'])<60
                    page.locator('#source-select').select_option('databricks')
                    expect(page.locator('#connection-heading')).to_have_text('Connect to Databricks')
                    expect(page.locator('[data-connection-key]')).to_have_count(4)
                    for key,value in {'server_hostname':'host.example','http_path':'/sql/path','token':'browser-private-token','table':'main.default.orders'}.items():
                        page.locator('[data-connection-key='+key+']').fill(value)
                    assert page.locator('#connection-token').get_attribute('type')=='password'
                    page.locator('#source-import-form button[type=submit]').click()
                    expect(page.locator('#active-dataset-name')).to_have_text('orders')
                    expect(page.locator('.dataset-name-block .eyebrow')).to_have_text('YOUR TABLE')
                    expect(page.locator('#active-dataset-source')).to_have_text('Databricks table')
                    expect(page.locator('#connection-token')).to_have_value('')
                    assert 'browser-private-token' not in page.evaluate('JSON.stringify(sessionStorage) + JSON.stringify(localStorage)')
                    assert page.evaluate('state.profile.row_count')==2
                    data[0]=b'id,amount,status\n001,bad,active\n002,20,active\n'
                    page.get_by_role('button',name='Connect another source').click()
                    page.locator('#connection-token').fill('browser-private-token')
                    page.locator('#source-import-form button[type=submit]').click()
                    expect(page.locator('#source-feedback')).to_contain_text('Connected and imported successfully')
                    page.wait_for_function('state.profile && state.profile.column_count === 3')
                    assert page.evaluate('state.profile.column_count')==3
                    page.goto(base+'/file-details?dataset='+page.evaluate('state.dataset.dataset_id'))
                    expect(page.locator('#page-profile')).to_have_class('page active')
                    expect(page.locator('#schema-baseline')).to_have_count(0)
                    expect(page.locator('#schema-renames')).to_have_count(0)
                    expect(page.locator('#schema-description')).not_to_be_visible()
                    page.locator('.schema-check-card > summary').click()
                    expect(page.locator('#schema-description')).to_be_visible()
                    page.locator('#schema-description').fill('id, amount as number and status')
                    page.locator('#schema-compare-form button').click()
                    expect(page.locator('#schema-feedback')).to_contain_text('Some schema expectations do not match')
                    expect(page.locator('#schema-feedback')).to_contain_text('status')
                    page.set_viewport_size({'width':390,'height':844})
                    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'Mobile overflow'
                    page.goto(base+'/upload-file')
                    page.locator('#source-select').select_option('databricks')
                    page.locator('#connection-token').fill('discard-on-switch')
                    page.locator('#source-select').select_option('s3')
                    expect(page.locator('#connection-token')).to_have_count(0)
                    expect(page.locator('#connection-access_key_id')).to_be_visible()
                    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                    assert not errors,errors
                    print('PASS browser source imports, schema drift, normal dataset flow and mobile layout')
                finally:
                    browser.close()
        finally:
            server.should_exit=True;thread.join(10)

if __name__=='__main__':run()
