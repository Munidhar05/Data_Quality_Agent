"""User-entered, request-scoped connections; no environment or file persistence."""
import json
from urllib.parse import urlsplit
from backend import connectors as c


def f(key, label, required=True, secret=False, **options):
    return dict(key=key,label=label,required=required,secret=secret,**options)

AWS = [f('region','AWS region'),f('access_key_id','Access key ID',secret=True),f('secret_access_key','Secret access key',secret=True),f('session_token','Session token',False,True)]
OBJECT = [f('bucket','Bucket'),f('key','Object path')]
TYPES = {
 'databricks':dict(label='Databricks',group='Platforms',fields=[f('server_hostname','Server hostname',placeholder='your-workspace.cloud.databricks.com'),f('http_path','HTTP path',placeholder='/sql/1.0/warehouses/...'),f('token','Access token',secret=True),f('table','Table',placeholder='catalog.schema.table')]),
 'rest':dict(label='REST API',group='APIs',fields=[f('url','API URL',placeholder='https://api.example.com/records'),f('auth','Authentication',options=['None','Bearer token','API key']),f('token','Bearer token',secret=True,show_when={'auth':'Bearer token'}),f('api_key_header','API key header',show_when={'auth':'API key'}),f('api_key','API key',secret=True,show_when={'auth':'API key'}),f('records_path','Records path',False,placeholder='data.records (leave empty for a JSON array)'),f('pagination','Pagination',options=['none','page','offset','next_url']),f('next_path','Next URL path',show_when={'pagination':'next_url'}),f('params','Query parameters (JSON)',False,multiline=True,placeholder='{"limit":100}')]),
 's3':dict(label='Amazon S3',group='AWS',fields=AWS+OBJECT),
 'athena':dict(label='Amazon Athena',group='AWS',fields=AWS+[f('database','Database'),f('table','Table'),f('workgroup','Workgroup',False),f('output_location','Query results S3 location',False,placeholder='s3://bucket/results/')]),
 'redshift':dict(label='Amazon Redshift',group='AWS',fields=AWS+[f('database','Database'),f('table','Table'),f('schema','Schema',False),f('workgroup_name','Serverless workgroup',False),f('cluster_identifier','Cluster identifier',False),f('db_user','Database user',False)]),
 'azure_blob':dict(label='Azure Blob Storage',group='Azure',fields=[f('connection_string','Storage connection string',secret=True),f('container','Container'),f('key','Blob path')]),
 'adls':dict(label='Azure Data Lake Gen2',group='Azure',fields=[f('connection_string','Storage connection string',secret=True),f('container','Filesystem'),f('key','File path')]),
 'gcs':dict(label='Google Cloud Storage',group='Google Cloud',fields=[f('project','Project ID'),f('service_account_json','Service account JSON',secret=True,multiline=True)]+OBJECT),
 'bigquery':dict(label='Google BigQuery',group='Google Cloud',fields=[f('project','Project ID'),f('service_account_json','Service account JSON',secret=True,multiline=True),f('table','Table',placeholder='project.dataset.table')]),
}
DATABASES = {'postgresql':('PostgreSQL','postgresql+psycopg',5432),'mysql':('MySQL','mysql+pymysql',3306),'mssql':('SQL Server','mssql+pyodbc',1433),'azure_sql':('Azure SQL','mssql+pyodbc',1433),'synapse':('Azure Synapse SQL','mssql+pyodbc',1433),'snowflake':('Snowflake','snowflake',None),'oracle':('Oracle','oracle+oracledb',1521)}
for kind,(label,driver,port) in DATABASES.items():
    fields=[f('host','Account identifier' if kind=='snowflake' else 'Server host'),f('port','Port',False,type='number',default=port),f('database','Service name' if kind=='oracle' else 'Database'),f('username','Username'),f('password','Password',secret=True),f('table','Table'),f('schema','Schema',False)]
    if kind=='snowflake':fields.append(f('warehouse','Warehouse'))
    if driver=='mssql+pyodbc':fields.append(f('odbc_driver','ODBC driver',default='ODBC Driver 18 for SQL Server'))
    TYPES[kind]=dict(label=label,group='Databases',fields=fields)


def catalog():
    return [dict(id=key,**value) for key,value in TYPES.items()]


def import_connection(kind, values, max_rows):
    if kind not in TYPES or not isinstance(values,dict):
        raise c.ConnectorError('Choose a supported connection type.')
    config, secrets = {}, {}
    definitions=TYPES[kind]['fields']
    if set(values)-{x['key'] for x in definitions}:
        raise c.ConnectorError('Unsupported connection fields.')
    for item in definitions:
        key=item['key']
        condition=item.get('show_when',{})
        if condition and any(values.get(k)!=v for k,v in condition.items()):continue
        value=values.get(key,item.get('default'))
        if value is None or value=='':
            if item['required']:raise c.ConnectorError('Enter '+item['label']+'.')
            continue
        if not isinstance(value,(str,int)) or isinstance(value,bool) or len(str(value))>32000:
            raise c.ConnectorError('Invalid '+item['label']+'.')
        if 'options' in item and value not in item['options']:
            raise c.ConnectorError('Invalid '+item['label']+'.')
        config[key]=value if item['secret'] else str(value).strip()
    if type(max_rows) is not int or not 1<=max_rows<=c.MAX_ROWS:raise c.ConnectorError('Invalid record limit.')
    for key in ('token','api_key','connection_string'):
        if key in config:
            reference='DQ_SOURCE_REQUEST_'+key.upper()
            secrets[reference]=config.pop(key)
            config[key+'_env']=reference
    if kind=='databricks':
        if '://' in config['server_hostname'] or '/' in config['server_hostname']:raise c.ConnectorError('Enter the hostname without https:// or a path.')
        if not config['http_path'].startswith('/'):raise c.ConnectorError('HTTP path must start with /.')
    if kind=='rest':
        host=urlsplit(config['url']).hostname
        if host in {'169.254.169.254','169.254.170.2','metadata.google.internal'}:raise c.ConnectorError('Cloud metadata endpoints cannot be imported.')
        config.pop('auth',None)
        if 'params' in config:
            try:config['params']=json.loads(config['params'])
            except ValueError:raise c.ConnectorError('Query parameters must be a JSON object.') from None
            if not isinstance(config['params'],dict):raise c.ConnectorError('Query parameters must be a JSON object.')
    aws={}
    if kind in {'s3','athena','redshift'}:
        aws={'aws_access_key_id':config.pop('access_key_id'),'aws_secret_access_key':config.pop('secret_access_key')}
        if config.get('session_token'):aws['aws_session_token']=config.pop('session_token')
    google=config.pop('service_account_json',None)
    handler_kind=kind
    if kind in DATABASES:
        sa=c.sdk('sqlalchemy','SQLAlchemy')
        driver=DATABASES[kind][1]
        try:port=int(config['port']) if config.get('port') else None
        except (ValueError,TypeError):raise c.ConnectorError('Enter a valid port.') from None
        if port is not None and not 1<=port<=65535:raise c.ConnectorError('Port must be between 1 and 65535.')
        query={}
        if driver=='mssql+pyodbc':query={'driver':config['odbc_driver'],'Encrypt':'yes'}
        database=config['database']
        if kind=='oracle':query['service_name']=database;database=None
        if kind=='snowflake':
            if config.get('schema'):database+='/'+config['schema']
            query['warehouse']=config['warehouse']
        url=sa.engine.URL.create(driver,username=config['username'],password=config['password'],host=config['host'],port=port,database=database,query=query)
        secrets['DQ_SOURCE_REQUEST_URL']=url.render_as_string(hide_password=False)
        config={'url_env':'DQ_SOURCE_REQUEST_URL','table':config['table'],**({'schema':config['schema']} if config.get('schema') else {})}
        handler_kind='database'
    token=c.REQUEST_CONNECTION.set({'secrets':secrets,'aws':aws,'google':google})
    try:
        result=c.HANDLERS[handler_kind](config,max_rows)
        if not result.content or len(result.content)>c.MAX_UPLOAD_BYTES:raise c.ConnectorError('Source is empty or exceeds the import limit.')
        provenance={'kind':kind,'connection_mode':'user_entered'}
        if config.get('table'):provenance['table']=config['table']
        return result,provenance
    except c.ConnectorError:raise
    except Exception:
        raise c.ConnectorError('Connection failed. Check credentials, permissions, source details and network access.') from None
    finally:
        c.REQUEST_CONNECTION.reset(token)
