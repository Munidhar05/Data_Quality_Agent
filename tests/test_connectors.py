import io
import json
from types import SimpleNamespace as NS
from unittest.mock import MagicMock
import httpx
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from backend import connectors as c
from backend.schema_changes import compare, snapshot


def mock_rest(monkeypatch, handler):
    client = httpx.Client
    monkeypatch.setattr(c.httpx, 'Client', lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs))


def test_rest_pages_preserve_identifiers_and_precision(monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        page = int(request.url.params['page'])
        return httpx.Response(200, json={'data': [{'id': '001', 'large': 9007199254740993}] if page == 1 else []})
    mock_rest(monkeypatch, handler)
    monkeypatch.setenv('DQ_SOURCE_TEST_TOKEN', 'private-token')
    result = c.rest({'url':'https://example.com/orders', 'token_env':'DQ_SOURCE_TEST_TOKEN', 'records_path':'data', 'pagination':'page'}, 10)
    assert json.loads(result.content) == [{'id':'001','large':'9007199254740993'}]
    assert len(calls) == 2 and calls[0].headers['authorization'] == 'Bearer private-token'


@pytest.mark.parametrize('mode', ['page', 'offset'])
def test_short_pages_do_not_silently_truncate(monkeypatch, mode):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=[{'id':len(calls)}] if len(calls) < 3 else [])
    mock_rest(monkeypatch, handler)
    assert len(json.loads(c.rest({'url':'https://example.com/a','pagination':mode,'page_size':10}, 10).content)) == 2
    key = 'page' if mode == 'page' else 'offset'
    assert [int(x.url.params[key]) for x in calls] == ([1,2,3] if mode == 'page' else [0,10,20])


@pytest.mark.parametrize('next_url', ['https://evil.example/a', 'http://example.com/a', 'https://example.com/a'])
def test_next_links_cannot_leak_credentials_or_loop(monkeypatch, next_url):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={'rows':[{'id':1}], 'next':next_url})
    mock_rest(monkeypatch, handler)
    with pytest.raises(c.ConnectorError):
        c.rest({'url':'https://example.com/a','pagination':'next_url','records_path':'rows','next_path':'next'}, 10)
    assert len(calls) == 1


def test_next_url_complete(monkeypatch):
    def handler(request):
        return httpx.Response(200, json={'rows':[{'id':str(request.url)}], 'next':'/b' if request.url.path == '/a' else None})
    mock_rest(monkeypatch, handler)
    assert len(json.loads(c.rest({'url':'https://example.com/a','records_path':'rows','pagination':'next_url','next_path':'next'}, 10).content)) == 2


@pytest.mark.parametrize('status', [301,401,403,500])
def test_http_errors_do_not_expose_response_secrets(monkeypatch, status):
    mock_rest(monkeypatch, lambda request: httpx.Response(status, text='private-token'))
    with pytest.raises(c.ConnectorError) as error:
        c.rest({'url':'https://example.com/a'}, 10)
    assert 'private-token' not in str(error.value)


def test_retries_and_page_cap(monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(503 if len(calls) == 1 else 200, json=[{'id':1}])
    mock_rest(monkeypatch, handler)
    monkeypatch.setattr(c.time, 'sleep', lambda x: None)
    with pytest.raises(c.ConnectorError, match='Page limit'):
        c.rest({'url':'https://example.com/a','pagination':'page','max_pages':1}, 10)
    assert len(calls) == 2


@pytest.mark.parametrize('payload', [b'not-json', b'{"x":1}', b'[1,2]', b'[]'])
def test_bad_or_empty_api_results(monkeypatch, payload):
    mock_rest(monkeypatch, lambda request: httpx.Response(200, content=payload))
    with pytest.raises(c.ConnectorError):
        c.rest({'url':'https://example.com/a'}, 10)


def test_limits_are_errors_not_partial_imports(monkeypatch):
    with pytest.raises(c.ConnectorError, match='record limit'):
        c.records_file([{'x':1}, {'x':2}], 1)
    monkeypatch.setattr(c, 'MAX_UPLOAD_BYTES', 10)
    with pytest.raises(c.ConnectorError):
        c.bounded([b'123456', b'123456'])
    with pytest.raises(c.ConnectorError):
        c.records_file([{'large':'too long'}], 10)


def test_server_catalog_access_and_error_redaction(monkeypatch):
    monkeypatch.setenv('DQ_SOURCES_JSON', json.dumps({'private':{'kind':'rest','owner_ids':['1'],'config':{'url':'https://secret.example'}},'shared':{'kind':'rest','config':{}}}))
    assert [x['id'] for x in c.catalog('2')] == ['shared']
    assert 'config' not in c.catalog('1')[0]
    with pytest.raises(FileNotFoundError):
        c.import_source('private', '2')
    def failure(config, limit):
        raise RuntimeError('password=private-token')
    monkeypatch.setitem(c.HANDLERS, 'rest', failure)
    with pytest.raises(c.ConnectorError) as error:
        c.import_source('shared', '2')
    assert 'private-token' not in str(error.value)


def test_s3_bounded_read_and_cleanup(monkeypatch):
    body = io.BytesIO(b'id\n001\n')
    client = MagicMock()
    client.get_object.return_value = {'Body':body}
    modules = {'boto3':NS(client=lambda *a,**k:client), 'botocore.config':NS(Config=lambda **kw:kw)}
    monkeypatch.setattr(c, 'sdk', lambda module, package:modules[module])
    assert c.s3({'bucket':'bucket','key':'folder/data.csv'}, 10).content == b'id\n001\n'
    client.get_object.assert_called_once_with(Bucket='bucket', Key='folder/data.csv')
    assert body.closed
    client.close.assert_called_once()


@pytest.mark.parametrize('adls', [False, True])
def test_azure_streams_and_closes(monkeypatch, adls):
    client = MagicMock()
    stream = MagicMock()
    stream.chunks.return_value = iter([b'id\n', b'001\n'])
    client.get_blob_client.return_value.download_blob.return_value = stream
    client.get_file_system_client.return_value.get_file_client.return_value.download_file.return_value = stream
    cls = MagicMock(return_value=client)
    credential = MagicMock()
    modules = {'azure.identity':NS(DefaultAzureCredential=lambda:credential), 'azure.storage.blob':NS(BlobServiceClient=cls), 'azure.storage.filedatalake':NS(DataLakeServiceClient=cls)}
    monkeypatch.setattr(c, 'sdk', lambda module, package:modules[module])
    result = (c.adls if adls else c.azure)({'account_url':'https://account.example','container':'files','key':'data.csv'},10)
    assert result.content == b'id\n001\n'
    client.close.assert_called_once()
    credential.close.assert_called_once()


def test_gcs_uses_generation_guard(monkeypatch):
    client, blob = MagicMock(), MagicMock()
    blob.size, blob.generation = 7, 42
    blob.download_as_bytes.return_value = b'id\n001\n'
    client.bucket.return_value.blob.return_value = blob
    monkeypatch.setattr(c, 'sdk', lambda *args:NS(Client=lambda **kw:client))
    assert c.gcs({'bucket':'bucket','key':'file.csv'}, 10).filename == 'file.csv'
    blob.download_as_bytes.assert_called_once_with(if_generation_match=42,timeout=c.TIMEOUT)
    client.close.assert_called_once()


def test_databricks_quotes_table_and_limits(monkeypatch):
    connection, cursor = MagicMock(), MagicMock()
    connection.__enter__.return_value = connection
    connection.cursor.return_value.__enter__.return_value = cursor
    cursor.description = [('id',)]
    cursor.fetchmany.side_effect = [[('001',)],[]]
    monkeypatch.setenv('DQ_SOURCE_DB_TOKEN','token')
    monkeypatch.setattr(c,'sdk',lambda *args:NS(connect=lambda **kw:connection))
    config = {'server_hostname':'host','http_path':'/sql/path','table':'catalog.schema.orders','token_env':'DQ_SOURCE_DB_TOKEN'}
    assert json.loads(c.databricks(config,10).content) == [{'id':'001'}]
    cursor.execute.assert_called_once_with('SELECT * FROM `catalog`.`schema`.`orders` LIMIT 11')
    with pytest.raises(c.ConnectorError):
        c.databricks({**config,'table':'orders; DROP TABLE orders'},10)


def test_bigquery_limit_and_cleanup(monkeypatch):
    client = MagicMock()
    client.get_table.return_value = NS(num_rows=1)
    client.list_rows.return_value = [{'id':'001'}]
    monkeypatch.setattr(c,'sdk',lambda *args:NS(Client=lambda **kw:client))
    assert json.loads(c.bigquery({'table':'p.d.t'},10).content) == [{'id':'001'}]
    assert client.list_rows.call_args.kwargs['max_results'] == 11
    client.close.assert_called_once()


def test_database_real_sqlite_and_overflow(tmp_path, monkeypatch):
    sa = pytest.importorskip('sqlalchemy')
    path = tmp_path / 'source.sqlite'
    url = 'sqlite:///' + str(path)
    engine = sa.create_engine(url)
    with engine.begin() as conn:
        conn.exec_driver_sql('CREATE TABLE orders (id TEXT, amount INTEGER)')
        conn.exec_driver_sql("INSERT INTO orders VALUES ('001',10), ('002',20)")
    engine.dispose()
    monkeypatch.setenv('DQ_SOURCE_DATABASE_URL',url)
    config = {'url_env':'DQ_SOURCE_DATABASE_URL','table':'orders'}
    assert json.loads(c.database(config,2).content)[0] == {'id':'001','amount':'10'}
    with pytest.raises(c.ConnectorError, match='record limit'):
        c.database(config,1)


def test_schema_changes_and_explicit_renames():
    baseline = snapshot(pd.DataFrame({'old':['a'],'amount':[1],'removed':['x']}))
    current = snapshot(pd.DataFrame({'amount':['bad'],'new':['a'],'added':[True]}))
    diff = compare(baseline,current,{'old':'new'})
    assert diff['added_columns'] == ['added'] and diff['removed_columns'] == ['removed']
    assert diff['renamed_columns'] == [{'before':'old','after':'new'}]
    assert diff['type_changes'][0]['column'] == 'amount'
    assert diff['column_order_changed']
    assert compare(current,current)['changed'] is False
    with pytest.raises(ValueError):
        compare(baseline,current,{'old':'amount'})


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    import backend.app as api
    import backend.storage as storage
    monkeypatch.setattr(storage,'UPLOAD_DIR',tmp_path/'uploads')
    monkeypatch.setattr(storage,'RUN_DIR',tmp_path/'runs')
    monkeypatch.setattr(api,'storage',storage.LocalFileStorage())
    monkeypatch.setattr(api.llm_provider,'configured',False)
    monkeypatch.setenv('DQ_SOURCES_JSON',json.dumps({'test':{'kind':'rest','config':{'url':'https://example.com'}}}))
    client = TestClient(api.app)
    credentials = {'email':'sources@example.com','password':'test-password'}
    assert client.post('/api/auth/signup',json={**credentials,'first_name':'Source','last_name':'Tester','company':'Test','phone':'9876543210'}).status_code == 201
    assert client.post('/api/auth/login',json=credentials).status_code == 200
    return client


def test_import_uses_normal_validation_and_schema_endpoints(api_client,monkeypatch):
    monkeypatch.setitem(c.HANDLERS,'rest',lambda config,limit:c.ImportedData('orders.csv',b'id,amount\n001,10\n002,20\n'))
    response = api_client.post('/api/datasets/import',json={'source_id':'test'})
    assert response.status_code == 200, response.text
    payload = response.json()
    dataset = payload['dataset']['dataset_id']
    assert payload['profile']['row_count'] == 2
    assert payload['dataset']['source']['source_id'] == 'test'
    assert 'example.com' not in response.text
    assert api_client.get(f'/api/datasets/{dataset}/rows').json()['rows'][0]['id'] == '001'
    assert api_client.get(f'/api/datasets/{dataset}/schema').status_code == 200
    diff = api_client.post(f'/api/datasets/{dataset}/schema/compare',json={'baseline_dataset_id':dataset})
    assert diff.status_code == 200 and diff.json()['changed'] is False
    run = api_client.post('/api/runs',json={'dataset_id':dataset,'approved':True,'selected_rules':[{'rule_id':'DQ-CATALOG-005','configuration':{}}]})
    assert run.status_code == 200, run.text
    assert api_client.get('/api/runs/'+run.json()['run_id']+'/download/accepted').status_code == 200


@pytest.mark.parametrize('content', [b'not-a-table',b'id\n',b'id,id\n1,2\n',b'id\n1\n2\n'])
def test_bad_import_leaves_no_dataset(api_client,monkeypatch,content):
    import backend.storage as storage
    monkeypatch.setitem(c.HANDLERS,'rest',lambda config,limit:c.ImportedData('bad.csv',content))
    response = api_client.post('/api/datasets/import',json={'source_id':'test','max_rows':1})
    assert response.status_code == 422, response.text
    assert list(storage.UPLOAD_DIR.iterdir()) == []


def test_sources_and_schemas_require_auth_and_ownership(api_client,monkeypatch):
    import backend.app as api
    monkeypatch.setitem(c.HANDLERS,'rest',lambda config,limit:c.ImportedData('ok.csv',b'id\n001\n'))
    dataset = api_client.post('/api/datasets/import',json={'source_id':'test'}).json()['dataset']['dataset_id']
    other = TestClient(api.app)
    assert other.get('/api/sources').status_code == 401
    credentials = {'email':'other@example.com','password':'test-password'}
    other.post('/api/auth/signup',json={**credentials,'first_name':'Other','last_name':'Tester','company':'Test','phone':'9876543210'})
    other.post('/api/auth/login',json=credentials)
    assert other.get(f'/api/datasets/{dataset}/schema').status_code == 404
    assert other.post('/api/datasets/import',json={'source_id':'test','baseline_dataset_id':dataset}).status_code == 404
    assert api_client.post('/api/datasets/import',json={'source_id':'test','max_rows':1.5}).status_code == 422
    assert api_client.post('/api/datasets/import',json={'source_id':'test','url':'https://evil.example'}).status_code == 422


def test_nonfinite_profile_is_json_safe():
    from backend.profiler import profile_frame
    from starlette.responses import JSONResponse
    JSONResponse(profile_frame(pd.DataFrame({'amount':[float('inf'),1]})))


@pytest.mark.parametrize('service', ['athena','redshift-data'])
def test_aws_query_pages_and_large_integers(monkeypatch,service):
    client=MagicMock()
    modules={'boto3':NS(client=lambda *a,**k:client),'botocore.config':NS(Config=lambda **kw:kw)}
    monkeypatch.setattr(c,'sdk',lambda module,package:modules[module])
    config={'database':'db','table':'orders','workgroup_name':'warehouse'}
    if service=='athena':
        client.start_query_execution.return_value={'QueryExecutionId':'query'}
        client.get_query_execution.return_value={'QueryExecution':{'Status':{'State':'SUCCEEDED'}}}
        def result(value,token=None):
            rows=[{'Data':[{'VarCharValue':v}]} for v in value]
            return {'ResultSet':{'ResultSetMetadata':{'ColumnInfo':[{'Name':'id'}]},'Rows':rows},**({'NextToken':token} if token else {})}
        client.get_query_results.side_effect=[result(['id','001'],'next'),result(['9007199254740993'])]
    else:
        client.execute_statement.return_value={'Id':'query'}
        client.describe_statement.return_value={'Status':'FINISHED'}
        client.get_statement_result.side_effect=[{'ColumnMetadata':[{'name':'id'}],'Records':[[{'stringValue':'001'}]],'NextToken':'next'}, {'ColumnMetadata':[{'name':'id'}],'Records':[[{'longValue':9007199254740993}]]}]
    data=c.aws_query(config,10,service)
    assert json.loads(data.content)==[{'id':'001'},{'id':'9007199254740993'}]
    statement=client.start_query_execution.call_args.kwargs['QueryString'] if service=='athena' else client.execute_statement.call_args.kwargs['Sql']
    assert statement=='SELECT * FROM "orders" LIMIT 11'
    client.close.assert_called_once()


@pytest.mark.parametrize('service', ['athena','redshift-data'])
def test_aws_query_timeout_cancels_and_closes(monkeypatch,service):
    client=MagicMock()
    client.start_query_execution.return_value={'QueryExecutionId':'query'}
    client.execute_statement.return_value={'Id':'query'}
    modules={'boto3':NS(client=lambda *a,**k:client),'botocore.config':NS(Config=lambda **kw:kw)}
    monkeypatch.setattr(c,'sdk',lambda module,package:modules[module])
    times=iter([0,2])
    monkeypatch.setattr(c.time,'monotonic',lambda:next(times))
    with pytest.raises(c.ConnectorError,match='timed out'):
        c.aws_query({'database':'db','table':'orders','workgroup_name':'wg','query_timeout_seconds':1},10,service)
    if service=='athena':client.stop_query_execution.assert_called_once_with(QueryExecutionId='query')
    else:client.cancel_statement.assert_called_once_with(Id='query')
    client.close.assert_called_once()



def test_user_entered_databricks_credentials_are_scoped_and_not_persisted(api_client,monkeypatch):
    import backend.storage as storage
    seen=[]
    def handler(config,limit):
        seen.append(c.secret(config,'token_env'))
        assert 'token' not in config
        return c.ImportedData('data.csv',b'id\n001\n')
    monkeypatch.setitem(c.HANDLERS,'databricks',handler)
    payload={'kind':'databricks','configuration':{'server_hostname':'host.example','http_path':'/sql/path','token':'private-ui-token','table':'main.default.orders'}}
    response=api_client.post('/api/datasets/connect',json=payload)
    assert response.status_code==200,response.text
    assert seen==['private-ui-token']
    assert c.REQUEST_CONNECTION.get() is None
    assert 'private-ui-token' not in response.text
    for path in storage.UPLOAD_DIR.iterdir():
        assert b'private-ui-token' not in path.read_bytes()
    assert response.json()['dataset']['source']=={'kind':'databricks','connection_mode':'user_entered','table':'main.default.orders','imported_at':response.json()['dataset']['source']['imported_at']}


@pytest.mark.parametrize('patch', [{'max_rows':'private-ui-token'},{'kind':'bad'},{'configuration':{'token':'private-ui-token'}},{'extra':'private-ui-token'}])
def test_user_connection_errors_never_echo_credentials(api_client,patch):
    payload={'kind':'databricks','configuration':{'server_hostname':'host.example','http_path':'/sql/path','token':'private-ui-token','table':'main.default.orders'}}
    response=api_client.post('/api/datasets/connect',json={**payload,**patch})
    assert response.status_code==422
    assert 'private-ui-token' not in response.text
    assert c.REQUEST_CONNECTION.get() is None


def test_user_connection_sdk_errors_are_sanitized(api_client,monkeypatch):
    def failure(config,limit):raise RuntimeError('private-ui-token in connection error')
    monkeypatch.setitem(c.HANDLERS,'databricks',failure)
    response=api_client.post('/api/datasets/connect',json={'kind':'databricks','configuration':{'server_hostname':'host.example','http_path':'/sql/path','token':'private-ui-token','table':'orders'}})
    assert response.status_code==422 and 'private-ui-token' not in response.text
    assert c.REQUEST_CONNECTION.get() is None


def test_connection_catalog_exposes_all_forms_without_credentials(api_client):
    response=api_client.get('/api/connection-types')
    assert response.status_code==200
    types={x['id']:x for x in response.json()}
    assert {'databricks','rest','s3','athena','redshift','azure_blob','adls','gcs','bigquery','postgresql','mysql','mssql','azure_sql','synapse','snowflake','oracle'}==set(types)
    assert [x['key'] for x in types['databricks']['fields']]==['server_hostname','http_path','token','table']
    assert types['databricks']['fields'][2]['secret']


def test_concurrent_credentials_do_not_cross_requests(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from backend.connection_forms import import_connection
    barrier=Barrier(2)
    def handler(config,limit):
        barrier.wait(timeout=10)
        value=c.secret(config,'token_env')
        return c.ImportedData('data.csv',('value\n'+value+'\n').encode())
    monkeypatch.setitem(c.HANDLERS,'databricks',handler)
    def run(token):
        result,_=import_connection('databricks',{'server_hostname':'host.example','http_path':'/sql/path','token':token,'table':'orders'},10)
        assert c.REQUEST_CONNECTION.get() is None
        return result.content
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(run,['first-token','second-token']))==[b'value\nfirst-token\n',b'value\nsecond-token\n']


def test_ui_aws_credentials_are_passed_to_sdk(monkeypatch):
    from backend.connection_forms import import_connection
    calls=[]
    client=MagicMock()
    client.get_object.return_value={'Body':io.BytesIO(b'id\n001\n')}
    def factory(*args,**kwargs):calls.append(kwargs);return client
    modules={'boto3':NS(client=factory),'botocore.config':NS(Config=lambda **kw:kw)}
    monkeypatch.setattr(c,'sdk',lambda module,package:modules[module])
    import_connection('s3',{'region':'us-east-1','access_key_id':'user-key','secret_access_key':'user-secret','session_token':'session','bucket':'bucket','key':'file.csv'},10)
    assert calls[0]['aws_access_key_id']=='user-key'
    assert calls[0]['aws_secret_access_key']=='user-secret'
    assert calls[0]['aws_session_token']=='session'
    assert c.REQUEST_CONNECTION.get() is None



@pytest.mark.parametrize(('qualified_table','expected_name'), [
    ('sample_catalog.sample_schema.example_records','example_records'),
    ('sample_schema.example_events','example_events'),
    ('example_items','example_items'),
])
def test_databricks_table_name_survives_profile_run_and_download(api_client,monkeypatch,qualified_table,expected_name):
    def handler(config,limit):
        assert config['table']==qualified_table
        return c.ImportedData('source.json',b'[{"id":"001"}]')
    monkeypatch.setitem(c.HANDLERS,'databricks',handler)
    response=api_client.post('/api/datasets/connect',json={'kind':'databricks','configuration':{'server_hostname':'host.example','http_path':'/sql/path','token':'private-token','table':qualified_table}})
    assert response.status_code==200,response.text
    metadata=response.json()['dataset']
    assert metadata['name']==expected_name
    assert metadata['filename']==expected_name
    assert metadata['source']['table']==qualified_table
    profile=api_client.get('/api/datasets/'+metadata['dataset_id']+'/profile').json()
    assert profile['dataset']['filename']==expected_name
    run=api_client.post('/api/runs',json={'dataset_id':metadata['dataset_id'],'approved':True,'selected_rules':[{'rule_id':'DQ-CATALOG-005','configuration':{}}]}).json()
    assert run['dataset_filename']==expected_name
    assert run['outputs']['accepted']==f'{expected_name}_accepted'
    download=api_client.get('/api/runs/'+run['run_id']+'/download/accepted')
    assert f'{expected_name}_accepted.csv' in download.headers['content-disposition']
    # Previously imported fully qualified display names also normalize on refresh.
    import backend.app as api
    api.storage.update_metadata(metadata['dataset_id'],{'name':qualified_table,'filename':qualified_table})
    restored=api_client.get('/api/datasets/'+metadata['dataset_id']+'/profile').json()['dataset']
    assert restored['filename']==expected_name
    assert restored['source']['table']==qualified_table



def test_informal_schema_descriptions_match_uploaded_columns():
    from backend.schema_expectations import check
    frame=pd.DataFrame({'client_id':['001'],'contact_email':['a@example.com'],'total':[12.5]})
    result=check(frame,'I expect client id, contact email as text and total numeric')
    assert result['status']=='MATCH'
    assert [x['column'] for x in result['items']]==['client_id','contact_email','total']
    assert result['items'][1]['expected_type']=='text'


def test_schema_accepts_unformatted_lists_and_reports_missing_fields():
    from backend.schema_expectations import check
    frame=pd.DataFrame({'key':['001'],'label':['a'],'email':['a@example.com']})
    assert check(frame,'key label email')['summary']['matched']==3
    result=check(frame,'key, delivery date')
    assert result['status']=='REVIEW'
    assert result['items'][1]['column'] is None
    assert result['items'][1]['status']=='MISSING'


def test_schema_ambiguity_requires_user_mapping():
    from backend.schema_expectations import check
    frame=pd.DataFrame({'billing_email':['a@example.com'],'shipping_email':['b@example.com']})
    result=check(frame,'email')
    assert result['status']=='REVIEW'
    assert result['items'][0]['status']=='AMBIGUOUS'
    resolved=check(frame,'email',{'0':'shipping_email'})
    assert resolved['status']=='MATCH'
    assert resolved['items'][0]['column']=='shipping_email'
    with pytest.raises(ValueError):check(frame,'email',{'0':'not_a_column'})


def test_schema_detects_type_missingness_and_explicit_extra_columns():
    from backend.schema_expectations import check
    frame=pd.DataFrame({'amount':['bad','2'],'key':['', '001']})
    result=check(frame,'amount as number, key required')
    assert result['status']=='MISMATCH'
    assert result['summary']['mismatched']==2
    assert [x['invalid_records'] for x in result['items']]==[1,1]
    assert check(frame,'only key')['status']=='MISMATCH'
    assert check(frame,'key')['status']=='MATCH'


def test_schema_normalized_name_collisions_are_not_guessed():
    from backend.schema_expectations import check
    frame=pd.DataFrame({'customer_id':['1'],'customerId':['2']})
    assert check(frame,'customer id')['status']=='REVIEW'
    assert check(frame,'customer_id')['items'][0]['column']=='customer_id'


def test_schema_date_field_names_do_not_imply_type_constraints():
    from backend.schema_expectations import check
    result=check(pd.DataFrame({'signup_date':['unknown']}),'signup date')
    assert result['status']=='MATCH'
    assert result['items'][0]['expected_type'] is None
    assert check(pd.DataFrame({'signup_date':['unknown']}),'signup date as date')['status']=='MISMATCH'


def test_schema_description_endpoint_uses_current_dataset_without_baseline(api_client):
    uploaded=api_client.post('/api/datasets',files={'file':('schema.csv',b'client_id,total\n001,12\n','text/csv')}).json()
    dataset=uploaded['dataset']['dataset_id']
    response=api_client.post(f'/api/datasets/{dataset}/schema/check',json={'description':'client id and total as number'})
    assert response.status_code==200,response.text
    assert response.json()['status']=='MATCH'
    assert api_client.post(f'/api/datasets/{dataset}/schema/check',json={'description':'   '}).status_code==422
    assert api_client.post(f'/api/datasets/{dataset}/schema/check',json={'description':'total','mappings':{'0':'missing'}}).status_code==422
    import backend.app as api
    other=TestClient(api.app)
    assert other.post(f'/api/datasets/{dataset}/schema/check',json={'description':'total'}).status_code==401
