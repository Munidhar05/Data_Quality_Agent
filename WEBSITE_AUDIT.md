# Website audit - current resolution status, 13 October 2026

**All 12 original findings and the reported skipped-business-rule workflow have been fixed.** The original observations at the end are historical evidence, not outstanding defects. Browser and device coverage limits are recorded separately; they are not known application errors.

## File-source expansion - 13 October 2026

Added file-only inputs using the existing upload flow: CSV, Parquet/PQ, Excel XLSX/XLS/XLSM, ODS, TSV, PSV, delimited TXT, JSON, JSONL/NDJSON, flat XML, Feather/Arrow, ORC and Avro. The same formats are available for reference comparisons. Uploads remain limited to 50 MB; normalized input must also fit that limit.

Excel/ODS imports the first worksheet and shows its name. JSON object fields flatten into dotted columns; arrays remain JSON text. XML requires flat, unique record fields and rejects unsafe entities. Spreadsheet display formatting is not retained, and accepted/rejected outputs remain CSV. Database, API, cloud-storage and streaming connectors are deferred pending manager approval.

Verification: **99 automated tests passed**, one existing dependency deprecation warning. Both browser audits passed without application JavaScript errors. New browser coverage includes Excel, JSON, TSV, XML and Avro uploads and refresh; API tests cover validation and original identifiers in both outputs. Import tests exercise 17 format extensions, nested JSON, long numeric identifiers, invalid files and unsafe XML. Legacy XLS uses the installed xlrd reader but does not have a dedicated binary fixture in this suite. Required readers were installed in the local virtual environment; deployments must install the updated requirements and restart the server.

## Latest follow-up - 13 October 2026

- Fixed an additional AI-response failure: invalid rule IDs, applicability values, column references, recommendation containers and boolean confidence values are ignored instead of crashing check selection.
- Added regression coverage for malformed AI suggestions, provider timeouts, HTTP failures, invalid JSON and incorrect response shapes. These tests use simulated provider responses; no external account or API key is needed.
- Full automated suite: **68 passed**, one existing third-party Starlette/AnyIO deprecation warning.
- Repeated `tests/browser_missteps.py`: all listed error-path scenarios passed in headless Edge with no application JavaScript errors, including pending business rules, failure/retry, double submissions, reference replacement, draft preservation and administrator saves.
- The sections below retain the verification results from the earlier fixes. The historical findings are explicitly marked resolved.

## Second workflow audit - 13 October 2026

This pass tested user missteps and error paths, following the missed type-then-validate issue. It did not assume that users complete the intended sequence.

Additional gaps fixed:

- Failed business-document uploads now block validation until retried or explicitly excluded. The failed filename and reason survive refresh. Unresolved points from a readable document remain pending in the editor.
- Dataset, rule and business-input controls are locked while generation, reference upload, semantic saving or validation is running. Original disabled states are restored, so mandatory checks remain mandatory and inputs can be retried after failure.
- Opening a report from History or a Results link no longer replaces the active file's unfinished review draft. The explicit Review this run's checks action restores that run's file and settings.
- New uploads clear stale Results controls and cannot inherit a previous report's output names. Edited download names are saved per account/run in this tab and survive navigation and refresh. Downloads capture their run and name before waiting for the response.
- Failed reference replacements explain that the previous reference is still in use. Unsupported replacements do not change the stored reference.
- Slow History/report restoration and semantic changes cannot overwrite another navigation action. Stale History responses are ignored.
- Logout failures display a toast on the current page. Expired download sessions return to login with the Results link preserved.
- Admin forms and modal dismissal are locked during save/delete; Escape cannot dismiss a pending mutation. Errors leave inputs available for correction/retry. Admin session expiry shows a sign-in explanation.
- Switching accounts resets the upload heading. Long filenames/error text wrap on narrow screens, and arithmetic rule descriptions use multiplication/subtraction symbols.

Verification completed:

- API/data suite: **58 passed**, one third-party deprecation warning.
- `tests/browser_audit.py`: passed the existing normal-flow, refresh, export, accessibility-state, mobile-overflow, account-isolation and administrator regression checks.
- `tests/browser_missteps.py`: passed incorrect-login recovery; unsupported/empty upload recovery without losing the current dataset; missing settings blocking Next; unchecked checks excluded from the chosen workflow; rule-generation failure/retry; rapid repeated clicks; editing after generation; request-time control locks; failed/mixed business-document handling; validation failure/retry; preservation of pending work while opening history; new-file output names; in-flight download names; delayed semantic saves; failed/unsupported reference replacements; History/record retries; visible failed logout; expired-session download recovery; and admin save failure, Escape handling and retry.
- Both browser audits completed in headless Edge with **no application JavaScript errors**. Tests used isolated accounts/storage. Failure and delay cases were injected at the browser request boundary to exercise frontend behavior; successful retries used the real local backend.

Limits: this tests the listed scenarios, not every possible interaction. Live external AI service calls, all browser/device combinations, full screen-reader use and the operating system's print-save dialog were not covered by this pass. It is not a guarantee that every website defect has been eliminated.

---

## Resolution update - 13 October 2026

Follow-up fix: pending business-rule text can no longer be silently omitted. The final button changes to Generate & review rules, generates interpretations, and stops before validation. Unresolved requirements remain in the editor until corrected or explicitly removed. The browser regression audit passed the reported type-then-click scenario, mixed valid/unresolved input, repeated unresolved attempts without creating runs, draft refresh, and inclusion of the generated rule in the final validation payload. The full browser audit completed with no application JavaScript errors.

All 12 numbered findings below have been addressed. The original audit is retained as the record of the problems before these changes.

| Finding | Resolution |
| --- | --- |
| 1. Shared account data | Owner recorded on uploads/runs; profile, rows, semantic changes, rule parsing, reference access, History and downloads enforce ownership. Unowned legacy files remain inaccessible. |
| 2. Changed CSV values | Original cell text used for browsing and downloads; separate parsed values used for validation. Leading zeros and text null tokens survive. Metadata no longer overwrites input columns. |
| 3. Lost drafts | Account/dataset drafts, generated rules and reference filenames saved in tab session storage and restored after refresh. Save failures are explained. |
| 4. Results navigation | Viewing a report preserves the active draft. The explicit Review this run's checks action restores that run's dataset and reviewed settings, including business rules. |
| 5. Login return links | Authentication uses the shared route map, including Business Rules; trailing-slash signup also works. |
| 6. Catalog totals | Heading and catalog derive counts from the same 19-check, 7-category catalog. |
| 7. Hidden issue limit | Issues paginate 100 per page, display matching totals and explain the full rejected CSV. Backend's 5,000-entry sample warning remains visible. |
| 8. Zero/false values | Nullish-aware display/search preserves zero, false and empty values. |
| 9. History filenames | History summaries include the original filename. |
| 10. Empty extra page | Next uses response offset, returned count and total. Stale record requests are ignored; numeric sorting preserves original text. |
| 11. Accessibility context | Page heading receives focus; current sidebar item uses aria-current; searches have labels. |
| 12. Admin fallback | No default admin password; administration requires DQ_ADMIN_PASSWORD. Database connections also close after transactions. |

Additional improvements implemented: downloadable sample and onboarding steps, optional Business Rules labels, Run validation action, loading/retry messages, reference-name restoration, editable expected schema, Results score/formula, administrator-assisted password recovery guidance, Print / Save as PDF labeling, and corrected placeholder icons/punctuation. One-sided numeric bounds now execute correctly.

Final verification: **58 automated tests passed**, with one third-party Starlette/AnyIO deprecation warning. The repeatable `tests/browser_audit.py` completed successfully in headless Edge with no application JavaScript errors. It checked the complete upload-to-download flow; original CSV values in both outputs; refresh recovery for text, generated rules, schema and references; report navigation; History filenames; catalog totals; mobile width at 390px; all seven downloaded report formats; the PDF print action; zero/false issue rendering and pagination; a full 50-row final page; account isolation; login return links; and admin create/edit/delete.

Testing limits: the PDF check verifies that the print action is invoked, not the operating system's Save dialog. Full screen-reader testing, provider-enabled AI and every document-format/browser combination remain outside this regression run. Mobile checks verify overflow and interactions, not a complete visual-design review.

Operational notes: restart the server if it is not using automatic reload. Admin access requires an explicitly configured DQ_ADMIN_PASSWORD. Existing unowned uploads/reports are preserved on disk; re-upload files to create owned runs. Drafts survive refresh in their browser tab but are not permanent or synchronized across devices.

---

## Historical audit - 12 October 2026 (resolved)

The following reproductions describe the website before the fixes. Original line numbers may have moved. Refer to the resolution table above for current behavior.

Scope: signup/login, upload, profiling, check selection, business rules, validation, results, accepted CSV download, history, deep links, refresh, mobile layout, and administrator source review.

## Verification

- Existing automated suite: 52 passed, one dependency deprecation warning (39.02 seconds).
- Headless Microsoft Edge: signup/login succeeded; upload and profiling succeeded; business rule `amount must be greater than 0` generated and validation completed. Three input rows produced one accepted and two rejected rows. Accepted CSV download succeeded.
- Catalog and Results at 390 x 844: document width stayed at 390px; this checks horizontal overflow, not every visual or touch interaction.
- Browser tests used temporary accounts and storage, with external AI disabled. No production user records were edited.
- Source reviewed for the administration interface; its existing API lifecycle tests passed. Admin browser CRUD, all export formats, every document format, provider-enabled AI, and full screen-reader behavior were not manually exercised.
- The second browser harness completed the observations below, then failed because its final synthetic failure-rendering probe ran on Business Rules, where the results container does not exist. That harness error is not a website navigation defect. A Windows file-lock error also interrupted temporary-directory cleanup.
- Playwright was installed in the existing virtual environment for the browser audit. Application source was not changed.

## Original findings - all resolved

### 1. High — accounts share datasets and validation reports
Evidence: a second newly registered account received one run from GET /api/runs and HTTP 200 for the first account's dataset profile. Authentication is checked, but dataset/run ownership is not stored or enforced.
Location: backend/app.py:40, backend/app.py:137, backend/app.py:361; backend/storage.py:43 and :81.
Impact: a user sees another user's history and can access their uploaded data and report. The README documents local shared storage as an MVP boundary, but account-based UI makes the behavior surprising.
Recommendation: associate uploads/runs with their creator, authorize every read, write, reference lookup and download, and filter History by account. Define explicit shared-workspace access separately if desired.

### 2. High — CSV identifiers are changed during import
Reproduction: upload an id column containing 001 and 002; File Details displays 1 and 2. Confirmed in Edge.
Location: backend/storage.py:55.
Cause: pandas read_csv infers numeric types before profiling and output generation.
Impact: leading zeros are lost, and default CSV null interpretation can also change literal values such as NA. Accepted/rejected output uses this parsed data.
Recommendation: preserve original text values for output and derive separate typed representations for validation; offer explicit type overrides for identifiers and meaningful null tokens.

### 3. High — unfinished check and business-rule work disappears on refresh
Reproduction: upload a file, enter a business condition, refresh Business Rules. The text area is empty. Generated rules and chosen configurations live only in memory as well.
Location: frontend/app.js:1, :24, :267.
Recommendation: save drafts per account and dataset, restore them on refresh, and indicate saved state. Warn before abandoning unsaved work until persistence exists.

### 4. Medium — Results Previous cannot restore the run's file
Reproduction: complete validation, refresh the Results URL, click Previous. Results stays open and says 'Upload a file first.' Confirmed in Edge.
Opening an old run while another file is active can instead send the user to that other file's Business Rules.
Location: frontend/app.js:287, :323, :939.
Cause: restoring a run only sets lastRun, without loading its dataset or restoring the reviewed configuration.
Recommendation: restore dataset and run settings before returning to review; make the destination clear, such as 'Review this run's checks'.

### 5. Medium — Business Rules links are lost when login is required
Reproduction: open /business-rules?dataset=<id> in a fresh browser context. The login URL is /login without a next parameter. Confirmed in Edge.
Location: frontend/auth.js:2.
Cause: workspacePaths omits /business-rules.
Recommendation: use one canonical route map for authentication and navigation and preserve the full allowed return path.

### 6. Medium — Available Checks gives contradictory totals
Confirmed visible text: heading says '100 data quality checks' in '15 categories', while the catalog says '19 checks across 7 categories'.
Location: frontend/index.html:69; frontend/app.js:926.
Recommendation: derive heading and catalog totals from the same catalog data.

### 7. Medium — failure details silently stop after 500 displayed issues
Location: frontend/app.js:1046.
Evidence from source: filtering ends with slice(0, 500); the UI does not disclose this display cap. Backend separately caps stored detail at 5,000 and does disclose that larger cap.
Impact: users can mistake a partial list for the full set of issues.
Recommendation: show 'Displaying 500 of X matching issues', paginate details, and explain that rejected CSV contains per-record reasons.

### 8. Medium — zero and false observed values look missing
Location: frontend/app.js:1048 and :1051.
Evidence from source: actual_value || '' in search and actual_value || '—' in display discard numeric zero and boolean false. This affects failures carrying those native JSON types; string '0' is unaffected.
Recommendation: use nullish fallback and distinguish missing, empty, zero, and false values.

### 9. Medium — history loses the original filename
Reproduction: upload 'Audit File.csv', validate, open History. Filename displays 'Audit_File'. Confirmed in Edge.
Location: backend/storage.py:86; frontend/app.js:938.
Cause: list_runs excludes dataset_filename despite the full report storing it.
Recommendation: include the original filename in history summaries and retain extension and spacing.

### 10. Medium — record pagination offers an empty extra page
Location: frontend/app.js:498.
Evidence from source: Next is enabled whenever a page has 50 rows. With exactly 50 or 100 total rows, the final full page still offers Next.
Recommendation: disable Next when response offset plus returned row count reaches total_count. Use response-local values when rendering, and ignore stale responses from rapid searches or sorting.

### 11. Medium — keyboard and assistive navigation need clearer context
Location: frontend/app.js:299, :455, :1039; frontend/index.html:21.
Evidence from source: page changes update visible content without moving focus to the new heading; sidebar active state lacks aria-current; dataset and failure search inputs rely on placeholders.
Recommendation: focus the page heading on user-triggered navigation, mark the current sidebar item with aria-current='page', and add persistent labels. Verify with keyboard-only and screen-reader use.

### 12. Medium — admin password has a predictable fallback
Location: backend/admin.py:31.
Evidence from source: when DQ_ADMIN_PASSWORD is absent, login accepts 'admin'. The actual configured password was not inspected.
Recommendation: require explicit admin-password configuration and keep administration unavailable until configured.

## Original improvement recommendations - implemented

- Add a small downloadable sample file and a concise three-step first-use explanation.
- Name the action 'Run validation' rather than 'Next: run validation', and label Business Rules as optional when no additional rules are required.
- Show readable empty, loading, success, and retry states on History and record browsing. Avoid exposing raw 'Failed to fetch' errors.
- Preserve reference upload filenames/status across check-card rerenders; currently only the reference ID survives draft restoration.
- Offer controls for schema expectations: schema validation currently hides its JSON configuration and only shows detected column names, so users cannot readily define an expected schema.
- Explain the History score and its formula in Results. Current Results rendering does not expose run.score even though History displays it and README says the formula is visible.
- Provide a clear recovery path for a forgotten password; the current login offers no recovery action.
- Label the PDF action 'Print / Save as PDF' because it opens the browser print dialog.
- Replace literal question marks used as the admin dialog close icon, column-picker arrow/search ellipsis, and business-document loading punctuation.

The suggested implementation order above has been completed. Testing limitations remain as explicitly stated in the current verification sections.
