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

The first screen accepts any CSV or Parquet file. Optional example inputs live in `datasets/demo/`: customers, orders, products, transactions, and employees. They are plain files and receive no dataset-specific treatment.

## What It Does

1. Reads a CSV or Parquet upload and dynamically profiles rows, columns, missingness, cardinality, duplicates, distributions, type-specific statistics, frequent values, and text patterns.
2. Ranks semantic candidates from value evidence. Uncertain meanings are shown as unknown; a user can supply and save an explicit meaning.
3. Discovers validation metadata from `backend/rule_registry.json` and Python rule plug-ins. Applicability evidence and confidence are displayed for review.
4. Parses a small, conservative set of explicit comparisons. An ambiguous or unsupported request returns a clarification instead of creating an executable rule.
5. Runs only after the user approves configured rules. The Pandas engine deterministically separates accepted and rejected rows, preserves original columns, and appends result metadata.
6. Persists local run reports, rule versions, selected configuration, approval, profile evidence, score formula, and downloadable accepted/rejected CSVs under `datasets/`.

## Rules and Plugins

Ten standard rules are declared as registry metadata: completeness, uniqueness, duplicate rows, evidence-backed email format, user-defined numeric bounds, approved values, reference membership, cross-column comparisons, regular-expression match, and type consistency. A generic minimum-text-length rule demonstrates plug-in discovery.

To add a Python rule, create a module in `backend/rule_plugins/` and expose a `RULE_PLUGINS` list. Each plugin implements `get_metadata()`, `check_applicability(profile)`, `generate_configuration(profile)`, `validate(data, configuration)`, and `explain_failure(result)`. Configuration descriptors can define UI controls. The registry discovers local modules at application startup; packaged rules may also register in the `feuji_dq.rules` entry-point group. Give every rule a unique ID and version, then restart the app. The API, review flow, results page, and report do not need rule-specific edits.

Rules in `backend/rule_registry.json` use built-in deterministic predicates and can be configured without a Python plug-in. Never rely on an AI response as executable rule logic.

## Optional AI Advice

The app runs without an LLM credential. In that mode no profile leaves the process; recommendations come from deterministic profiling evidence.

To enable an OpenAI-compatible chat-completions endpoint, set these environment variables before starting the server:

```powershell
$env:DQ_LLM_API_KEY = "your-provider-key"
$env:DQ_LLM_BASE_URL = "https://api.openai.com/v1"
$env:DQ_LLM_MODEL = "gpt-4o-mini"
python -m uvicorn backend.app:app --reload
```

Only structured profile metadata and rule definitions are sent. Raw rows, samples, top values, numeric/date minima and maxima are excluded. Provider output must be valid JSON referencing known rule IDs and uploaded columns; it is shown as advisory alongside deterministic evidence. A provider timeout or malformed response leaves local rules available. The LLM never decides PASS/FAIL.

## MVP Boundaries

- Local Pandas processing; uploads are limited to 50 MB. The execution and storage interfaces allow future engines and storage backends, but Spark, database, cloud, and scheduled execution are not implemented.
- The natural-language rule parser intentionally handles a narrow set of unambiguous comparisons. It asks for clarification when it cannot safely resolve a requirement; it is not a general-purpose natural-language compiler.
- Audit and datasets are local files, not encrypted or multi-user storage. Do not expose this development server to untrusted networks or upload sensitive data without appropriate deployment controls.
- The score is the equally weighted average of represented quality categories; rules within each category are severity-weighted by record pass rate. The UI displays the full formula.
- AI is optional and metadata-only. No AI is required for profiling or validation.


## Data quality catalog

The application offers 19 executable tests in 7 categories. Add checks in Choose Checks and configure explicit expectations. Reference comparisons and distribution drift accept a reference CSV or Parquet upload. Dataset-level violations reject every record; row-level violations reject affected records. Configure IQR for outlier detection and histogram bins plus maximum total variation distance for distribution drift. Volume anomalies compare record counts with a supplied historical count baseline. Schema and type validation check required fields and type-convertible values; source-to-target reconciliation compares configured keys, values, and record counts. Business Rule Validation applies a conditional comparison.

See [all 19 configuration examples](backend/catalog-checks.md). Legacy backend rule IDs remain available for old stored runs, but the user-facing catalog and picker show the replacement 19 tests only.

Business Rules supports multiline points and document uploads up to 10 MB (text, PDF, DOCX, XLSX, PPTX and ODT). Scanned documents need text recognition before upload. Generate rules processes up to 100 points together. With a configured provider, requirement text and structured dataset metadata are sent for interpretation; raw dataset rows are excluded. Without a provider, supported comparisons use the local parser. Only reviewed numeric or cross-column comparisons are executable; unsupported and ambiguous conditions are displayed for clarification.
