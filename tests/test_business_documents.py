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
