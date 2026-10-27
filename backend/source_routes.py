import re
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from backend import connectors
from backend.schema_changes import compare, snapshot
from backend.storage import ACTIVE_OWNER

router = APIRouter(prefix='/api')

class ImportRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source_id: str = Field(min_length=1, max_length=80, pattern=r'^[A-Za-z0-9_-]+$')
    max_rows: int = Field(default=100000, ge=1, le=100000, strict=True)
    baseline_dataset_id: str | None = None
    renames: dict[str, str] = Field(default_factory=dict)

class ComparisonRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    baseline_dataset_id: str
    renames: dict[str, str] = Field(default_factory=dict)

@router.get('/sources')
def list_sources():
    try:
        return connectors.catalog(ACTIVE_OWNER.get())
    except connectors.ConnectorError as error:
        raise HTTPException(503, str(error)) from error

@router.post('/datasets/import')
def import_dataset(body: ImportRequest):
    return finish_import(body, lambda: connectors.import_source(body.source_id, ACTIVE_OWNER.get(), body.max_rows))

def finish_import(body, load_source):
    from backend.app import storage, _dataset_or_404, _profile_with_overrides, _recommendations
    from backend.llm import metadata_for_llm
    baseline = None
    if body.baseline_dataset_id:
        _, frame = _dataset_or_404(body.baseline_dataset_id)
        baseline = snapshot(frame)
    elif body.renames:
        raise HTTPException(422, 'Rename mappings require a baseline dataset.')
    metadata = None
    try:
        imported, provenance = load_source()
        from pathlib import Path
        table = provenance.get('table')
        table_name = table.rsplit('.', 1)[-1] if table and provenance.get('kind') == 'databricks' else table
        dataset_name = re.sub(r'[^A-Za-z0-9._-]+', '_', table_name).strip('._') if table_name else body.source_id
        dataset_name = dataset_name or body.source_id
        metadata = storage.save_dataset(dataset_name + Path(imported.filename).suffix, imported.content)
        frame = storage.load_dataset(metadata['dataset_id'])
        if frame.empty or len(frame) > body.max_rows:
            raise ValueError('Source is empty or exceeds the record limit; no partial dataset was imported.')
        schema = snapshot(frame)
        difference = compare(baseline, schema, body.renames) if baseline else None
        provenance['imported_at'] = datetime.now(timezone.utc).isoformat()
        updates = {'source': provenance, 'observed_schema': schema}
        if table:
            updates.update(name=dataset_name, filename=table_name)
        storage.update_metadata(metadata['dataset_id'], updates)
        profile = _profile_with_overrides(storage.dataset_metadata(metadata['dataset_id']), frame)
        storage.save_business_context(metadata['dataset_id'], metadata_for_llm(profile, []))
        recommendations, status = _recommendations(frame, profile)
        return {'dataset': storage.dataset_metadata(metadata['dataset_id']), 'profile': profile,
                'recommendations': recommendations, 'recommendation_status': status,
                'schema_comparison': difference, 'warnings': []}
    except FileNotFoundError as error:
        if metadata:
            storage.discard_dataset(metadata['dataset_id'])
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        if metadata:
            storage.discard_dataset(metadata['dataset_id'])
        raise HTTPException(422, str(error)) from error
    except Exception:
        if metadata:
            storage.discard_dataset(metadata['dataset_id'])
        raise HTTPException(502, 'Imported source could not be profiled. No dataset was retained.') from None

@router.get('/datasets/{dataset_id}/schema')
def dataset_schema(dataset_id: str):
    from backend.app import _dataset_or_404
    _, frame = _dataset_or_404(dataset_id)
    return snapshot(frame)

@router.post('/datasets/{dataset_id}/schema/compare')
def compare_schema(dataset_id: str, body: ComparisonRequest):
    from backend.app import _dataset_or_404
    _, frame = _dataset_or_404(dataset_id)
    _, baseline = _dataset_or_404(body.baseline_dataset_id)
    try:
        return compare(snapshot(baseline), snapshot(frame), body.renames)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@router.get('/connection-types')
def connection_types():
    from backend.connection_forms import catalog
    return catalog()


@router.post('/datasets/connect')
async def connect_dataset(request: Request):
    from starlette.concurrency import run_in_threadpool
    from backend.connection_forms import import_connection, TYPES
    # Manual validation ensures rejected request bodies never echo submitted secrets.
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > 65536:
            raise HTTPException(413, 'Connection settings exceed the request limit.')
    try:
        import json
        body = json.loads(data)
        if not isinstance(body, dict) or set(body)-{'kind','configuration','max_rows','baseline_dataset_id','renames'}:
            raise ValueError()
        kind = body.get('kind')
        if not isinstance(kind,str) or kind not in TYPES or not isinstance(body.get('configuration'),dict):
            raise ValueError()
        options = ImportRequest(source_id='connection_'+kind, max_rows=body.get('max_rows',100000), baseline_dataset_id=body.get('baseline_dataset_id'),renames=body.get('renames',{}))
    except Exception:
        raise HTTPException(422, 'Choose a connection type and provide valid connection settings.') from None
    return await run_in_threadpool(finish_import, options, lambda: import_connection(kind,body['configuration'],options.max_rows))


class SchemaExpectationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    description: str = Field(min_length=1,max_length=8000)
    mappings: dict[str,str] = Field(default_factory=dict)


@router.post('/datasets/{dataset_id}/schema/check')
def check_expected_schema(dataset_id: str, body: SchemaExpectationRequest):
    from backend.app import _dataset_or_404
    from backend.schema_expectations import check
    _, frame = _dataset_or_404(dataset_id)
    try:
        return check(frame,body.description,body.mappings)
    except ValueError as error:
        raise HTTPException(422,str(error)) from error
