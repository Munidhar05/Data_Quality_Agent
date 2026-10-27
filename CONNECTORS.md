# Connected sources and schema changes

## Connect from the UI

Beside Choose file, use the connection dropdown. Select Databricks, an API, AWS, Azure, Google Cloud or a database. Only that connection's fields appear. Enter credentials and select Connect and import. No .env source definition or account ID is needed. Credentials are sent to the local backend for that request, never saved in datasets/reports/browser storage, and cleared from the form after success, source switching or account changes. Failed requests retain the form for retry. The server still needs the optional SDKs/drivers installed.

Databricks asks for server hostname, HTTP path, access token and table (catalog.schema.table). AWS asks for user-supplied access keys and an optional session token. Azure storage uses a storage connection string. Google Cloud uses service-account JSON. Database forms collect host, database, username, password and table, with provider-specific extras.

Authenticated endpoints: GET /api/connection-types and POST /api/datasets/connect with {"kind":"databricks","configuration":{"server_hostname":"host","http_path":"/sql/...","token":"...","table":"catalog.schema.table"}}. Secrets are request-scoped; server-source environment references are not accepted by this endpoint.

The server-configured source workflow below remains available for existing integrations; it is not required for UI imports.

Install core requirements first, then only the SDKs you need or all optional connectors:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-connectors.txt
```

SQL Server requires a system ODBC driver as well as pyodbc. Database URLs use SQLAlchemy dialects: postgresql+psycopg, mysql+pymysql, mssql+pyodbc, oracle+oracledb, snowflake. Azure SQL and Synapse SQL use SQL Server; RDS uses its database dialect. Redshift also has a direct Data API adapter that does not require a SQLAlchemy dialect. Use read-only database accounts; generated SELECT statements do not make a write-capable account safe.

## Server configuration

Set DQ_SOURCES_JSON in the server environment or .env. It maps source IDs to definitions. Example (one line in .env):

```dotenv
DQ_SOURCES_JSON='{"orders_api":{"kind":"rest","label":"Orders API","owner_ids":["1"],"config":{"url":"https://api.example.com/orders","token_env":"DQ_SOURCE_API_TOKEN","records_path":"data","pagination":"page","page_parameter":"page","size_parameter":"limit","page_size":1000,"max_pages":100}},"warehouse":{"kind":"database","owner_ids":["1"],"config":{"url_env":"DQ_SOURCE_DATABASE_URL","schema":"public","table":"orders"}}}'
DQ_SOURCE_API_TOKEN=replace-on-server
DQ_SOURCE_DATABASE_URL=postgresql+psycopg://reader:password@host:5432/database
```

owner_ids are account ID strings. Omission explicitly shares a source with every logged-in account; an empty list grants nobody access. Only the server administrator configures endpoints, tables and credentials. The legacy /datasets/import endpoint accepts configured source IDs; UI connections use /datasets/connect with transient credentials. Arbitrary SQL and server credential references are not accepted by the UI endpoint. Use trusted definitions. Credentials are never returned or stored in browser drafts, dataset provenance or reports. Keep .env private and avoid secrets in labels/IDs.

| Kind | Required config | Optional config/auth |
|---|---|---|
| rest | url (HTTPS) | token_env; api_key_env + api_key_header; params; format (default json); records_path |
| s3 | bucket, key | region; AWS credential chain / IAM role |
| athena | database, table | region, catalog, workgroup, output_location, query_timeout_seconds |
| redshift | database, table; one of workgroup_name or cluster_identifier | region, schema, db_user, secret_arn_env, query_timeout_seconds |
| azure_blob | account_url, container, key | DefaultAzureCredential; connection_string_env replaces account_url |
| adls | account_url, container (filesystem), key | DefaultAzureCredential; connection_string_env replaces account_url |
| gcs | bucket, key | project; Google Application Default Credentials |
| bigquery | table (project.dataset.table) | project; Google Application Default Credentials |
| databricks | server_hostname, http_path, table, token_env | table may be catalog.schema.table for Unity Catalog |
| database | url_env, table | schema, connect_args (driver-specific TLS/timeouts) |

Credential references must name server variables beginning DQ_SOURCE_. AWS/Google/Azure identities use standard SDK credential chains. Object storage imports one explicit object in any supported upload format; it does not scan folders. ADLS uses the DFS endpoint, Blob the Blob endpoint. Databricks reads through a SQL Warehouse. No external resources are provisioned or modified.

REST supports JSON arrays, nested arrays via dotted records_path, or supported file responses via format (csv, parquet, xlsx, etc.). Pagination: none, page, offset, next_url. Page/offset stop on an empty page, not a short page. Optional start defaults to 1 for page and 0 for offset. Configure page_parameter, size_parameter, page_size and max_pages. next_url requires next_path containing the next URL or null. URLs must stay on the same HTTPS origin. Redirects fail. GET retries 429/502/503/504 twice. Cursor-token pagination and OAuth refresh are not included; refresh credentials on the server.

## Use

Upload page -> choose a connection beside Choose file -> enter its details -> Connect and import. Existing checks, business rules and exports then apply. Legacy configured-source API:

```http
GET /api/sources
POST /api/datasets/import
{"source_id":"orders_api","max_rows":100000,"baseline_dataset_id":"optional-previous-dataset-id"}
```

Imports enforce 50 MB and 100,000-row limits and fail rather than silently retaining samples. Pagination must finish before the page cap. Empty sources fail. Table imports fetch a max+1 sentinel to detect overflow. BigQuery uses table-data listing, not a billed SQL query; views require a materialized/exported table. Configure driver/server timeouts for databases. Processing remains synchronous and local, not distributed Spark execution. Reads are not guaranteed transactionally consistent while a live source changes; use immutable objects or source-managed snapshots where needed.

## Check schema in plain language

In File Details, use Check your schema. Describe expected fields in ordinary words, e.g. "client ID, contact email as text, and total as a number". No baseline ID, JSON or fixed input format is required. Commas, newlines, short lists and simple sentences are supported. Matching handles common naming variations and spelling similarity against this dataset's actual column names. It does not require an AI provider.

Results show the expected field, matched column and any stated type/required-value mismatches. When several columns could match, select the intended column and check again. Uncertain or missing matches remain for review. Extra columns are allowed unless you explicitly say "only" or "exactly". A field called "signup date" does not itself impose a date-type constraint; say "signup date as date" to check its values. This is a bounded interpreter, so unclear expectations may need a clearer description or a manual column choice.

Authenticated API: POST /api/datasets/{id}/schema/check with {"description":"client ID and total as number","mappings":{}}. Matching is against the current owned dataset. Existing baseline-comparison APIs remain available to integrations; baseline IDs are not required or shown in the UI.

## Verification

Tests simulate API/cloud SDK clients; SQLAlchemy tests use a real temporary SQLite database when installed. Live account connectivity, IAM, firewall, quotas and provider drivers must be verified with your accounts.

Run connector tests with `.venv/Scripts/python.exe -B -m pytest -q tests/test_connectors.py`. Run the browser flow with `.venv/Scripts/python.exe -B tests/browser_sources.py`; it uses a temporary local server and simulated source data.

## AWS query services

Athena and Redshift Data API use the standard AWS credential chain. Both issue a generated SELECT with a max+1 row sentinel, wait for completion, and page results. Queries time out after 120 seconds by default (configurable 1-600); cancellation is requested on timeout. Raw provider errors are not returned. No arbitrary SQL is accepted.

Athena requires an existing workgroup with a query-result location, or output_location configured as an S3 URI. Queries can incur AWS charges and write query-result objects to that location; they do not modify the source table. LIMIT bounds returned records, not bytes scanned: configure workgroup scan/cost limits. IAM must permit query execution and reading its result objects. Redshift Data API must be enabled and authorized for the configured workgroup/cluster and database. Optional secret_arn_env references a DQ_SOURCE_ variable containing an existing Secrets Manager ARN.

Examples of config objects within a server source definition:

```json
{"kind":"athena","owner_ids":["1"],"config":{"region":"us-east-1","database":"analytics","table":"orders","workgroup":"quality-imports"}}
```

```json
{"kind":"redshift","owner_ids":["1"],"config":{"region":"us-east-1","database":"analytics","schema":"public","table":"orders","workgroup_name":"quality-warehouse"}}
```

SDK references: [Databricks SQL](https://docs.databricks.com/aws/en/dev-tools/python-sql-connector), [SQLAlchemy reflection](https://docs.sqlalchemy.org/en/20/core/reflection.html), [Azure downloads](https://learn.microsoft.com/uk-ua/azure/storage/blobs/storage-blob-download-python), [Athena results](https://docs.aws.amazon.com/boto3/latest/reference/services/athena/client/get_query_results.html), [Redshift Data API](https://docs.aws.amazon.com/boto3/latest/reference/services/redshift-data.html).

### Dependency compatibility

Use the SQLAlchemy 2.0 series constrained in requirements-connectors.txt. The Snowflake dialect imports ORM APIs removed in SQLAlchemy 2.1; installing 2.1 can fail at import despite pip check succeeding. See the [Snowflake compatibility notes](https://github.com/snowflakedb/snowflake-sqlalchemy/releases). Stop the development server before installing packages, then restart so it loads the updated environment.
