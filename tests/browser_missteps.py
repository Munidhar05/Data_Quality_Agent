"""Adversarial browser workflows: real input, skipped steps, delays and injected failures."""
import sys
import os
import tempfile
import threading
import time
import socket
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uvicorn
import backend.app as api
import backend.auth as auth
import backend.storage as storage
from playwright.sync_api import sync_playwright, expect


def run():
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    with tempfile.TemporaryDirectory(prefix="dq-missteps-") as temp:
        root=Path(temp);auth.DB_PATH=root/'accounts.sqlite3'
        storage.UPLOAD_DIR=root/'uploads';storage.UPLOAD_DIR.mkdir()
        storage.RUN_DIR=root/'runs';storage.RUN_DIR.mkdir();api.UPLOAD_DIR=storage.UPLOAD_DIR
        api.llm_provider.configured=False;os.environ['DQ_ADMIN_PASSWORD']='missteps-admin-secret'
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
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
                    context=browser.new_context(viewport={'width':1440,'height':900});page=context.new_page();page.set_default_timeout(12000)
                    errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                    posts=[];page.on('request',lambda request:posts.append(request.url) if request.method=='POST' and request.url.endswith('/api/runs') else None)
                    account={'first_name':'Edge','last_name':'Cases','company':'Audit','phone':'9876543210','email':'edge@example.com','password':'edge-case-password'}
                    assert context.request.post(base+'/api/auth/signup',data=account).status==201
                    page.goto(base+'/login');page.locator('[name=email]').fill(account['email']);page.locator('[name=password]').fill('wrong-password');page.locator('#auth-submit').click()
                    expect(page.locator('#auth-message')).to_contain_text('incorrect')
                    page.locator('[name=password]').fill(account['password']);page.locator('#auth-submit').click();expect(page.locator('#welcome-upload')).to_be_visible()
                    def upload(name='A.csv',data=b'id,amount,status\n001,10,active\n002,-1,active\n'):
                        page.locator('#file-input').set_input_files({'name':name,'mimeType':'text/csv','buffer':data})
                        expect(page.locator('#active-dataset-name')).to_have_text(name)
                    upload();dataset=page.evaluate('state.dataset.dataset_id')
                    page.locator('#file-input').set_input_files({'name':'bad.exe','mimeType':'text/plain','buffer':b'not a table'})
                    expect(page.locator('#toast')).to_contain_text('supported data file');assert page.evaluate('state.dataset.dataset_id')==dataset
                    page.locator('#file-input').set_input_files({'name':'empty.csv','mimeType':'text/csv','buffer':b''})
                    expect(page.locator('#notice')).to_contain_text('could not be read');assert page.evaluate('state.dataset.dataset_id')==dataset
                    expect(page.locator('#file-input')).to_be_enabled()
                    page.locator('.rail-link[data-page=review]').click()
                    mandatory=page.locator('.rule-card .rule-toggle[disabled]');expect(mandatory).to_have_count(1)
                    # Clearing mandatory settings on selected optional checks must stop Next.
                    card=page.locator('.rule-card').filter(has=page.locator('[data-param-column]')).first
                    card.locator('.column-picker summary').click();card.locator('[data-column-action=clear]').click()
                    page.locator('[data-step-business]').click();expect(page.locator('#page-review')).to_have_class('page active');expect(card.locator('.check-settings-error')).to_be_visible()
                    card.locator('.rule-toggle').uncheck();page.locator('[data-step-business]').click();expect(page.locator('#page-business')).to_have_class('page active')
                    print('PASS invalid login, unsupported/empty upload recovery, missing settings and disabled-check exclusion')
                    page.locator('#business-input').fill('amount should be reasonable')
                    page.locator('#business-form button').click()
                    expect(page.locator('.business-clarification')).to_have_count(1)
                    expect(page.locator('[data-clarify-column]')).to_have_count(0)
                    expect(page.locator('.business-rule-item')).to_have_count(0)
                    expect(page.locator('[data-clarify-reply]')).to_have_count(0)
                    page.reload()
                    expect(page.locator('.business-clarification')).to_have_count(1)
                    assert not posts
                    page.locator('#business-input').fill('amount must be a valid number greater than or equal to 5.')
                    page.locator('#business-form button').click()
                    expect(page.locator('#business-input')).to_have_value('')
                    expect(page.locator('.business-rule-item')).to_have_count(1)
                    expect(page.locator('.business-rule-item')).to_contain_text('>= 5')
                    page.locator('.remove-business-rule').click()
                    print('PASS unapplied notices, persistence, explicit thresholds and separate review')
                    page.locator('#business-input').fill('Column Prefix starts A')
                    page.locator('#business-form button').click()
                    expect(page.locator('[data-clarify-choice]')).to_have_count(0)
                    expect(page.locator('[data-exclude-unapplied]')).to_have_count(1)
                    page.reload()
                    expect(page.locator('[data-exclude-unapplied]')).to_have_count(1)
                    page.locator('[data-exclude-unapplied]').click()
                    expect(page.locator('#business-input')).to_have_value('')
                    expect(page.locator('.business-rule-item')).to_have_count(0)
                    assert not posts
                    print('PASS unsupported notices, exclusion and refresh persistence')

                    page.locator('#business-input').fill('amount must be greater than 0')
                    pattern='**/business-rules/batch'
                    page.route(pattern,lambda route:route.fulfill(status=503,content_type='application/json',body='{"detail":"Temporary generation failure"}'))
                    page.locator('#approve-run').click();expect(page.locator('#business-feedback')).to_contain_text('Temporary generation failure')
                    expect(page.locator('#business-input')).to_have_value('amount must be greater than 0');assert not posts
                    expect(page.locator('#business-form button')).to_be_enabled();page.unroute(pattern)
                    pending=[];page.route(pattern,lambda route:pending.append(route))
                    page.evaluate("document.querySelector('#approve-run').click();document.querySelector('#approve-run').click()")
                    page.wait_for_function('businessBusy');assert len(pending)==1
                    expect(page.locator('#business-input')).to_be_disabled();expect(page.locator('.remove-business-rule')).to_have_count(0)
                    expect(page.locator('.rule-toggle').first).to_be_disabled()
                    page.locator('.rail-link[data-page=history]').click();expect(page.locator('#page-business')).to_have_class('page active')
                    pending[0].continue_();page.unroute(pattern);expect(page.locator('.business-rule-item')).to_have_count(1)
                    expect(page.locator('#business-form button')).to_be_enabled();expect(page.locator('#business-input')).to_be_enabled();assert not posts
                    # Editing after generation must generate an additional rule, not silently run the old rule alone.
                    page.locator('#business-input').fill('amount must be less than 100')
                    page.locator('#approve-run').click();expect(page.locator('.business-rule-item')).to_have_count(2);assert not posts
                    page.locator('.remove-business-rule').last.click();expect(page.locator('.business-rule-item')).to_have_count(1)
                    print('PASS generation failure/retry, double click, request-time edits, skipped Generate and edited requirements')
                    # A failed business document remains a blocker after refresh.
                    page.locator('#business-document').set_input_files({'name':'empty.txt','mimeType':'text/plain','buffer':b''})
                    expect(page.locator('#business-document-error')).to_contain_text('was not added');expect(page.locator('#approve-run')).to_be_disabled()
                    page.reload();expect(page.locator('#business-document-error')).to_contain_text('empty.txt');expect(page.locator('#approve-run')).to_be_disabled()
                    page.locator('#exclude-failed-document').click();expect(page.locator('#approve-run')).to_be_enabled()
                    # A document containing one unresolved point cannot silently validate only its supported point.
                    page.locator('#business-document').set_input_files({'name':'mixed.txt','mimeType':'text/plain','buffer':b'amount must be greater than 0\namount should be valid'})
                    expect(page.locator('#business-input')).to_have_value('amount should be valid');expect(page.locator('#approve-run')).to_have_text('Generate & review rules');assert not posts
                    page.locator('#business-input').fill('')
                    # Validation failure does not report success or lose reviewed rules; double clicks submit once.
                    runpattern='**/api/runs';page.route(runpattern,lambda route:route.fulfill(status=503,content_type='application/json',body='{"detail":"Temporary validation failure"}'))
                    page.locator('#approve-run').click();expect(page.locator('#agent-title')).to_have_text('Processing could not finish')
                    expect(page.locator('.business-rule-item')).to_have_count(1);expect(page.locator('#approve-run')).to_be_enabled();page.unroute(runpattern)
                    pending=[];page.route(runpattern,lambda route:pending.append(route));before=len(posts)
                    page.evaluate("document.querySelector('#approve-run').click();document.querySelector('#approve-run').click()")
                    page.wait_for_function('agentBusy');assert len(pending)==1 and len(posts)==before+1
                    expect(page.locator('.remove-business-rule')).to_be_disabled();expect(page.locator('#business-input')).to_be_disabled()
                    pending[0].continue_();page.unroute(runpattern);expect(page.locator('#dq-summary-report')).to_be_visible()
                    run_url=page.url;run_id=page.evaluate('state.lastRun.run_id');assert page.evaluate('state.lastRun.business_rules.length')==1
                    print('PASS failed/mixed document gating, validation failure/retry and single submission during double click')
                    # Opening Results must not overwrite edits made after the run.
                    page.locator('.rail-link[data-page=business]').click();page.locator('#business-input').fill('amount must be at least 5')
                    page.locator('.rail-link[data-page=history]').click();expect(page.locator('.history-open')).to_be_visible();page.locator('.history-open').click();expect(page.locator('#dq-summary-report')).to_be_visible()
                    page.locator('.rail-link[data-page=business]').click();expect(page.locator('#business-input')).to_have_value('amount must be at least 5')
                    page.locator('.rail-link[data-page=results]').click();page.locator('#accepted-name').fill('Old renamed output')
                    page.locator('.rail-link[data-page=review]').click();page.locator('.rail-link[data-page=results]').click();expect(page.locator('#accepted-name')).to_have_value('Old renamed output')
                    # New file cannot inherit old output names.
                    upload('B.csv');page.locator('.rail-link[data-page=review]').click();page.locator('[data-step-business]').click();page.locator('#approve-run').click();expect(page.locator('#dq-summary-report')).to_be_visible()
                    assert page.evaluate('state.lastRun.outputs.accepted')=='B_accepted'
                    # Capture download name before slow request; later edits cannot change an in-flight filename.
                    page.locator('#accepted-name').fill('First name');pending=[];downloadpattern='**/download/accepted';page.route(downloadpattern,lambda route:pending.append(route))
                    page.locator('[data-download-records=accepted]').click();page.wait_for_function("document.querySelector('#accepted-name').value === 'First name'");assert len(pending)==1
                    page.locator('#accepted-name').fill('Second name')
                    with page.expect_download() as download:pending[0].continue_()
                    assert download.value.suggested_filename=='First name.csv';page.unroute(downloadpattern)
                    print('PASS historical report preserves pending draft, new-file output names and in-flight download identity')
                    # History retry and record retry provide a visible recovery action.
                    page.route(runpattern,lambda route:route.fulfill(status=503,content_type='application/json',body='{"detail":"History unavailable"}'))
                    page.locator('.rail-link[data-page=history]').click();expect(page.locator('#history-retry')).to_be_visible();page.unroute(runpattern);page.locator('#history-retry').click();expect(page.locator('.history-open')).to_have_count(2)
                    rows='**/rows?*';page.route(rows,lambda route:route.fulfill(status=503,content_type='application/json',body='{"detail":"Records unavailable"}'))
                    page.locator('.rail-link[data-page=profile]').click();expect(page.locator('#explorer-retry')).to_be_visible();page.unroute(rows);page.locator('#explorer-retry').click();expect(page.locator('#explorer-table tbody tr')).to_have_count(2)
                    # Slow semantic saves cannot change the active file or receive duplicate clicks.
                    page.locator('.file-meaning-editor summary').first.click()
                    semantic='**/semantics';pending=[];page.route(semantic,lambda route:pending.append(route))
                    page.locator('.semantic-editor select').first.select_option('IDENTIFIER')
                    page.locator('.semantic-save').first.click();page.wait_for_function('agentBusy');assert len(pending)==1
                    expect(page.locator('#file-input')).to_be_disabled()
                    page.locator('.rail-link[data-page=history]').click();expect(page.locator('#page-profile')).to_have_class('page active')
                    pending[0].continue_();page.unroute(semantic);page.wait_for_function('!agentBusy');expect(page.locator('#file-input')).to_be_enabled()
                    # Reference replacement failures explicitly retain the old file, never claim the new file is ready.
                    page.locator('.rail-link[data-page=review]').click();page.locator('.add-check-picker > summary').click()
                    reference=page.locator('.add-optional-check[data-rule-id="DQ-CATALOG-010"]');reference.evaluate("el => el.closest('details').open = true");reference.click()
                    refcard=page.locator('.rule-card[data-rule-id="DQ-CATALOG-010"]')
                    refcard.locator('.reference-file-input').set_input_files({'name':'Reference.csv','mimeType':'text/csv','buffer':b'id\n001\n'})
                    expect(refcard.locator('.reference-status')).to_have_text('Reference ready: Reference.csv')
                    previous=refcard.locator('[data-value-type=json]').input_value()
                    uploads='**/api/datasets';pending=[];page.route(uploads,lambda route:pending.append(route))
                    refcard.locator('.reference-file-input').set_input_files({'name':'Replacement.csv','mimeType':'text/csv','buffer':b'id\n002\n'})
                    page.wait_for_function('businessBusy');assert len(pending)==1
                    expect(refcard.locator('.delete-test')).to_be_disabled()
                    pending[0].fulfill(status=503,content_type='application/json',body='{"detail":"Reference unavailable"}');page.unroute(uploads)
                    expect(refcard.locator('.reference-status')).to_contain_text('still using Reference.csv')
                    assert refcard.locator('[data-value-type=json]').input_value()==previous
                    expect(refcard.locator('.delete-test')).to_be_enabled()
                    refcard.locator('.reference-file-input').set_input_files({'name':'bad.exe','mimeType':'text/plain','buffer':b'invalid'})
                    expect(refcard.locator('.reference-status')).to_contain_text('Still using Reference.csv')
                    assert refcard.locator('[data-value-type=json]').input_value()==previous
                    print('PASS slow semantic-save navigation and failed/unsupported reference replacements')
                    # Failed logout must be visible on the page where the user clicked it.
                    logout='**/api/auth/logout';page.route(logout,lambda route:route.abort());page.locator('#logout-button').click();expect(page.locator('#toast')).to_contain_text('Could not sign out');page.unroute(logout)
                    page.locator('.rail-link[data-page=results]').click()
                    page.route(downloadpattern,lambda route:route.fulfill(status=401,content_type='application/json',body='{"detail":"Please log in to continue."}'))
                    page.locator('[data-download-records=accepted]').click();expect(page.locator('#auth-submit')).to_be_visible();assert '/login?next=' in page.url;page.unroute(downloadpattern)
                    print('PASS History/record retry, visible logout failure and expired-session download recovery')
                    # Administrator cannot edit or dismiss a modal during save; retry keeps form values.
                    admin=context.new_page();admin.on('pageerror',lambda error:errors.append(str(error)));admin.goto(base+'/admin')
                    admin.locator('#admin-login-form [name=password]').fill('missteps-admin-secret');admin.locator('#admin-login-form button').click();expect(admin.locator('#admin-workspace')).to_be_visible()
                    admin.locator('#create-user').click()
                    for key,value in {'first_name':'New','last_name':'User','company':'Test','email':'new@example.com','phone':'9876543210','password':'new-user-password'}.items():admin.locator(f'#user-form [name={key}]').fill(value)
                    users='**/api/admin/users';pending=[]
                    admin.route(users,lambda route:pending.append(route) if route.request.method=='POST' else route.continue_())
                    admin.locator('#user-form button[type=submit]').click();expect(admin.locator('#user-form [name=email]')).to_be_disabled();expect(admin.locator('#close-dialog')).to_be_disabled()
                    admin.keyboard.press('Escape');expect(admin.locator('#user-dialog')).to_be_visible()
                    pending[0].fulfill(status=503,content_type='application/json',body='{"detail":"Save unavailable"}');expect(admin.locator('#user-form-error')).to_contain_text('Save unavailable');expect(admin.locator('#user-form [name=email]')).to_be_enabled();admin.unroute(users)
                    admin.locator('#user-form button[type=submit]').click();expect(admin.locator('#users-body tr').filter(has_text='new@example.com')).to_be_visible()
                    print('PASS administrator save locking, Escape handling, failed save and retry')
                    assert not errors,errors
                    print('PASS adversarial workflow audit: no application JavaScript errors')
                finally:browser.close()
        finally:server.should_exit=True;thread.join(10)

if __name__=='__main__':run()
