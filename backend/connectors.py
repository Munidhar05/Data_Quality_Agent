"""Bounded imports using server sources or request-scoped user credentials."""
from __future__ import annotations
import importlib
from contextvars import ContextVar
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import urljoin, urlsplit
import httpx
from backend.file_inputs import record_text
from backend.storage import MAX_UPLOAD_BYTES

REQUEST_CONNECTION = ContextVar('request_connection', default=None)

MAX_ROWS = 100_000
TIMEOUT = 30

class ConnectorError(ValueError):
    pass

@dataclass
class ImportedData:
    filename: str
    content: bytes

def sdk(module, package):
    try:
        return importlib.import_module(module)
    except ImportError:
        raise ConnectorError(f'Install optional dependency {package} from requirements-connectors.txt.') from None

def required(config, key):
    value = config.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConnectorError(f'Source configuration requires {key}.')
    return value

def secret(config, key):
    name = required(config, key)
    if not name.startswith('DQ_SOURCE_') or not re.fullmatch(r'[A-Z0-9_]+', name):
        raise ConnectorError('Credential environment references must start with DQ_SOURCE_.')
    transient = REQUEST_CONNECTION.get() or {}
    value = transient.get('secrets', {}).get(name) or os.environ.get(name)
    if not value:
        raise ConnectorError('A required source credential is not configured on the server.')
    return value

def sources(owner):
    try:
        definitions = json.loads(os.environ.get('DQ_SOURCES_JSON', '{}'))
    except (ValueError, TypeError):
        raise ConnectorError('DQ_SOURCES_JSON must be a JSON object.') from None
    if not isinstance(definitions, dict):
        raise ConnectorError('DQ_SOURCES_JSON must be a JSON object.')
    result = {}
    for source_id, source in definitions.items():
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', source_id) or not isinstance(source, dict):
            raise ConnectorError('Invalid source definition.')
        owners = source.get('owner_ids')
        if owners is not None and (not isinstance(owners, list) or not all(isinstance(x, str) for x in owners)):
            raise ConnectorError('Source owner_ids must be account ID strings.')
        if owners is not None and str(owner) not in owners:
            continue
        if source.get('kind') not in HANDLERS or not isinstance(source.get('config'), dict):
            raise ConnectorError('Unknown source kind or missing config.')
        result[source_id] = source
    return result

def catalog(owner):
    return [{'id': key, 'label': str(value.get('label', key)), 'kind': value['kind']}
            for key, value in sources(owner).items()]

def bounded(chunks):
    content = bytearray()
    for chunk in chunks:
        content.extend(chunk)
        if len(content) > MAX_UPLOAD_BYTES:
            raise ConnectorError('Source exceeds the 50 MB import limit. Select a smaller extract.')
    return bytes(content)

def records_file(rows, max_rows):
    data = bytearray(b'[')
    count = 0
    for row in rows:
        if count >= max_rows:
            raise ConnectorError(f'Source exceeds the {max_rows} record limit; no partial dataset was imported.')
        if not isinstance(row, dict):
            raise ConnectorError('The API must return record objects.')
        data.extend(((',' if count else '') + json.dumps(record_text(row), ensure_ascii=False, default=str)).encode('utf-8'))
        if len(data) > MAX_UPLOAD_BYTES - 1:
            raise ConnectorError('Source exceeds the 50 MB import limit.')
        count += 1
    if not count:
        raise ConnectorError('Source returned no records.')
    data.extend(b']')
    return ImportedData('source.json', bytes(data))

def field(value, path):
    if not path:
        return value
    for part in path.split('.'):
        if not isinstance(value, dict) or part not in value:
            raise ConnectorError('Configured JSON response path does not exist.')
        value = value[part]
    return value

def origin(url):
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ConnectorError('REST sources require HTTPS without embedded credentials or fragments.')
    return parsed.scheme, parsed.hostname.lower(), parsed.port or 443

def rest(config, max_rows):
    url = required(config, 'url')
    initial_origin = origin(url)
    headers = {'Accept': 'application/json'}
    if config.get('token_env'):
        headers['Authorization'] = 'Bearer ' + secret(config, 'token_env')
    if config.get('api_key_env'):
        header = required(config, 'api_key_header')
        if header.lower() in {'host', 'cookie', 'connection', 'content-length'}:
            raise ConnectorError('Unsupported API key header.')
        headers[header] = secret(config, 'api_key_env')
    params = config.get('params', {})
    if not isinstance(params, dict):
        raise ConnectorError('REST params must be an object.')
    params = dict(params)
    pagination = config.get('pagination', 'none')
    if pagination not in {'none', 'page', 'offset', 'next_url'}:
        raise ConnectorError('Unsupported REST pagination mode.')
    max_pages, page_size = config.get('max_pages', 100), config.get('page_size', 1000)
    start = config.get('start', 1 if pagination == 'page' else 0)
    if type(max_pages) is not int or not 1 <= max_pages <= 1000 or type(page_size) is not int or not 1 <= page_size <= 10000 or type(start) is not int or start < 0:
        raise ConnectorError('Invalid pagination limits.')
    seen, all_rows, total_bytes = set(), [], 0
    with httpx.Client(timeout=TIMEOUT, follow_redirects=False, trust_env=False) as client:
        for page in range(max_pages):
            if origin(url) != initial_origin:
                raise ConnectorError('Pagination cannot send credentials to a different origin.')
            if pagination in {'page', 'offset'}:
                params[config.get('page_parameter', 'page' if pagination == 'page' else 'offset')] = start + page * (1 if pagination == 'page' else page_size)
                params[config.get('size_parameter', 'limit')] = page_size
            request_key = str(httpx.URL(url, params=params))
            if request_key in seen:
                raise ConnectorError('Pagination repeated a URL; no partial import was saved.')
            seen.add(request_key)
            for attempt in range(3):
                with client.stream('GET', url, params=params, headers=headers) as response:
                    if response.status_code in {429, 502, 503, 504} and attempt < 2:
                        time.sleep(0.25 * 2 ** attempt)
                        continue
                    if response.status_code != 200:
                        raise ConnectorError(f'Source returned HTTP {response.status_code}; check permissions and source configuration.')
                    content = bounded(response.iter_bytes())
                break
            total_bytes += len(content)
            if total_bytes > MAX_UPLOAD_BYTES:
                raise ConnectorError('Combined API pages exceed the 50 MB import limit.')
            file_format = config.get('format', 'json')
            if file_format != 'json':
                if pagination != 'none':
                    raise ConnectorError('Pagination requires JSON record responses.')
                return ImportedData('source.' + file_format, content)
            try:
                payload = json.loads(content)
            except ValueError:
                raise ConnectorError('Source returned invalid JSON.') from None
            records = field(payload, config.get('records_path', ''))
            if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
                raise ConnectorError('Set records_path to a JSON array of record objects.')
            all_rows.extend(records)
            if len(all_rows) > max_rows:
                raise ConnectorError('Record limit exceeded; no partial import was saved.')
            if pagination == 'none':
                break
            if pagination == 'next_url':
                next_url = field(payload, required(config, 'next_path'))
                if not next_url:
                    break
                if not isinstance(next_url, str):
                    raise ConnectorError('The next-page URL must be a string or null.')
                url, params = urljoin(url, next_url), {}
            elif not records:
                break
        else:
            raise ConnectorError('Page limit reached before completion; no partial import was saved.')
    return records_file(all_rows, max_rows)

def s3(config, max_rows):
    boto3 = sdk('boto3', 'boto3')
    client = boto3.client('s3', **(REQUEST_CONNECTION.get() or {}).get('aws', {}), region_name=config.get('region'), config=sdk('botocore.config', 'boto3').Config(connect_timeout=TIMEOUT, read_timeout=TIMEOUT, retries={'max_attempts': 2}))
    try:
        response = client.get_object(Bucket=required(config, 'bucket'), Key=required(config, 'key'))
        body = response['Body']
        try:
            content = bounded(iter(lambda: body.read(65536), b''))
        finally:
            body.close()
        return ImportedData(PurePosixPath(config['key']).name, content)
    finally:
        client.close()

def azure(config, max_rows):
    from contextlib import ExitStack
    with ExitStack() as stack:
        credential = None
        if not config.get('connection_string_env'):
            credential = sdk('azure.identity', 'azure-identity').DefaultAzureCredential()
            stack.callback(credential.close)
        if config.get('_adls'):
            cls = sdk('azure.storage.filedatalake', 'azure-storage-file-datalake').DataLakeServiceClient
            client = cls.from_connection_string(secret(config, 'connection_string_env')) if config.get('connection_string_env') else cls(required(config, 'account_url'), credential=credential)
            stack.callback(client.close)
            file = client.get_file_system_client(required(config, 'container')).get_file_client(required(config, 'key'))
            stream = file.download_file(connection_timeout=TIMEOUT, read_timeout=TIMEOUT)
        else:
            cls = sdk('azure.storage.blob', 'azure-storage-blob').BlobServiceClient
            client = cls.from_connection_string(secret(config, 'connection_string_env')) if config.get('connection_string_env') else cls(required(config, 'account_url'), credential=credential)
            stack.callback(client.close)
            stream = client.get_blob_client(required(config, 'container'), required(config, 'key')).download_blob(connection_timeout=TIMEOUT, read_timeout=TIMEOUT)
        return ImportedData(PurePosixPath(config['key']).name, bounded(stream.chunks()))

def adls(config, max_rows):
    return azure({**config, '_adls': True}, max_rows)

def google_credentials():
    raw = (REQUEST_CONNECTION.get() or {}).get('google')
    if not raw:
        return {}
    try:
        info = json.loads(raw)
        cls = sdk('google.oauth2.service_account', 'google-auth').Credentials
        return {'credentials': cls.from_service_account_info(info, scopes=['https://www.googleapis.com/auth/cloud-platform'])}
    except ConnectorError:
        raise
    except Exception:
        raise ConnectorError('Enter a valid service account JSON credential.') from None


def gcs(config, max_rows):
    client = sdk('google.cloud.storage', 'google-cloud-storage').Client(project=config.get('project'), **google_credentials())
    try:
        blob = client.bucket(required(config, 'bucket')).blob(required(config, 'key'))
        blob.reload(timeout=TIMEOUT)
        if blob.size is None or blob.size > MAX_UPLOAD_BYTES:
            raise ConnectorError('Cloud object exceeds the 50 MB import limit.')
        content = blob.download_as_bytes(if_generation_match=blob.generation, timeout=TIMEOUT)
        return ImportedData(PurePosixPath(config['key']).name, bounded([content]))
    finally:
        client.close()

def database(config, max_rows):
    sa = sdk('sqlalchemy', 'SQLAlchemy')
    engine = sa.create_engine(secret(config, 'url_env'), pool_pre_ping=True, connect_args=config.get('connect_args', {}))
    try:
        with engine.connect() as connection:
            table = sa.Table(required(config, 'table'), sa.MetaData(), schema=config.get('schema'), autoload_with=connection, resolve_fks=False)
            result = connection.execution_options(stream_results=True).execute(sa.select(table).limit(max_rows + 1))
            try:
                return records_file((dict(row) for row in result.mappings()), max_rows)
            finally:
                result.close()
    finally:
        engine.dispose()

def databricks(config, max_rows):
    sql = sdk('databricks.sql', 'databricks-sql-connector')
    parts = required(config, 'table').split('.')
    if not 1 <= len(parts) <= 3 or any(not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', part) for part in parts):
        raise ConnectorError('Databricks table requires simple table, schema.table or catalog.schema.table identifiers.')
    statement = 'SELECT * FROM ' + '.'.join('`' + part + '`' for part in parts) + f' LIMIT {max_rows + 1}'
    with sql.connect(server_hostname=required(config, 'server_hostname'), http_path=required(config, 'http_path'), access_token=secret(config, 'token_env')) as connection:
        with connection.cursor() as cursor:
            cursor.execute(statement)
            columns = [column[0] for column in cursor.description]
            def rows():
                while True:
                    batch = cursor.fetchmany(1000)
                    if not batch:
                        return
                    for row in batch:
                        yield dict(zip(columns, row))
            return records_file(rows(), max_rows)

def bigquery(config, max_rows):
    bq = sdk('google.cloud.bigquery', 'google-cloud-bigquery')
    client = bq.Client(project=config.get('project'), **google_credentials())
    try:
        table = client.get_table(required(config, 'table'), timeout=TIMEOUT)
        if table.num_rows > max_rows:
            raise ConnectorError('BigQuery table exceeds the record limit. Configure a smaller extract.')
        rows = client.list_rows(table, max_results=max_rows + 1, page_size=1000, timeout=TIMEOUT)
        return records_file((dict(row.items()) for row in rows), max_rows)
    finally:
        client.close()


def aws_query(config, max_rows, service):
    """Athena/Redshift Data API SELECTs, bounded polling and paged results."""
    boto3 = sdk('boto3', 'boto3')
    table = required(config, 'table')
    parts = ([config['schema']] if config.get('schema') else []) + [table]
    if any(not isinstance(x, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', x) for x in parts):
        raise ConnectorError('Table/schema names must be simple identifiers.')
    statement = 'SELECT * FROM ' + '.'.join('"' + x + '"' for x in parts) + f' LIMIT {max_rows + 1}'
    timeout = config.get('query_timeout_seconds', 120)
    if type(timeout) is not int or not 1 <= timeout <= 600:
        raise ConnectorError('Query timeout must be between 1 and 600 seconds.')
    client = boto3.client(service, **(REQUEST_CONNECTION.get() or {}).get('aws', {}), region_name=config.get('region'), config=sdk('botocore.config', 'boto3').Config(connect_timeout=TIMEOUT,read_timeout=TIMEOUT,retries={'max_attempts':2}))
    query_id, finished = None, False
    deadline = time.monotonic() + timeout
    try:
        if service == 'athena':
            context = {'Database':required(config,'database')}
            if config.get('catalog'):
                context['Catalog'] = config['catalog']
            arguments = {'QueryString':statement,'QueryExecutionContext':context}
            if config.get('workgroup'):
                arguments['WorkGroup'] = config['workgroup']
            if config.get('output_location'):
                arguments['ResultConfiguration'] = {'OutputLocation':config['output_location']}
            query_id = client.start_query_execution(**arguments)['QueryExecutionId']
        else:
            arguments = {'Sql':statement,'Database':required(config,'database')}
            workgroup, cluster = config.get('workgroup_name'), config.get('cluster_identifier')
            if bool(workgroup) == bool(cluster):
                raise ConnectorError('Configure exactly one Redshift workgroup_name or cluster_identifier.')
            arguments['WorkgroupName' if workgroup else 'ClusterIdentifier'] = workgroup or cluster
            if config.get('db_user'):
                arguments['DbUser'] = config['db_user']
            if config.get('secret_arn_env'):
                arguments['SecretArn'] = secret(config,'secret_arn_env')
            query_id = client.execute_statement(**arguments)['Id']
        while time.monotonic() < deadline:
            if service == 'athena':
                status = client.get_query_execution(QueryExecutionId=query_id)['QueryExecution']['Status']['State']
            else:
                status = client.describe_statement(Id=query_id)['Status']
            if status in {'SUCCEEDED','FINISHED'}:
                finished = True
                break
            if status in {'FAILED','CANCELLED','ABORTED'}:
                finished = True
                raise ConnectorError('Source query failed or was cancelled; check source permissions and configuration.')
            time.sleep(.5)
        if not finished:
            raise ConnectorError('Source query timed out; cancellation was requested.')
        def rows():
            token, seen, first = None, set(), True
            columns = None
            while True:
                if time.monotonic() >= deadline:
                    raise ConnectorError('Query result retrieval timed out; no partial import was saved.')
                arguments = {'QueryExecutionId':query_id,'MaxResults':1000} if service == 'athena' else {'Id':query_id}
                if token:
                    arguments['NextToken'] = token
                result = client.get_query_results(**arguments) if service == 'athena' else client.get_statement_result(**arguments)
                metadata = result['ResultSet']['ResultSetMetadata']['ColumnInfo'] if service == 'athena' else result['ColumnMetadata']
                names = [x['Name' if service == 'athena' else 'name'] for x in metadata]
                if len(set(names)) != len(names) or columns is not None and names != columns:
                    raise ConnectorError('Source returned duplicate or changing result columns.')
                columns = names
                batch = result['ResultSet']['Rows'] if service == 'athena' else result['Records']
                if service == 'athena' and first:
                    batch = batch[1:]  # Athena SELECT result header occurs on the first page only.
                first = False
                for record in batch:
                    cells = record['Data'] if service == 'athena' else record
                    if len(cells) != len(columns):
                        raise ConnectorError('Source returned an inconsistent record width.')
                    if service == 'athena':
                        values = [cell.get('VarCharValue') for cell in cells]
                    else:
                        values = [None if cell.get('isNull') else next(iter(cell.values()),None) for cell in cells]
                    yield dict(zip(columns,values))
                token = result.get('NextToken')
                if not token:
                    return
                if token in seen:
                    raise ConnectorError('Source repeated a result page token.')
                seen.add(token)
        return records_file(rows(),max_rows)
    finally:
        if query_id and not finished:
            try:
                if service == 'athena':
                    client.stop_query_execution(QueryExecutionId=query_id)
                else:
                    client.cancel_statement(Id=query_id)
            except Exception:
                pass  # Cleanup must not hide the original failure or leak SDK messages.
        client.close()


def athena(config,max_rows):
    return aws_query(config,max_rows,'athena')


def redshift(config,max_rows):
    return aws_query(config,max_rows,'redshift-data')

HANDLERS = {'rest': rest, 's3': s3, 'azure_blob': azure, 'adls': adls, 'gcs': gcs, 'database': database, 'databricks': databricks, 'bigquery': bigquery, 'athena': athena, 'redshift': redshift}

def import_source(source_id, owner, max_rows=MAX_ROWS):
    source = sources(owner).get(source_id)
    if source is None:
        raise FileNotFoundError('Source is not available to your account.')
    if type(max_rows) is not int or not 1 <= max_rows <= MAX_ROWS:
        raise ConnectorError('Record limit must be between 1 and 100000.')
    try:
        result = HANDLERS[source['kind']](source['config'], max_rows)
        if not result.content or len(result.content) > MAX_UPLOAD_BYTES:
            raise ConnectorError('Source is empty or exceeds the 50 MB import limit.')
        provenance = {'source_id': source_id, 'kind': source['kind']}
        if source['config'].get('table'):
            provenance['table'] = source['config']['table']
        return result, provenance
    except ConnectorError:
        raise
    except Exception:
        raise ConnectorError('Source import failed. Check server credentials, permissions, driver installation, and source availability.') from None
