import io
import json
import zipfile
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.business_documents import extract_document, parse_batch
from backend.llm import DisabledLLMProvider


def test_multiple_points_and_unresolved_conditions():
    result = parse_batch("1. amount must be greater than 0\n- age must be at least 18\n? status must be approved", ["amount", "age", "status"], {}, DisabledLLMProvider())
    assert [(rule["column"],rule["operator"],rule["value"]) for rule in result["rules"]] == [("amount", ">", 0), ("age", ">=", 18)]
    assert result["unresolved"][0]["requirement"] == "status must be approved"
    compound = parse_batch("amount greater than 0 and less than 100", ["amount"], {}, DisabledLLMProvider())
    assert not compound["rules"] and compound["unresolved"]
    inclusive = parse_batch("amount greater than or equal to 0", ["amount"], {}, DisabledLLMProvider())
    assert inclusive["rules"][0]["operator"] == ">="


def test_read_documents_and_reject_binary_or_empty_files():
    assert extract_document("rules.any", b"amount must be greater than 0") == "amount must be greater than 0"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", '<w:document xmlns:w="word"><w:p><w:r><w:t>amount must be positive</w:t></w:r></w:p></w:document>')
    assert extract_document("rules.docx", buffer.getvalue()) == "amount must be positive"
    from pypdf import PdfWriter
    pdf = io.BytesIO(); writer = PdfWriter(); writer.add_blank_page(width=100,height=100); writer.write(pdf)
    with pytest.raises(ValueError, match="No readable text"):
        extract_document("scan.pdf", pdf.getvalue())
    for content in [b"", b"abc\x00", b"\xff\x00\xfe"]:
        with pytest.raises(ValueError):
            extract_document("rules.bin", content)


def test_ai_receives_metadata_and_rejects_unknown_columns(monkeypatch):
    import backend.business_documents as module
    payload = {}
    def post(url, **kwargs):
        payload.update(kwargs["json"])
        output = {"rules":[{"status":"PARSED","type":"comparison","column":"amount","operator":">","value":0,"original_requirement":"Amount should be positive"},{"status":"PARSED","type":"comparison","column":"invented","operator":">","value":18,"original_requirement":"Age should be adult"}],"unresolved":[]}
        return SimpleNamespace(raise_for_status=lambda:None, json=lambda:{"choices":[{"message":{"content":json.dumps(output)}}]})
    monkeypatch.setattr(module.httpx,"post",post)
    provider = SimpleNamespace(configured=True, base_url="https://example.test", api_key="test", model="test")
    result = parse_batch("Amount should be positive\nAge should be adult", ["amount","age"], {"columns":[{"name":"amount","physical_type":"int64"}]}, provider)
    assert result["mode"] == "ai" and len(result["rules"]) == 1
    assert result["unresolved"][0]["requirement"] == "Age should be adult"
    assert json.loads(payload["messages"][1]["content"])["dataset"]["columns"][0]["name"] == "amount"


def test_batch_and_document_endpoints(tmp_path, monkeypatch):
    import backend.app as module
    import backend.storage as storage
    monkeypatch.setattr(storage,"UPLOAD_DIR",tmp_path / "uploads")
    monkeypatch.setattr(storage,"RUN_DIR",tmp_path / "runs")
    storage.UPLOAD_DIR.mkdir(); storage.RUN_DIR.mkdir()
    monkeypatch.setattr(module,"llm_provider",DisabledLLMProvider())
    client = TestClient(module.app)
    client.post('/api/auth/signup',json={'first_name':'Rule','last_name':'Tester','company':'Test','country_code':'+91','phone':'9876543210','email':'batch@example.com','password':'test-password'})
    client.post('/api/auth/login',json={'email':'batch@example.com','password':'test-password'})
    dataset = client.post('/api/datasets',files={'file':('data.csv',b'amount,age\n1,18\n-1,16\n','text/csv')}).json()['dataset']['dataset_id']
    base = f'/api/datasets/{dataset}/business-rules'
    response = client.post(base+'/batch',json={'text':'1. amount greater than 0\n2. age at least 18'})
    assert response.status_code == 200 and len(response.json()['rules']) == 2
    response = client.post(base+'/document',files={'file':('rules.txt',b'amount greater than 0','text/plain')})
    assert response.status_code == 200 and len(response.json()['rules']) == 1
    assert client.post(base+'/document',files={'file':('bad.bin',b'\x00','application/octet-stream')}).status_code == 400
    rules = client.post(base+'/batch',json={'text':'amount greater than 0\nage at least 18'}).json()['rules']
    run = client.post('/api/runs',json={'dataset_id':dataset,'selected_rules':[],'business_rules':rules,'approved':True})
    assert run.status_code == 200 and run.json()['rejected_count'] == 1


ORDERS_RULES_CSV = 'Rule ID,Category,Business Rule (NLP)\nBR_001,Completeness,Every order must have a non-null and non-empty order_id.\nBR_002,Uniqueness,Each order_id must be unique across the entire dataset.\nBR_003,Completeness,Every order must have a non-null and non-empty customer_id.\nBR_004,Validity,Quantity must be a valid number greater than zero.\nBR_005,Validity,Unit price must be a valid number greater than zero.\nBR_006,Accuracy,"Total amount must equal quantity multiplied by unit price minus discount amount, with an allowed difference of 0.01."\nBR_007,Validity,"Order status must be one of PLACED, SHIPPED, DELIVERED, or CANCELLED."\nBR_008,Timeliness,Order timestamp must not be in the future.\nBR_009,Validity,Discount amount must be a valid non-negative number.\nBR_010,Consistency,"For every DELIVERED order, delivery_timestamp must be populated and must not be earlier than order_timestamp."\n'


def test_orders_csv_parses_all_ten_without_ai_and_keeps_source_ids():
    columns = ['order_id','customer_id','quantity','unit_price','total_amount','discount_amount','order_status','order_timestamp','delivery_timestamp']
    result = parse_batch(extract_document('rules.csv', ORDERS_RULES_CSV.encode()), columns, {}, DisabledLLMProvider())
    assert not result['unresolved']
    assert len(result['rules']) == 10
    assert [r['source_rule_id'] for r in result['rules']] == [f'BR_{i:03d}' for i in range(1,11)]
    assert [r['type'] for r in result['rules']] == ['not_empty','unique','not_empty','numeric_comparison','numeric_comparison','arithmetic','allowed_values','not_future','numeric_comparison','conditional_dates']


def test_orders_conditions_execute_every_part_of_compound_rules():
    import pandas as pd
    from backend.business_conditions import validate_condition
    frame = pd.DataFrame([
        dict(order_id='one',customer_id='customer',quantity=2,unit_price=10,total_amount=19,discount_amount=1,order_status='DELIVERED',order_timestamp='2020-01-01',delivery_timestamp='2020-01-02'),
        dict(order_id='two',customer_id=' ',quantity='bad',unit_price=0,total_amount=999,discount_amount=-1,order_status='UNKNOWN',order_timestamp='2999-01-01',delivery_timestamp=None),
        dict(order_id='two',customer_id='customer',quantity=2,unit_price=10,total_amount=19.01,discount_amount=1,order_status='DELIVERED',order_timestamp='2020-01-01',delivery_timestamp='2019-12-31'),
        dict(order_id=None,customer_id=None,quantity=None,unit_price='bad',total_amount=None,discount_amount='bad',order_status='DELIVERED',order_timestamp='bad',delivery_timestamp=None),
    ])
    rules = parse_batch(ORDERS_RULES_CSV,list(frame.columns),{},DisabledLLMProvider())['rules']
    expected = [{3},{1,2},{1,3},{1,3},{1,3},{1,3},{1},{1,3},{1,3},{2,3}]
    for ordinal,(rule,failed) in enumerate(zip(rules,expected),1):
        assert {item['row_index'] for item in validate_condition(frame,rule,ordinal)} == failed, rule


def test_generic_csv_names_and_missing_columns_are_not_silently_invented():
    text = 'Rule ID,Category,Business Rule (NLP)\nA,Validity,Sale price must be a valid non-negative number.\nB,Completeness,Every sale must have a non-null and non-empty sale_id.\n'
    result = parse_batch(text,['sale_price','sale_id'],{},DisabledLLMProvider())
    assert len(result['rules'])==2 and not result['unresolved']
    result = parse_batch(text,['unrelated'],{},DisabledLLMProvider())
    assert not result['rules'] and len(result['unresolved'])==2


def test_orders_rules_document_and_run_end_to_end(tmp_path, monkeypatch):
    import backend.app as module
    import backend.storage as storage
    monkeypatch.setattr(storage,'UPLOAD_DIR',tmp_path/'uploads')
    monkeypatch.setattr(storage,'RUN_DIR',tmp_path/'runs')
    storage.UPLOAD_DIR.mkdir();storage.RUN_DIR.mkdir()
    monkeypatch.setattr(module,'llm_provider',DisabledLLMProvider())
    client=TestClient(module.app)
    client.post('/api/auth/signup',json={'first_name':'Orders','last_name':'Tester','company':'Test','country_code':'+91','phone':'9876543210','email':'orders-rules@example.com','password':'test-password'})
    client.post('/api/auth/login',json={'email':'orders-rules@example.com','password':'test-password'})
    data=b'order_id,customer_id,quantity,unit_price,total_amount,discount_amount,order_status,order_timestamp,delivery_timestamp\n1,c1,2,10,19,1,DELIVERED,2020-01-01,2020-01-02\n2,c2,2,10,999,1,DELIVERED,2020-01-01,2019-12-31\n'
    dataset=client.post('/api/datasets',files={'file':('orders.csv',data,'text/csv')}).json()['dataset']['dataset_id']
    response=client.post(f'/api/datasets/{dataset}/business-rules/document',files={'file':('rules.csv',ORDERS_RULES_CSV.encode(),'text/csv')})
    assert response.status_code==200
    parsed=response.json()
    assert len(parsed['rules'])==10 and not parsed['unresolved']
    response=client.post('/api/runs',json={'dataset_id':dataset,'selected_rules':[],'business_rules':parsed['rules'],'approved':True})
    assert response.status_code==200,response.text
    run=response.json()
    assert len(run['check_results'])==10 and run['accepted_count']==1 and run['rejected_count']==1
    assert {f['rule_name'] for f in run['failures']}=={'BR_006','BR_010'}


@pytest.mark.parametrize('requirement,column,prefix', [
    ('product id should be start with format "P"','product_id','P'),
    ('customer code must start with "CUS-"','customer_code','CUS-'),
    ("sku should start with prefix 'x.'",'sku','x.'),
    ('Every Product ID must start with "P".','product_id','P'),
])
def test_literal_prefix_rules_are_dynamic(requirement,column,prefix):
    import pandas as pd
    from backend.business_conditions import validate_condition
    result=parse_batch(requirement,[column],{},DisabledLLMProvider())
    assert len(result['rules'])==1 and not result['unresolved']
    rule=result['rules'][0]
    assert rule['type']=='starts_with' and rule['prefix']==prefix and rule['column']==column
    frame=pd.DataFrame({column:[prefix+'123',prefix,None,'','other'+prefix,123]})
    assert {issue['row_index'] for issue in validate_condition(frame,rule,1)}=={2,3,4,5}


def test_prefix_case_and_literal_characters_are_preserved():
    import pandas as pd
    from backend.business_conditions import validate_condition
    rule=parse_batch('product id should be start with format "P"',['product_id'],{},DisabledLLMProvider())['rules'][0]
    assert {f['row_index'] for f in validate_condition(pd.DataFrame({'product_id':['P123','p123','123P']}),rule,1)}=={1,2}
    rule=parse_batch('sku must start with "A."',['sku'],{},DisabledLLMProvider())['rules'][0]
    assert {f['row_index'] for f in validate_condition(pd.DataFrame({'sku':['A.123','AX123']}),rule,1)}=={1}
    missing=parse_batch('product id should be start with format "P"',['unrelated'],{},DisabledLLMProvider())
    assert not missing['rules'] and len(missing['unresolved'])==1
    compound=parse_batch('product id must start with "P" and be unique',['product_id'],{},DisabledLLMProvider())
    assert not compound['rules'] and len(compound['unresolved'])==1


@pytest.mark.parametrize('text,column,kind',[
    ('qty should be positive','quantity','numeric_comparison'),
    ('quantty should be positive','quantity','numeric_comparison'),
    ('customer code must be unique','customer_id','unique'),
    ('email should be required','contact_email','not_empty'),
])
def test_vague_aliases_abbreviations_and_typos_are_grounded(text,column,kind):
    result=parse_batch(text,[column],{},DisabledLLMProvider())
    assert len(result['rules'])==1 and not result['unresolved']
    assert result['rules'][0]['column']==column and result['rules'][0]['type']==kind
    assert result['rules'][0]['original_requirement']==text


def test_ambiguous_mapping_and_missing_threshold_offer_clarification():
    result=parse_batch('price should be reasonable',['unit_price','sale_price'],{},DisabledLLMProvider())
    assert not result['rules']
    pending=result['unresolved'][0]
    assert {c['name'] for c in pending['candidate_columns']}=={'unit_price','sale_price'}
    assert pending['suggestions'][0]['intent']=='minimum'
    assert pending['suggestions'][0]['value_label']=='minimum'
    result=parse_batch('product code should start with a proper format',['product_id'],{},DisabledLLMProvider())
    assert not result['rules'] and result['unresolved'][0]['suggestions'][0]['intent']=='starts_with'
    assert result['unresolved'][0]['candidate_columns'][0]['name']=='product_id'


def test_rich_ai_rules_and_retrieved_metadata(monkeypatch):
    import backend.business_documents as module
    captured={}
    text='product identifier must use a leading P'
    def post(url,**kwargs):
        captured.update(json.loads(kwargs['json']['messages'][1]['content']))
        return SimpleNamespace(raise_for_status=lambda:None,json=lambda:{'choices':[{'message':{'content':json.dumps({'rules':[{'status':'PARSED','type':'starts_with','column':'product_id','prefix':'P','original_requirement':text}],'unresolved':[]})}}]})
    monkeypatch.setattr(module.httpx,'post',post)
    provider=SimpleNamespace(configured=True,base_url='https://test.invalid',api_key='test',model='test')
    result=parse_batch(text,['product_id'],{'columns':[{'name':'product_id','physical_type':'string'}]},provider)
    assert len(result['rules'])==1 and result['rules'][0]['type']=='starts_with'
    assert captured['retrieved_context'][text][0]['name']=='product_id'


def test_ai_cannot_invent_a_reasonable_threshold(monkeypatch):
    import backend.business_documents as module
    text='price should be reasonable'
    response={'rules':[{'status':'PARSED','type':'numeric_comparison','column':'price','operator':'<=','value':100,'original_requirement':text}],'unresolved':[]}
    monkeypatch.setattr(module.httpx,'post',lambda *a,**kw: SimpleNamespace(raise_for_status=lambda:None,json=lambda:{'choices':[{'message':{'content':json.dumps(response)}}]}))
    provider=SimpleNamespace(configured=True,base_url='https://test.invalid',api_key='test',model='test')
    result=parse_batch(text,['price'],{},provider)
    assert not result['rules'] and result['unresolved'][0]['suggestions']


@pytest.mark.parametrize('text', ['product id Should Start with P','product id Must Start with P','product_id starts with P','Product ID should be start with format P'])
def test_explicit_unquoted_prefix_needs_no_manual_work(text):
    result=parse_batch(text,['product_id','product_name'],{},DisabledLLMProvider())
    assert len(result['rules'])==1 and not result['unresolved']
    assert result['rules'][0]['column']=='product_id' and result['rules'][0]['prefix']=='P'


def test_ambiguous_prefix_has_complete_sentences_and_keeps_literal():
    result=parse_batch('Product Column Prefix starts P',['product_id','product_name','customer_id'],{},DisabledLLMProvider())
    assert not result['rules'] and len(result['unresolved'])==1
    question=result['unresolved'][0]
    assert not question['suggestions']
    assert {choice['rule']['column'] for choice in question['choices']}=={'product_id','product_name'}
    assert all(choice['rule']['prefix']=='P' for choice in question['choices'])
    assert all('starts with "P"' in choice['label'] for choice in question['choices'])
    renamed=parse_batch('Account Column Prefix starts AC-',['account_id','account_name'],{},DisabledLLMProvider())
    assert {c['rule']['column'] for c in renamed['unresolved'][0]['choices']}=={'account_id','account_name'}
    assert all(c['rule']['prefix']=='AC-' for c in renamed['unresolved'][0]['choices'])


def test_dataset_context_is_saved_on_upload_and_available_for_rules(tmp_path,monkeypatch):
    import backend.app as module
    import backend.storage as storage
    monkeypatch.setattr(storage,'UPLOAD_DIR',tmp_path/'uploads')
    monkeypatch.setattr(storage,'RUN_DIR',tmp_path/'runs')
    storage.UPLOAD_DIR.mkdir();storage.RUN_DIR.mkdir()
    monkeypatch.setattr(module,'llm_provider',DisabledLLMProvider())
    client=TestClient(module.app)
    account={'first_name':'Context','last_name':'Tester','company':'Test','country_code':'+91','phone':'9876543210','email':'context@example.com','password':'test-password'}
    client.post('/api/auth/signup',json=account);client.post('/api/auth/login',json={'email':account['email'],'password':account['password']})
    dataset=client.post('/api/datasets',files={'file':('products.csv',b'product_id,product_name\nP1,Product one\n','text/csv')}).json()['dataset']['dataset_id']
    saved=json.loads((storage.UPLOAD_DIR/f'{dataset}.json').read_text(encoding='utf-8'))
    assert saved['business_context']['version']==1
    assert {c['name'] for c in saved['business_context']['metadata']['columns']}=={'product_id','product_name'}
    result=client.post(f'/api/datasets/{dataset}/business-rules/batch',json={'text':'product id Should Start with P'}).json()
    assert result['rules'][0]['prefix']=='P' and not result['unresolved']



def test_concurrent_registry_discovery_does_not_share_partial_maps():
    from concurrent.futures import ThreadPoolExecutor
    from backend.rule_registry import RuleRegistry
    registry=RuleRegistry()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(lambda _:registry.discover(),range(12)))
    assert all(len({rule['rule_id'] for rule in result})==len(result) for result in results)


def test_typo_datatype_grounded_interpretation_and_choices():
    import pandas as pd
    from backend.business_conditions import validate_condition
    text = 'Amount column Data type should be DoubleTypppe'
    result = parse_batch(text, ['total_amount', 'product_id'], {}, DisabledLLMProvider())
    rule = result['rules'][0]
    assert (rule['type'], rule['column'], rule['expected_type']) == ('data_type', 'total_amount', 'DoubleType')
    failures = validate_condition(pd.DataFrame({'total_amount':[12.5, 'invalid', None]}), rule, 1)
    assert [item['row_index'] for item in failures] == [1, 2]
    ambiguous = parse_batch(text, ['total_amount', 'discount_amount'], {}, DisabledLLMProvider())
    assert not ambiguous['rules']
    assert {item['rule']['column'] for item in ambiguous['unresolved'][0]['choices']} == {'total_amount', 'discount_amount'}
    assert 'minimum' not in ambiguous['unresolved'][0]['clarification']
    other = parse_batch('balance column data type should be IntegerType', ['account_balance'], {}, DisabledLLMProvider())
    assert other['rules'][0]['expected_type'] == 'IntegerType'
    unknown = parse_batch('amount data type should be Unicorn', ['amount'], {}, DisabledLLMProvider())
    assert not unknown['rules']
    assert 'minimum' not in unknown['unresolved'][0]['clarification']


@pytest.mark.parametrize('text,columns,kind', [
    ('customer should be unique', ['customer_id', 'customer_account_id'], 'unique'),
    ('balance should be positive', ['opening_balance', 'closing_balance'], 'numeric_comparison'),
    ('contact should be required', ['contact_email', 'contact_phone'], 'not_empty'),
    ('account must start with AC-', ['account_id', 'account_reference_code'], 'starts_with'),
])
def test_ambiguous_rule_types_offer_dataset_grounded_choices(text, columns, kind):
    result = parse_batch(text, columns, {}, DisabledLLMProvider())
    assert not result['rules']
    choices = result['unresolved'][0]['choices']
    assert {choice['rule']['column'] for choice in choices} == set(columns)
    assert all(choice['rule']['type'] == kind for choice in choices)
    assert not result['unresolved'][0]['suggestions']


def test_unrelated_prefix_does_not_suggest_arbitrary_columns():
    result = parse_batch('spaceship must start with Z', ['amount', 'customer_id'], {}, DisabledLLMProvider())
    assert not result['rules']
    assert not result['unresolved'][0]['choices']



def test_ai_cannot_choose_between_ambiguous_columns(monkeypatch):
    import backend.business_documents as module
    text = 'customer should be unique'
    response = {'rules':[{'status':'PARSED','type':'unique','column':'customer_id','original_requirement':text}], 'unresolved':[]}
    monkeypatch.setattr(module.httpx, 'post', lambda *a, **kw: SimpleNamespace(raise_for_status=lambda:None, json=lambda:{'choices':[{'message':{'content':json.dumps(response)}}]}))
    provider = SimpleNamespace(configured=True, base_url='https://test.invalid', api_key='test', model='test')
    result = parse_batch(text, ['customer_id', 'customer_account_id'], {}, provider)
    assert not result['rules']
    assert len(result['unresolved'][0]['choices']) == 2


@pytest.mark.parametrize('text,columns,length,operator', [
    ('country column contains only 2 charcacters validate that columns only', ['country_code','shipping_country'], 2, '=='),
    ('region column must contain exactly 4 characters', ['billing_region','delivery_region'], 4, '=='),
    ('warehouse should have at most 7 chars', ['warehouse_key','warehouse_reference'], 7, '<='),
    ('branch length should be at least 3', ['branch_code','branch_name'], 3, '>='),
])
def test_dynamic_character_lengths_and_column_ambiguity(text, columns, length, operator):
    import pandas as pd
    from backend.business_conditions import validate_condition
    result = parse_batch(text, [*columns, 'unrelated'], {}, DisabledLLMProvider())
    assert not result['rules']
    choices = result['unresolved'][0]['choices']
    assert {choice['rule']['column'] for choice in choices} == set(columns)
    assert all(choice['rule']['length'] == length and choice['rule']['operator'] == operator for choice in choices)
    rule = choices[0]['rule']
    frame = pd.DataFrame({rule['column']:['x'*length, 'x'*(length+1), None, 42], 'unrelated':['bad']*4})
    failures = validate_condition(frame, rule, 1)
    expected = [1, 2, 3] if operator in {'==','<='} else [2,3]
    assert [failure['row_index'] for failure in failures] == expected
    assert {failure['column'] for failure in failures} == {rule['column']}


def test_length_clarification_keeps_original_condition():
    from backend.business_context import clarify_requirement
    original = 'region column contains only 4 characters'
    rule = clarify_requirement(original, 'use delivery_region only', ['billing_region','delivery_region'], {})
    assert rule['column'] == 'delivery_region' and rule['length'] == 4
    assert rule['original_requirement'] == original
    assert clarify_requirement(original, 'invented_region', ['billing_region','delivery_region'], {}) is None


def test_compound_length_request_is_not_silently_weakened():
    result = parse_batch('region must contain exactly 4 characters and start with A', ['region'], {}, DisabledLLMProvider())
    assert not result['rules']


def test_length_clarification_endpoint_and_selected_column_validation(tmp_path, monkeypatch):
    import backend.app as module
    import backend.storage as storage
    monkeypatch.setattr(storage, 'UPLOAD_DIR', tmp_path/'uploads')
    monkeypatch.setattr(storage, 'RUN_DIR', tmp_path/'runs')
    storage.UPLOAD_DIR.mkdir(); storage.RUN_DIR.mkdir()
    monkeypatch.setattr(module, 'llm_provider', DisabledLLMProvider())
    client = TestClient(module.app)
    client.post('/api/auth/signup', json={'first_name':'Length','last_name':'Tester','company':'Test','country_code':'+91','phone':'9876543210','email':'length@example.com','password':'test-password'})
    client.post('/api/auth/login', json={'email':'length@example.com','password':'test-password'})
    dataset = client.post('/api/datasets', files={'file':('regions.csv', b'billing_region,delivery_region,unrelated\nINVALID,ABCD,bad\nINVALID,ABC,bad\n', 'text/csv')}).json()['dataset']['dataset_id']
    saved = json.loads((storage.UPLOAD_DIR/f'{dataset}.json').read_text(encoding='utf-8'))
    documents = saved['business_context']['metadata']['retrieval_index']['documents']
    assert {doc['column'] for doc in documents} == {'billing_region','delivery_region','unrelated'}
    original = 'region column contains only 4 characters'
    base = f'/api/datasets/{dataset}/business-rules'
    response = client.post(base+'/batch', json={'text':original})
    assert response.json()['unresolved'][0]['status'] == 'NOT_APPLIED'
    assert response.json()['unresolved'][0]['choices'] == []
    reply = client.post(base+'/clarify', json={'requirement':original, 'clarification':'delivery_region'})
    assert reply.status_code == 200
    rules = reply.json()['rules']
    assert rules[0]['column'] == 'delivery_region' and rules[0]['length'] == 4
    run = client.post('/api/runs', json={'dataset_id':dataset,'selected_rules':[],'business_rules':rules,'approved':True})
    assert run.status_code == 200
    assert (run.json()['accepted_count'], run.json()['rejected_count']) == (1,1)
    rewritten = client.post(base+'/clarify', json={'requirement':original, 'clarification':'billing_region must contain at most 7 characters'})
    assert rewritten.json()['rules'][0]['column'] == 'billing_region'
    assert rewritten.json()['rules'][0]['operator'] == '<='
