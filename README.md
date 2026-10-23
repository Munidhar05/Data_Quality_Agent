# Feuji Data Quality Agent

A metadata-driven MVP for local CSV and Parquet profiling, explainable rule recommendations, human-approved deterministic validation, and accepted/rejected outputs. It does not require a known table, business domain, schema, or column names.

## Run on Windows

From this project directory, in PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn backend.app:app --reload
```

Open <http://127.0.0.1:8000>. The API docs are at <http://127.0.0.1:8000/docs>.

To stop the server, press `Ctrl+C` in the terminal. Run the tests with:

```powershell
python -m pytest -q
```

The first screen accepts CSV, Parquet, Excel (.xlsx/.xls/.xlsm), ODS, TSV, PSV, delimited TXT, JSON, JSONL/NDJSON, flat XML, Feather/Arrow, ORC or Avro files (50 MB maximum). Optional example inputs live in `datasets/demo/`: customers, orders, products, transactions, and employees. They are plain files and receive no dataset-specific treatment.

## What It Does

1. Reads a supported tabular file upload and dynamically profiles rows, columns, missingness, cardinality, duplicates, distributions, type-specific statistics, frequent values, and text patterns.
2. Ranks semantic candidates from value evidence. Uncertain meanings are shown as unknown; a user can supply and save an explicit meaning.
3. Discovers validation metadata from `backend/rule_registry.json` and Python rule plug-ins. Applicability evidence and confidence are displayed for review.
4. Interprets natural-language Business Rules offline against uploaded schema metadata. Ambiguous, incomplete, missing, conflicting, or unsupported requirements receive a Not applied notice without clarification questions.
5. Runs only after the user approves configured rules. The Pandas engine deterministically separates accepted and rejected rows, preserves original columns, and appends result metadata.
6. Persists local run reports, rule versions, selected configuration, approval, profile evidence, score formula, and downloadable accepted/rejected CSVs under `datasets/`.

## Rules and Plugins

Ten standard rules are declared as registry metadata: completeness, uniqueness, duplicate rows, evidence-backed email format, user-defined numeric bounds, approved values, reference membership, cross-column comparisons, regular-expression match, and type consistency. A generic minimum-text-length rule demonstrates plug-in discovery.

To add a Python rule, create a module in `backend/rule_plugins/` and expose a `RULE_PLUGINS` list. Each plugin implements `get_metadata()`, `check_applicability(profile)`, `generate_configuration(profile)`, `validate(data, configuration)`, and `explain_failure(result)`. Configuration descriptors can define UI controls. The registry discovers local modules at application startup; packaged rules may also register in the `feuji_dq.rules` entry-point group. Give every rule a unique ID and version, then restart the app. The API, review flow, results page, and report do not need rule-specific edits.

Rules in `backend/rule_registry.json` use built-in deterministic predicates and can be configured without a Python plug-in. Never rely on an AI response as executable rule logic.

## AI recommendations with local fallback

The app runs without an LLM credential. In that mode no profile leaves the process; recommendations come from deterministic profiling evidence.

To use Gemini, add the following to your local `.env` (which is ignored by Git), then restart the server:

```dotenv
DQ_LLM_PROVIDER=gemini
GEMINI_API_KEY=replace-with-your-key
GEMINI_MODEL=gemini-3.8-flash
```

Valid AI responses supply applicability, confidence, candidate columns, reasons and evidence. Each recommendation has a `recommendation_source` of `AI` or `RULE_BASED`. Missing or invalid entries fall back individually to Python rules; timeout, authentication, quota or provider failures fall back entirely. API profile responses include `recommendation_status`: `OK`, `PARTIAL`, `INVALID_RESPONSE`, `UNAVAILABLE` or `NOT_CONFIGURED`. Required user configuration and approval remain enforced. Set `DQ_LLM_PROVIDER=offline` to disable external requests.

To enable an OpenAI-compatible chat-completions endpoint, set these environment variables before starting the server:

```powershell
$env:DQ_LLM_API_KEY = "your-provider-key"
$env:DQ_LLM_BASE_URL = "https://api.openai.com/v1"
$env:DQ_LLM_MODEL = "gpt-4o-mini"
python -m uvicorn backend.app:app --reload
```

Only structured profile metadata and rule definitions are sent. Raw rows, samples, top values, numeric/date minima and maxima are excluded. Provider output must be valid JSON referencing known rule IDs and uploaded columns; it supplies recommendations after validation against the local rule registry. A provider timeout or malformed response leaves local rules available. The LLM never decides PASS/FAIL.

## MVP Boundaries

- Local Pandas processing; uploads are limited to 50 MB. The execution and storage interfaces allow future engines and storage backends, but Spark, database, cloud, and scheduled execution are not implemented.
- The natural-language rule parser intentionally handles a narrow set of unambiguous comparisons. It asks for clarification when it cannot safely resolve a requirement; it is not a general-purpose natural-language compiler.
- Audit and datasets are local files with account ownership checks, but are not encrypted or designed for shared-workspace collaboration. Do not expose this development server to untrusted networks or upload sensitive data without appropriate deployment controls.
- The score is the equally weighted average of represented quality categories; rules within each category are severity-weighted by record pass rate. The UI displays the full formula.
- AI is optional and metadata-only. No AI is required for profiling or validation.


## Data quality catalog

The application offers 19 executable tests in 7 categories. Add checks in Choose Checks and configure explicit expectations. Reference comparisons and distribution drift accept a reference upload in any supported data format. Dataset-level violations reject every record; row-level violations reject affected records. Configure IQR for outlier detection and histogram bins plus maximum total variation distance for distribution drift. Volume anomalies compare record counts with a supplied historical count baseline. Schema and type validation check required fields and type-convertible values; source-to-target reconciliation compares configured keys, values, and record counts. Business Rule Validation applies a conditional comparison.

See [all 19 configuration examples](backend/catalog-checks.md). Legacy backend rule IDs remain available for old stored runs, but the user-facing catalog and picker show the replacement 19 tests only.

Business Rules supports multiline points and document uploads up to 10 MB (text, PDF, DOCX, XLSX, PPTX and ODT). Scanned documents need text recognition before upload. Generate rules processes up to 100 points together. With a configured provider, requirement text and structured dataset metadata are sent for interpretation; raw dataset rows are excluded. Each upload builds a local retrieval index of column names, inferred semantic types, and profile metadata. Business-rule interpretation retrieves relevant column documents from that index; it does not train a model or require an embedding service. Local interpretation supports comparisons, prefixes, character lengths, data types, completeness, uniqueness, approved values, dates, and supported formulas. Ambiguous columns produce complete selectable rule suggestions. A free-text clarification can select a column while preserving the original condition, or supply a complete revised rule. Validation applies only reviewed rules and their selected columns; missing thresholds are never inferred from dataset statistics.


## Account access and administration

Uploads, profiles, reference files, runs and accepted/rejected downloads belong to the account that created them. History lists only that account's runs. Files and reports created before ownership tracking was added are retained on disk but inaccessible through the account APIs; re-upload the original file and run validation again. Do not guess ownership or assign legacy files to whoever signs in first.

Administration at `/admin` requires an explicitly configured `DQ_ADMIN_PASSWORD`. Without it the admin login is unavailable. Set a private password before starting the server, for example:

```powershell
$env:DQ_ADMIN_PASSWORD = "replace-with-your-private-admin-password"
python -m uvicorn backend.app:app --reload
```

Users who forget their password should contact the workspace administrator. The administrator can set a replacement password through Edit user; existing sessions are revoked when an account is updated. There is no email-based self-service reset in this local application.

## Values, drafts and reports

CSV record browsing and accepted/rejected downloads preserve original cell text, including leading zeros, decimal formatting, empty fields, and literal `NA`, `NULL`, `null` and `NaN`. Validation derives numeric and boolean values separately. Empty CSV fields are missing for validation; text null tokens are literal values. Identifier, phone, postal-code, category and description meanings keep the validation representation as text. CSV output formatting/quoting can differ from the original file. Parquet output still uses CSV serialization for typed values.

If an input column has the same name as result metadata (`dq_status`, `failed_rules`, `failure_reasons`, `run_id`, `validated_at`), the metadata uses the next free suffix, such as `dq_status_2`. `output_metadata_columns` in the JSON report identifies the generated columns. Source columns are preserved.

Check configurations, reference filenames, business-rule text and generated rules are saved per account and dataset in this browser tab's session storage. They survive refresh and navigation within the tab; closing the tab can discard unfinished drafts. A message identifies save failures, and leaving during an unsaved or active request triggers the browser's leave-page warning.

Business-rule text still in the editor is pending, not part of the validation run. While text is pending, the final action reads `Generate & review rules`: clicking it interprets the text and stops for review. Successfully generated requirements move to Rules ready for review. Unresolved requirements remain in the editor and must be corrected or explicitly removed; this also applies to uploaded business-rule documents. When the editor is clear, `Run validation` submits the reviewed rules.

Results can restore the corresponding dataset and reviewed checks after refresh or from History. Failed-record details paginate 100 issues at a time and display matching totals; the backend's stored audit sample is still limited to 5,000 issues. Rejected CSVs include every record's failure reasons. Results displays the quality score and its formula. The PDF action opens Print / Save as PDF.

## Browser regression audit

The optional browser audit uses Playwright with installed Microsoft Edge. It starts a temporary local server with isolated accounts/storage and disables external AI. It covers signup/login, drafts, schema/reference editing, validation, exports, account isolation, keyboard focus/current-page labels, mobile overflow and administrator create/edit/delete.

```powershell
python -m pip install playwright
python tests/browser_audit.py
python tests/browser_missteps.py
```

Run the API and data regression suite with `python -m pytest -q`. Browser testing requires permission to launch Edge and write temporary test files.


The adversarial browser audit injects failed and delayed requests and exercises skipped steps, repeated clicks, pending document requirements, stale draft/report state, reference replacements, download identity, session recovery and administrator save errors. It checks that retries use retained inputs and that success is reported only after the requested action succeeds.

If a business document fails to upload or parse, validation is blocked until the user retries or explicitly chooses Continue without this document. Viewing a report preserves current draft work; Review this run's checks explicitly restores that report's dataset/settings. Edited output names belong to their specific report, and never carry over to a different upload.

### File input behavior

Excel/ODS imports the first worksheet and displays its name. Export other worksheets separately. TXT requires a detectable comma, semicolon, tab or pipe delimiter. JSON accepts record objects, arrays of objects or a single wrapper containing a record array; nested objects become dotted columns and arrays remain JSON text. XML accepts flat repeating records and rejects nested/ambiguous fields and unsafe entities. Additional formats are normalized into the existing validation pipeline; accepted/rejected downloads remain CSV. Spreadsheet display formatting is not retained. Database, API, cloud and streaming connectors are deferred pending approval.

## Offline Business Rules

Enter ordinary sentences, one per line, or upload a business-rule document. No API key, billing, or fixed CSV template is required for Business Rules. Existing CSV rule descriptions and source IDs are also supported.

The offline interpreter supports required and unique fields, types, numeric comparisons and ranges, approved text values, prefixes, suffixes, text containment, date checks, supported arithmetic formulas, AND/OR clauses, and if/then conditions. It recognizes common wording variations and written numbers. Each interpreted rule includes its source requirement and actual schema column names, and validation runs deterministically.

This is a bounded offline interpreter, so arbitrary natural language is not guaranteed to be understood. Missing limits, equally plausible columns, contradictory ranges, and unsupported conditions are marked Not applied; no values or policies are invented. Edit the original sentence or exclude the notice before validation. Optional AI advice for general profiling is separate from Business Rules and retains its existing configuration.

### Business Rules chat

With Gemini configured, pasted/uploaded Business Rules are interpreted using the Google GenAI chat SDK. The clarification endpoint uses the original requirement as chat history and the clarification as the next message. Chats are scoped to one request; no user or dataset shares conversation state. Only column names and user-provided requirement text are sent for this feature (requirements may contain your own literals). Install updated requirements and restart the backend. Responses must compile completely with the local parser; failed, empty or unsupported responses fall back to Python. Results use `mode: ai_chat` and rule `interpretation_source: AI` when successful. Review the resulting conditions before approving deterministic execution.
