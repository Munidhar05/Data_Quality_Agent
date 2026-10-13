import json
from types import SimpleNamespace
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from backend.business_offline import compile_rule, parse_offline_batch
from backend.business_offline_execution import validate_extended
from backend.llm import DisabledLLMProvider


@pytest.fixture(autouse=True)
def offline_provider(monkeypatch):
    import backend.app as module
    monkeypatch.setattr(module,"llm_provider",DisabledLLMProvider())


def execute(frame,rule):
    from backend.app import _validate_business_rule
    return _validate_business_rule(frame,rule,1)


@pytest.mark.parametrize("text,column,kind",[
    ("Product must be starts with P","product_id","starts_with"),
    ("Please ensure quantity should be positive","quantity","numeric_comparison"),
    ("No missing values in account_code","account_code","not_empty"),
    ("No duplicate entries in customer_id","customer_id","unique"),
    ("balance has to be at least 10","balance","numeric_comparison"),
    ("balance >= 10","balance","numeric_comparison"),
    ("quantity must be between 1 and 100","quantity","compound"),
    ("amount must be at most twenty five","amount","numeric_comparison"),
    ("sku must end with '-US'","sku","text_match"),
    ("label must contain 'east and west'","label","text_match"),
    ("status must be ACTIVE","status","literal_comparison"),
    ("age must be a whole number","age","data_type"),
    ("product code must be unique","product_id","unique"),
])
def test_natural_wording_is_interpreted_offline(text,column,kind):
    result=parse_offline_batch(text,[column],{})
    assert result["mode"]=="offline" and not result["unresolved"]
    assert result["rules"][0]["type"]==kind and result["rules"][0]["evidence"]


def test_ranges_and_boolean_conditions_execute_every_clause():
    frame=pd.DataFrame({"amount":[0,5,10,11,None]})
    for text in ["amount must be between 1 and 10","amount must be greater than 0 and at most 10"]:
        rule=parse_offline_batch(text,list(frame.columns),{})["rules"][0]
        assert {failure["row_index"] for failure in execute(frame,rule)}=={0,3,4}
    rule=parse_offline_batch("amount must be below 1 or above 10",list(frame.columns),{})["rules"][0]
    assert {failure["row_index"] for failure in execute(frame,rule)}=={1,2,4}


def test_exclusive_range_preserves_endpoints():
    frame=pd.DataFrame({"value":[1,2,3]})
    rule=parse_offline_batch("value must be between 1 and 3 exclusive",["value"],{})["rules"][0]
    assert {failure["row_index"] for failure in execute(frame,rule)}=={0,2}


def test_conditional_rules_execute_only_when_the_condition_applies():
    frame=pd.DataFrame({"status":["ACTIVE","CLOSED","ACTIVE"],"quantity":[1,0,0]})
    result=parse_offline_batch("If status equals ACTIVE then quantity must be greater than zero",list(frame.columns),{})
    assert not result["unresolved"]
    assert {failure["row_index"] for failure in execute(frame,result["rules"][0])}=={2}


def test_literal_comparisons_and_text_fragments_are_case_sensitive():
    frame=pd.DataFrame({"code":["P-US","p-US",None]})
    rule=parse_offline_batch("code must start with P and end with '-US'",["code"],{})["rules"][0]
    assert {failure["row_index"] for failure in execute(frame,rule)}=={1,2}
    rule=parse_offline_batch("code must equal 'P-US'",["code"],{})["rules"][0]
    assert {failure["row_index"] for failure in execute(frame,rule)}=={1,2}


def test_lists_preserve_literal_case_and_comparison_inclusivity():
    frame=pd.DataFrame({"status":["ACTIVE","active","PAUSED"]})
    rule=parse_offline_batch("status must be one of ACTIVE, PAUSED, or CLOSED",["status"],{})["rules"][0]
    assert rule["values"]==["ACTIVE","PAUSED","CLOSED"]
    assert {failure["row_index"] for failure in execute(frame,rule)}=={1}
    rule=parse_offline_batch("amount must be greater than or equal to 3 and less than or equal to 7",["amount"],{})["rules"][0]
    assert rule["rules"][0]["operator"]==">=" and rule["rules"][1]["operator"]=="<="


def test_quoted_and_or_and_written_number_field_names_are_preserved():
    rule=parse_offline_batch("description must contain 'one and two'",["description"],{})["rules"][0]
    assert rule["value"]=="one and two"
    rule=parse_offline_batch("one must be at least twenty five",["one"],{})["rules"][0]
    assert rule["column"]=="one" and rule["value"]==25


@pytest.mark.parametrize("text,columns,classification",[
    ("Product must start with P",["product_id","product_name"],"ambiguous"),
    ("price must be reasonable",["price"],"incomplete"),
    ("spaceship must start with Z",["amount","customer_id"],"missing"),
    ("amount must be between 10 and 1",["amount"],"conflicting"),
    ("amount must be greater than 0 and follow our internal policy",["amount"],"unsupported"),
])
def test_unclear_rules_are_not_applied_and_do_not_ask_questions(text,columns,classification):
    result=parse_offline_batch(text,columns,{})
    assert not result["rules"]
    item=result["unresolved"][0]
    assert item["status"]=="NOT_APPLIED" and item["classification"]==classification
    assert "?" not in item["reason"] and not item["choices"] and not item["suggestions"]


def test_multisentence_text_and_csv_source_metadata_are_preserved():
    result=parse_offline_batch("code must start with P. quantity must be at least 1.",["code","quantity"],{})
    assert len(result["rules"])==2
    result=parse_offline_batch('Rule ID,Category,Business Rule (NLP)\nX1,Validity,"status must be one of ACTIVE, PAUSED"',["status"],{})
    assert result["rules"][0]["source_rule_id"]=="X1"


@pytest.mark.parametrize("rule",[
    {"type":"compound","mode":[],"rules":[]},
    {"type":"compound","mode":"all","rules":[{"type":"unique","column":"invented"},{"type":"unique","column":"code"}]},
    {"type":"literal_comparison","column":"code","operator":[],"value":"P"},
    {"type":"text_match","column":"code","match":"eval","value":"x"},
])
def test_invalid_extended_rules_are_rejected(rule):
    with pytest.raises(ValueError): execute(pd.DataFrame({"code":["P"]}),{**rule,"original_requirement":"test"})


def test_nested_rule_complexity_is_bounded():
    rule={"type":"unique","column":"code"}
    for _ in range(8): rule={"type":"compound","mode":"all","rules":[rule,{"type":"not_empty","column":"code"}]}
    with pytest.raises(ValueError): execute(pd.DataFrame({"code":["P"]}),rule)


@pytest.fixture
def client_dataset(tmp_path,monkeypatch):
    import backend.app as module
    import backend.storage as storage
    monkeypatch.setattr(storage,"UPLOAD_DIR",tmp_path/"uploads")
    monkeypatch.setattr(storage,"RUN_DIR",tmp_path/"runs")
    storage.UPLOAD_DIR.mkdir(); storage.RUN_DIR.mkdir()
    client=TestClient(module.app)
    account={"first_name":"Offline","last_name":"Rules","company":"Test","country_code":"+91","phone":"9876543210","email":"offline@example.com","password":"test-password"}
    client.post("/api/auth/signup",json=account)
    client.post("/api/auth/login",json={"email":account["email"],"password":account["password"]})
    dataset=client.post("/api/datasets",files={"file":("items.csv",b"product_id,quantity\nP1,2\nX1,0\n","text/csv")}).json()["dataset"]["dataset_id"]
    return client,dataset,module


def test_business_apis_never_call_a_configured_external_provider(client_dataset,monkeypatch):
    client,dataset,module=client_dataset
    monkeypatch.setattr(module,"llm_provider",SimpleNamespace(configured=True,api_key="unusable",base_url="https://test.invalid",model="test"))
    def fail(*args,**kwargs): raise AssertionError("Business Rules must not call an external API")
    monkeypatch.setattr("httpx.post",fail)
    base=f"/api/datasets/{dataset}/business-rules"
    response=client.post(base+"/batch",json={"text":"Product must be starts with P\nquantity must be between 1 and 10"})
    assert response.status_code==200 and response.json()["mode"]=="offline" and len(response.json()["rules"])==2
    assert client.post(base+"/parse",json={"requirement":"quantity must be greater than zero"}).json()["status"]=="PARSED"
    assert client.post(base+"/document",files={"file":("rules.txt",b"product_id must start with P","text/plain")}).status_code==200
    run=client.post("/api/runs",json={"dataset_id":dataset,"business_rules":response.json()["rules"],"approved":True})
    assert run.status_code==200 and run.json()["accepted_count"]==1 and run.json()["rejected_count"]==1


def test_unsupported_api_rule_is_reported_without_a_question(client_dataset):
    client,dataset,_=client_dataset
    result=client.post(f"/api/datasets/{dataset}/business-rules/parse",json={"requirement":"quantity must be reasonable"}).json()
    assert result["status"]=="NOT_APPLIED" and "?" not in result["clarification"]
