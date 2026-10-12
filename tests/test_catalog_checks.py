import copy
import json
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from backend.app import app
from backend.rules import discover_rules
from backend.rule_plugins import catalog_validation as catalog


@pytest.fixture
def sample():
    return pd.DataFrame({'id':[1,2], 'amount':[10,20], 'name':['alice','bob'], 'email':['a@example.com','b@example.com'], 'status':['active','inactive'], 'old_status':['pending','active'], 'date':['2026-10-01','2026-10-02'], 'code':['AB123','CD456'], 'payload':['{"id":1,"tags":[]}','{"id":2,"tags":[]}'], 'country':['IN','US'], 'parent_id':[1,2], 'quantity':[1,2], 'price':[10,10], 'total':[10,20], 'converted':[11,22], 'start':['2026-10-01T00:00:00Z','2026-10-02T00:00:00Z'], 'end':['2026-10-01T01:00:00Z','2026-10-02T01:00:00Z'], 'updated_at':['2026-10-08T12:00:00Z']*2, 'created_at':['2026-01-01']*2, 'event_time':['2026-10-01T00:00:00Z']*2, 'ingested_at':['2026-10-01T00:00:30Z']*2, 'processed_at':['2026-10-01T00:00:40Z']*2, 'unit':['kg']*2, 'phone':['+919876543210']*2, 'postcode':['500001']*2, 'batch':['batch-1','batch-2'], 'partition':['2026-10-01','2026-10-02'], 'category':['a','b'], 'notes':['hello','world']})


@pytest.mark.parametrize('number', range(1,20))
def test_every_replacement_check_executes(number,sample,monkeypatch):
    monkeypatch.setattr(catalog,'_reference',lambda parameters:sample.copy())
    plugin=catalog.RULE_PLUGINS[number-1]
    parameters=copy.deepcopy(catalog.EXAMPLES[number])
    if 'reference_dataset_id' in parameters:parameters['reference_dataset_id']='reference'
    result=plugin.validate(sample,{'parameters':parameters})
    assert isinstance(result,list)
    assert plugin.get_metadata()['catalog_number']==number
    for finding in result:
        assert finding['rule_id']==f'DQ-CATALOG-{number:03d}'
        assert finding['status']=='FAIL'
    if parameters:
        with pytest.raises(ValueError):plugin.validate(sample,{'parameters':{}})


def test_replacement_registry_is_exactly_19():
    rules=[r for r in discover_rules() if 'catalog_number' in r]
    assert len(rules)==19
    assert {r['catalog_number'] for r in rules}==set(range(1,20))
    assert len(catalog.CATALOG)==7


def test_pass_fail_and_mandatory_duplicate(sample):
    null=catalog.RULE_PLUGINS[2]
    assert not null.validate(sample,{'parameters':{'columns':['id']}})
    bad=sample.copy();bad.loc[1,'id']=None
    assert null.validate(bad,{'parameters':{'columns':['id']}})[0]['row_index']==1
    assert catalog.RULE_PLUGINS[4].validate(pd.DataFrame({'id':[1,1]}),{})[0]['row_index']==1
    assert catalog.RULE_PLUGINS[13].validate(sample,{'parameters':{'expected':3}})
    assert not catalog.RULE_PLUGINS[13].validate(sample,{'parameters':{'expected':2}})
    assert catalog.RULE_PLUGINS[13].validate(sample.iloc[:0],{'parameters':{'expected':2}})[0]['row_index']=='DATASET'


def test_replacement_reference_api(tmp_path,monkeypatch):
    import backend.storage as storage
    monkeypatch.setattr(storage,'UPLOAD_DIR',tmp_path/'uploads');storage.UPLOAD_DIR.mkdir()
    monkeypatch.setattr(storage,'RUN_DIR',tmp_path/'runs');storage.RUN_DIR.mkdir()
    client=TestClient(app)
    account={'first_name':'Catalog','last_name':'Tester','company':'Feuji','country_code':'+91','phone':'9876543210','email':'catalog@example.com','password':'test-password'}
    assert client.post('/api/auth/signup',json=account).status_code==201
    assert client.post('/api/auth/login',json={'email':account['email'],'password':account['password']}).status_code==200
    data=client.post('/api/datasets',files={'file':('data.csv',b'id,amount\n1,10\n2,20\n','text/csv')}).json()
    ref=client.post('/api/datasets',files={'file':('reference.csv',b'id\n1\n','text/csv')}).json()
    response=client.post('/api/runs',json={'dataset_id':data['dataset']['dataset_id'],'approved':True,'selected_rules':[{'rule_id':'DQ-CATALOG-010','configuration':{'parameters':{'column':'id','reference_column':'id','reference_dataset_id':ref['dataset']['dataset_id']}}}]})
    assert response.status_code==200,response.text
    run=response.json();assert run['accepted_count']==1 and run['rejected_count']==1
    assert run['check_results'][0]['status']=='FAIL'


def test_file_based_suggestions_do_not_invent_business_expectations():
    profile={'columns':[{'name':'customer_key','pyspark_type':'LongType','null_count':0,'semantic_candidates':[{'type':'IDENTIFIER','confidence':0.99}],'statistics':{'mean':2}}, {'name':'sales','pyspark_type':'DoubleType','null_count':3,'semantic_candidates':[],'statistics':{'mean':20}}]}
    assert catalog.RULE_PLUGINS[2].generate_configuration(profile)['parameters']['columns']==['sales']
    assert catalog.RULE_PLUGINS[5].generate_configuration(profile)['parameters']['columns']==['customer_key']
    assert catalog.RULE_PLUGINS[15].generate_configuration(profile)['parameters']['column']=='sales'
    assert catalog.RULE_PLUGINS[6].generate_configuration(profile)['parameters']['maximum'] is None
    assert catalog.RULE_PLUGINS[13].check_applicability(profile)['requires_user_input']
    assert not catalog.RULE_PLUGINS[2].check_applicability(profile)['requires_user_input']
