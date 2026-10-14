const state = {
  userId: null,
  dataset: null,
  profile: null,
  recommendations: [],
  rules: [],
  businessRules: [],
  businessClarifications: [],
  businessDocumentFailure: null,
  lastRun: null,
  downloadNames: {},
  reviewDraft: null,
  explorerOffset: 0,
  explorerSearch: '',
  explorerSort: { column: '', direction: 'asc' },

};

const byId = (id) => document.getElementById(id);
const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[character]);
const numberFormat = (value) => Number(value || 0).toLocaleString();
const percentage = (value) => `${Number(value || 0).toFixed(1)}%`;
const semanticChoices = ['UNKNOWN', 'IDENTIFIER', 'EMAIL', 'PHONE', 'NAME', 'DATE', 'TIMESTAMP', 'CURRENCY', 'AMOUNT', 'PERCENTAGE', 'STATUS', 'CATEGORY', 'ADDRESS', 'COUNTRY', 'STATE', 'POSTAL_CODE', 'BOOLEAN', 'FOREIGN_KEY', 'DESCRIPTION', 'NUMERIC_MEASURE'];

let agentBusy = false;
let businessBusy = false;
const deleteIcon = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 6h18M9 6V4h6v2M5 6l1 14h12l1-14M10 10v6M14 10v6"/></svg>';
let draftSaved = true;
let lockedControls = new Map();
function syncWorkspaceLock() {
  const busy = agentBusy || businessBusy;
  if (busy) {
    document.querySelectorAll('.workspace input, .workspace select, .workspace textarea, .workspace button, #logout-button').forEach(control => {
      if (!lockedControls.has(control)) lockedControls.set(control, control.disabled);
      control.disabled = true;
    });
  } else {
    lockedControls.forEach((disabled, control) => { if (control.isConnected) control.disabled = disabled; });
    lockedControls.clear();
  }
}
function draftKey() { return state.userId && state.dataset ? `dq-draft:${state.userId}:${state.dataset.dataset_id}` : null; }
function persistDraft() {
  const key = draftKey();
  if (!key || !state.reviewDraft) return;
  try {
    sessionStorage.setItem(key, JSON.stringify({version: 1, draft: state.reviewDraft, businessRules: state.businessRules, documentFailure: state.businessDocumentFailure}));
    draftSaved = true;
    if (byId('draft-status')) byId('draft-status').textContent = 'Draft saved in this browser tab.';
  } catch {
    draftSaved = false;
    if (byId('draft-status')) byId('draft-status').textContent = 'Draft could not be saved. Keep this tab open until validation finishes.';
  }
}
function loadDraft() {
  const key = draftKey();
  try {
    const saved = key && JSON.parse(sessionStorage.getItem(key));
    if (saved?.version === 1 && Array.isArray(saved.draft?.cards) && Array.isArray(saved.businessRules)) {
      state.reviewDraft = saved.draft; state.businessRules = saved.businessRules; state.businessDocumentFailure = saved.documentFailure || null;
    }
  } catch { draftSaved = false; }
}
window.addEventListener('beforeunload', event => {
  if (!draftSaved || agentBusy || businessBusy) { event.preventDefault(); event.returnValue = ''; }
});
function setWorkspaceUser(user) {
  if (state.userId !== String(user.id)) {
    state.dataset = null; state.profile = null; state.lastRun = null; state.downloadNames = {}; state.reviewDraft = null; state.businessRules = []; state.businessClarifications = []; state.businessDocumentFailure = null;
    ['review-page-content', 'business-page-content', 'results-page-content', 'profile-page-content'].forEach(id => byId(id).replaceChildren());
    byId('welcome-upload').classList.remove('hidden'); byId('workspace-content').classList.add('hidden');
    byId('upload-heading').textContent = 'Upload your file';
    byId('upload-help').textContent = 'Choose a file below to see its data profile.';
  }
  state.userId = String(user.id);
}
async function activateRun(run) {
  const payload = await api(`/api/datasets/${encodeURIComponent(run.dataset_id)}/profile`);
  state.dataset = payload.dataset; state.profile = payload.profile; state.recommendations = payload.recommendations;
  state.lastRun = run; state.businessRules = run.business_rules || []; state.businessDocumentFailure = null;
  state.reviewDraft = {cards: (run.rules_selected || []).filter(rule => rule.predicate !== 'user_approved_comparison').map(rule => ({
    id: rule.rule_id, checked: true, controls: Object.entries(rule.configuration || {}).map(([key, value]) => ({key, value: key === 'parameters' ? JSON.stringify(value) : value}))
  })), acceptedName: run.outputs?.accepted, rejectedName: run.outputs?.rejected, businessText: '', businessFeedback: ''};
  state.explorerOffset = 0; state.explorerSearch = ''; state.explorerSort = {column: '', direction: 'asc'};
  byId('review-page-content').replaceChildren();
  byId('welcome-upload').classList.add('hidden'); byId('workspace-content').classList.remove('hidden');
  byId('upload-heading').textContent = 'Your file is ready';
  byId('upload-help').textContent = [...(state.dataset.import_notes || []), 'Review the summary below, then choose your checks.'].join(' ');
  renderOverview();
}
function saveReviewDraft() {
  const target = byId('review-page-content');
  if (!target.querySelector('.rule-card')) return;
  state.reviewDraft = {
    cards: [...target.querySelectorAll('.rule-card')].map(card => ({ id: card.dataset.ruleId, checked: card.querySelector('.rule-toggle').checked, referenceName: card.dataset.referenceName || '', schemaText: card.querySelector('[data-param-json]')?.value, controls: [...card.querySelectorAll('[data-config]')].map(control => ({ key: control.dataset.key, value: control.multiple ? [...control.selectedOptions].map(option => option.value) : control.type === 'checkbox' ? control.checked : control.value })) })),
    acceptedName: state.reviewDraft?.acceptedName ?? `${state.dataset.name}_accepted`,
    rejectedName: state.reviewDraft?.rejectedName ?? `${state.dataset.name}_rejected`,
    businessText: byId('business-input')?.value || '',
    businessFeedback: byId('business-feedback')?.querySelector('.business-response')?.textContent || byId('business-feedback')?.textContent || '',
    businessClarifications: state.businessClarifications,
  };
  persistDraft();
}
function restoreReviewDraft() {
  if (!state.reviewDraft) return;
  state.reviewDraft.cards.forEach(saved => {
    const card = [...byId('review-page-content').querySelectorAll('.rule-card')].find(item => item.dataset.ruleId === saved.id);
    if (!card) return;
    const params = saved.controls.find(setting=>setting.key==='parameters');
    if (params) {
      const rule = executableChecks().find(item => item.rule_id === saved.id);
      try { card.querySelector('.config-form').innerHTML = renderRuleConfig({...rule, configuration_defaults: {...rule.configuration_defaults, parameters: JSON.parse(params.value)}}); }
      catch { toast('Some saved settings could not be restored. Review this check before running.', true); }
    }
    if (saved.schemaText !== undefined && card.querySelector('[data-param-json]')) { card.querySelector('[data-param-json]').value = saved.schemaText; syncSuggestedParameters(card.querySelector('[data-param-json]')); }
    card.dataset.referenceName = saved.referenceName || '';
    if (card.querySelector('.reference-status') && saved.referenceName) card.querySelector('.reference-status').textContent = 'Reference ready: ' + saved.referenceName;
    card.querySelector('.rule-toggle').checked = card.querySelector('.rule-toggle').disabled || saved.checked;
    saved.controls.forEach(setting => {
      const control = [...card.querySelectorAll('[data-config]')].find(item => item.dataset.key === setting.key);
      if (!control) return;
      if (control.multiple) [...control.options].forEach(option => { option.selected = setting.value.includes(option.value); });
      else if (control.type === 'checkbox') control.checked = setting.value;
      else control.value = setting.value;
    });
  });
  byId('business-input').value = state.reviewDraft.businessText || '';
  byId('business-feedback').textContent = state.reviewDraft.businessFeedback || '';
  state.businessClarifications = state.reviewDraft.businessClarifications || [];
  renderClarificationChoices();
  if (byId('draft-status')) byId('draft-status').textContent = draftSaved ? 'Draft saved in this browser tab.' : 'Draft could not be saved. Keep this tab open.';
}
function setBusinessBusy(busy) {
  businessBusy = busy;
  syncWorkspaceLock();
  updateApprovalCount();
}


function agentProgress(stage, title, detail, busy = false, failed = false) {
  agentBusy = busy;
  byId('agent-title').textContent = title;
  byId('agent-detail').textContent = detail;
  byId('agent-indicator').className = busy ? 'agent-spinner' : failed ? 'agent-error' : 'agent-ready';
  byId('agent-status').classList.toggle('hidden', !busy && !failed);
  byId('agent-status').setAttribute('aria-busy', String(busy));
  syncWorkspaceLock();
  uploadSurface.classList.toggle('upload-busy', busy);
}
function saveDownload(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url; link.download = filename; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}
function reportSnapshot() {
  const report = byId('dq-summary-report');
  if (!report) throw new Error('No report is available.');
  const clone = report.cloneNode(true);
  clone.querySelectorAll('[data-export-control]').forEach(item => item.remove());
  const styles = [...document.styleSheets].map(sheet => {
    try { return [...sheet.cssRules].map(rule => rule.cssText).join('\n'); } catch { return ''; }
  }).join('\n');
  return { clone, styles, width: Math.max(1200, report.scrollWidth), height: report.scrollHeight };
}
async function downloadQualityReport(format = 'html') {
  try {
    byId('report-download-menu')?.removeAttribute('open');
    if (format === 'pdf') { window.print(); return; }
    const { clone, styles, width } = reportSnapshot();
    if (format === 'html') {
      saveDownload(new Blob([`<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Data Quality Summary Report</title><style>${styles} body{background:white;padding:24px;max-width:1600px;margin:auto}</style></head><body>${clone.outerHTML}</body></html>`], {type:'text/html;charset=utf-8'}), 'data_quality_summary_report.html');
      return;
    }
    if (format === 'json') {
      saveDownload(new Blob([JSON.stringify(state.lastRun, null, 2)], {type:'application/json'}), 'data_quality_summary_report.json');
      return;
    }
    if (format === 'csv') {
      const rows = [['Data Quality Summary Report'], ['Report information']];
      clone.querySelectorAll('.report-metadata > div').forEach(item => rows.push([item.textContent.trim()]));
      rows.push([], ['Summary metric', 'Value', 'Percentage']);
      clone.querySelectorAll('.report-metric').forEach(item => rows.push([
        item.querySelector('strong').textContent, item.querySelector('b').textContent,
        item.querySelector('small')?.textContent || '',
      ]));
      clone.querySelectorAll('.report-charts .report-panel').forEach(panel => {
        rows.push([], [panel.querySelector('h2').textContent]);
        const chart = panel.querySelector('svg');
        rows.push([chart?.getAttribute('aria-label') || panel.textContent.trim()]);
      });
      rows.push([], ['Data Quality Check Details']);
      clone.querySelectorAll('.dq-check-table tr').forEach(row => rows.push([...row.cells].map(cell => cell.textContent)));
      clone.querySelectorAll('.report-note,.notice,.dq-report-footnote').forEach(item => rows.push([item.textContent.trim()]));
      const csv = rows.map(row => row.map(value => '"' + String(value).replaceAll('"','""') + '"').join(',')).join('\r\n');
      saveDownload(new Blob(['\ufeff' + csv], {type:'text/csv;charset=utf-8'}), 'data_quality_summary_report.csv');
      return;
    }
    await document.fonts.ready;
    const exportWidth = Math.max(1600, width);
    const container = document.createElement('div');
    container.className = 'report-export';
    container.style.cssText = `position:fixed;left:-100000px;top:0;width:${exportWidth}px;padding:12px;background:white;box-sizing:border-box;`;
    const exportStyle = document.createElement('style');
    exportStyle.textContent = `
      .report-export .reference-report{width:100%;height:auto;max-height:none;overflow:visible}
      .report-export .report-banner{grid-template-columns:1.5fr 1fr}
      .report-export .report-metadata{text-align:right;justify-self:stretch}
      .report-export .report-metrics{grid-template-columns:repeat(6,minmax(0,1fr))}
      .report-export .report-charts{grid-template-columns:1fr 1.35fr 1fr}
      .report-export .report-category{grid-column:auto;grid-row:auto}
      .report-export .report-checks,.report-export .column-table-wrap{overflow:visible;height:auto;max-height:none}
      .report-export .report-checks .dq-check-table{width:100%;min-width:0;table-layout:fixed}
      .report-export .report-checks .dq-check-table th:nth-child(1){width:48px}
      .report-export .report-checks .dq-check-table th:nth-child(2){width:14%}
      .report-export .report-checks .dq-check-table th:nth-child(3){width:9%}
      .report-export .report-checks .dq-check-table th:nth-child(4){width:18%}
      .report-export .report-checks .dq-check-table th:nth-child(5){width:22%}
      .report-export .report-checks .dq-check-table td,.report-export .report-checks .dq-check-table th{
        min-width:0;white-space:normal;overflow:visible;text-overflow:clip;overflow-wrap:anywhere;height:auto;max-height:none
      }
      .report-export .report-checks .dq-check-table th:first-child,
      .report-export .report-checks .dq-check-table td:first-child{
        width:48px;min-width:48px;white-space:nowrap;overflow-wrap:normal;word-break:normal;text-align:center
      }
      .report-export .report-badge{min-width:0;white-space:normal}
    `;
    container.append(exportStyle, clone); document.body.appendChild(container);
    let svg;
    let height;
    try {
      // Measure the exact layout that will be serialized, after fonts and export rules apply.
      height = Math.ceil(Math.max(container.scrollHeight, container.getBoundingClientRect().height)) + 2;
      const frozen = clone.cloneNode(true);
      const sources = [clone, ...clone.querySelectorAll('*')];
      const targets = [frozen, ...frozen.querySelectorAll('*')];
      sources.forEach((source, index) => {
        const computed = getComputedStyle(source);
        const target = targets[index];
        // Resolve CSS variables and media queries so the exported SVG renders independently.
        const properties = 'display box-sizing position top right bottom left width height min-width max-width min-height max-height margin-top margin-right margin-bottom margin-left padding-top padding-right padding-bottom padding-left overflow overflow-x overflow-y color background-color background-image border-top border-right border-bottom border-left border-radius border-collapse border-spacing table-layout font-family font-size font-weight font-style line-height letter-spacing text-align text-transform text-decoration white-space overflow-wrap word-break text-overflow vertical-align grid-template-columns grid-template-rows grid-column grid-row gap row-gap column-gap flex flex-direction flex-wrap flex-shrink align-items align-self justify-content opacity fill stroke stroke-width stroke-linecap stroke-linejoin transform transform-origin'.split(' ');
        properties.forEach(property => target.style.setProperty(property, computed.getPropertyValue(property)));
      });
      const wrapper = document.createElementNS('http://www.w3.org/1999/xhtml', 'div');
      wrapper.setAttribute('style', `width:${exportWidth}px;padding:12px;background:white;box-sizing:border-box;`);
      wrapper.append(frozen);
      svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${exportWidth}" height="${height}" viewBox="0 0 ${exportWidth} ${height}"><foreignObject width="100%" height="100%">${new XMLSerializer().serializeToString(wrapper)}</foreignObject></svg>`;
    } finally { container.remove(); }
    if (format === 'svg') {
      saveDownload(new Blob([svg], {type:'image/svg+xml;charset=utf-8'}), 'data_quality_summary_report.svg');
      return;
    }
    const types = {png:'image/png', jpeg:'image/jpeg', webp:'image/webp'};
    if (!types[format]) throw new Error('Unsupported report format.');
    const img = new Image();
    await new Promise((resolve,reject) => {
      img.onload = resolve; img.onerror = () => reject(new Error('Report image could not be rendered. Try HTML or PDF.'));
      img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg);
    });
    const canvas = document.createElement('canvas');
    // Bound pixel allocation for long reports while preserving the complete report.
    const scale = Math.min(2, 16000 / exportWidth, 16000 / height, Math.sqrt(64000000 / (exportWidth * height)));
    canvas.width = Math.ceil(exportWidth * scale); canvas.height = Math.ceil(height * scale);
    const context = canvas.getContext('2d');
    if (!context) throw new Error('Image export is unavailable in this browser.');
    context.scale(scale,scale); context.fillStyle = '#fff'; context.fillRect(0,0,exportWidth,height); context.drawImage(img,0,0);
    const dataUrl = canvas.toDataURL(types[format], 0.95);
    const mime = dataUrl.slice(5, dataUrl.indexOf(';'));
    const bytes = Uint8Array.from(atob(dataUrl.split(',')[1] || ''), character => character.charCodeAt(0));
    const blob = new Blob([bytes], {type:mime});
    if (!blob.size || blob.type !== types[format]) throw new Error('This browser does not support that image format. Try PNG.');
    saveDownload(blob, `data_quality_summary_report.${format === 'jpeg' ? 'jpg' : format}`);
  } catch (error) { toast(`Download failed: ${error.message}`, true); }
}
async function downloadRecordFile(output) {
  try {
    const run = state.lastRun;
    const name = byId(output === 'accepted' ? 'accepted-name' : 'rejected-name').value.trim() || run.outputs?.[output] || output;
    const response = await fetch(`/api/runs/${encodeURIComponent(run.run_id)}/download/${output}`);
    if (response.status === 401 && window.showLogin) window.showLogin();
    if (!response.ok) throw new Error(response.status === 401 ? 'Please sign in again to download records.' : 'Could not download records. Try again.');
    saveDownload(await response.blob(), name.replace(/\.csv$/i,'') + '.csv');
  } catch (error) { toast(error.message, true); }
}

async function api(path, options = {}) {
  let response;
  try { response = await fetch(path, options); }
  catch { throw new Error('Unable to connect. Check your connection and try again.'); }
  if (response.status === 401 && window.showLogin) window.showLogin();
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(Array.isArray(payload.detail) ? payload.detail.map(item => item.msg).join('; ') : payload.detail || `Request failed (${response.status})`);
  return payload;
}

let toastTimer;
function toast(message, error = false) {
  const node = byId('toast');
  node.textContent = message;
  node.style.borderLeftColor = error ? 'var(--red)' : 'var(--orange-light)';
  node.classList.add('visible');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.remove('visible'), 3400);
}

function showNotice(message, kind = '') {
  const node = byId('notice');
  node.textContent = message;
  node.className = `notice ${kind}`.trim();
}

function setLoading(button, loading, label = 'Working…') {
  if (loading) {
    button.dataset.previous = button.innerHTML;
    button.disabled = true;
    button.textContent = label;
  } else {
    button.disabled = false;
    if (button.dataset.previous) button.innerHTML = button.dataset.previous;
  }
}

function updateFlow(name = 'overview') {
  document.querySelectorAll('[data-needs-file]').forEach((button) => { button.disabled = !state.dataset; });
  document.querySelectorAll('[data-needs-results]').forEach((button) => { button.disabled = !state.lastRun; });
  const current = name === 'profile' ? 'overview' : name;
  document.querySelectorAll('.flow-step').forEach((button) => {
    const active = button.dataset.page === current;
    button.classList.toggle('active', active);
    if (active) button.setAttribute('aria-current', 'step');
    else button.removeAttribute('aria-current');
  });
}

const pagePaths = { overview: '/upload-file', review: '/choose-checks', business: '/business-rules', results: '/results', profile: '/file-details', history: '/history', rules: '/available-checks' };
function writePageUrl(name, replace = false) {
  if (!/^https?:$/.test(location.protocol)) return;
  const url = new URL(pagePaths[name], location.origin);
  if (state.dataset && ['overview', 'review', 'business', 'profile'].includes(name)) url.searchParams.set('dataset', state.dataset.dataset_id);
  if (name === 'results' && state.lastRun) url.searchParams.set('run', state.lastRun.run_id);
  if (url.pathname + url.search !== location.pathname + location.search) history[replace ? 'replaceState' : 'pushState']({page:name}, '', url.pathname + url.search);
}
async function openPageFromUrl() {
  if (document.body.classList.contains('auth-pending')) {
    if (window.setAuthMode) window.setAuthMode(location.pathname === '/signup' ? 'signup' : 'login', false);
    return;
  }
  if (agentBusy || businessBusy) { writePageUrl(document.querySelector('.page.active')?.dataset.pageContent || 'overview', true); return; }
  const name = Object.keys(pagePaths).find(key => pagePaths[key] === location.pathname.replace(/\/$/, '')) || 'overview';
  const params = new URLSearchParams(location.search);
  try {
    const datasetId = params.get('dataset');
    if (datasetId && ['overview', 'review', 'business', 'profile'].includes(name) && state.dataset?.dataset_id !== datasetId) {
      agentProgress(1, 'Opening your file', 'Restoring the file and saved settings. Please wait.', true);
      const payload = await api(`/api/datasets/${encodeURIComponent(datasetId)}/profile`);
      state.dataset = payload.dataset; state.profile = payload.profile; state.recommendations = payload.recommendations;
      state.reviewDraft = null; state.businessRules = []; state.businessClarifications = []; state.businessDocumentFailure = null; state.explorerOffset = 0; state.explorerSearch = ''; state.explorerSort = {column:'',direction:'asc'};
      loadDraft();
      byId('review-page-content').innerHTML = '';
      byId('welcome-upload').classList.add('hidden'); byId('workspace-content').classList.remove('hidden');
      renderOverview(); byId('upload-heading').textContent = 'Your file is ready';
      byId('upload-help').textContent = [...(state.dataset.import_notes || []), 'Review the summary below, then choose your checks.'].join(' ');
    }
    const runId = params.get('run');
    if (name === 'results' && runId && state.lastRun?.run_id !== runId) {
      agentProgress(1, 'Opening report', 'Please wait for the selected report.', true);
      state.lastRun = await api(`/api/runs/${encodeURIComponent(runId)}`);
    }
    if (agentBusy) agentProgress(0, 'Ready', 'Your workspace is ready.');
    if ((['review', 'business', 'profile'].includes(name) && !state.dataset) || (name === 'results' && !state.lastRun)) {
      toast('Upload a file or open a previous result from History to continue.'); showPage('overview', {replace:true}); return;
    }
    showPage(name, {replace:true});
  } catch (error) {
    if (agentBusy) agentProgress(0, 'Unable to open this page', error.message, false, true);
    if (document.body.classList.contains('auth-pending')) return;
    toast(error.message, true); showPage('overview', {replace:true});
  }
}
window.addEventListener('popstate', () => openPageFromUrl());

function showPage(name, options = {}) {
  if (agentBusy || businessBusy) { toast('Please wait for the current request to finish.'); return; }
  if (name === 'business' && state.dataset) {
    if (!byId('review-page-content').querySelector('.rule-card')) renderReview();
    if (!validateCheckSettings()) return;
  }
  if (byId('page-review').classList.contains('active') || byId('page-business').classList.contains('active')) saveReviewDraft();
  if (name === 'review' && state.dataset) agentProgress(3, 'What business rules should your data follow?', 'Review suggested tests and add your conditions, or run the suggested tests as they are.');
  if (name === 'results' && state.lastRun) agentProgress(6, 'Your quality report is ready', 'Download accepted records, rejected records, and your summary report.');
  if (['review', 'business', 'profile'].includes(name) && !state.dataset) { toast('Upload a file first.', true); return; }
  if (name === 'results' && !state.lastRun) { toast('Run checks first to see results.', true); return; }
  writePageUrl(name, options.replace);
  updateFlow(name);
  document.querySelectorAll('[data-page-content]').forEach((page) => page.classList.toggle('active', page.dataset.pageContent === name));
  document.querySelectorAll('.rail-link').forEach(link => {
    const active = link.dataset.page === name; link.classList.toggle('active', active);
    if (active) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current');
  });
  if (name === 'review') renderReview();
  if (name === 'business' && !byId('review-page-content').querySelector('.rule-card')) renderReview();
  if (name === 'profile' && state.profile) renderProfile();
  if (name === 'rules') renderRegistry();
  if (name === 'history') renderHistory();
  if (name === 'results' && state.lastRun) renderResults(state.lastRun);
  const heading = document.querySelector('.page.active h1');
  if (heading) { heading.tabIndex = -1; heading.focus({preventScroll: true}); }
  window.scrollTo({top: 0});
}

document.querySelectorAll('[data-page]').forEach((element) => element.addEventListener('click', (event) => { event.preventDefault(); showPage(element.dataset.page); }));
byId('back-to-review').addEventListener('click', async () => {
  if (agentBusy || businessBusy || !state.lastRun) return;
  agentProgress(1, 'Restoring reviewed checks', "Loading this report's file and settings.", true);
  try { await activateRun(state.lastRun); agentProgress(0, 'Ready', 'Review the restored checks.'); showPage('business'); }
  catch (error) { agentProgress(0, 'Unable to restore this run', error.message, false, true); toast(error.message, true); }
});
byId('start-new-upload').addEventListener('click', () => byId('replace-dataset').click());

const fileInput = byId('file-input');
fileInput.addEventListener('change', () => {
  if (fileInput.files.length) uploadDataset(fileInput.files[0]);
});
byId('replace-dataset').addEventListener('click', () => fileInput.click());
byId('remove-dataset').addEventListener('click', () => {
  if (agentBusy || businessBusy) return;
  try { const key = draftKey(); if (key) sessionStorage.removeItem(key); } catch {}
  state.dataset = null;
  state.profile = null;
  state.recommendations = [];
  state.businessRules = []; state.businessClarifications = [];
  state.businessDocumentFailure = null;
  state.reviewDraft = null;
  state.lastRun = null;
  state.explorerOffset = 0;
  state.explorerSearch = '';
  state.explorerSort = { column: '', direction: 'asc' };
  fileInput.value = '';
  ['review-page-content', 'business-page-content', 'business-upload-slot', 'profile-page-content', 'results-page-content', 'dashboard-metrics'].forEach(id => { byId(id).replaceChildren(); });
  byId('workspace-content').classList.add('hidden');
  byId('welcome-upload').classList.remove('hidden', 'dragging');
  byId('notice').classList.add('hidden');
  byId('active-dataset-name').textContent = '';
  byId('active-dataset-source').textContent = '';
  byId('upload-heading').textContent = 'Upload your file';
  byId('upload-help').textContent = 'Choose a file below to see its data profile.';
  agentProgress(0, 'Ready for your file', 'Upload a supported data file.');
  showPage('overview', { replace: true });
  fileInput.focus();
});
byId('view-file-data').addEventListener('click', () => {
  if (agentBusy) return;
  showPage('profile');
  byId('profile-page-content').querySelector('.explorer')?.scrollIntoView({ block: 'start' });
});
const dropTarget = byId('drop-target');
const uploadSurface = byId('welcome-upload');
for (const type of ['dragenter', 'dragover']) uploadSurface.addEventListener(type, (event) => { event.preventDefault(); uploadSurface.classList.add('dragging'); });
for (const type of ['dragleave', 'drop']) uploadSurface.addEventListener(type, (event) => { event.preventDefault(); uploadSurface.classList.remove('dragging'); });

uploadSurface.addEventListener('drop', (event) => { const file = event.dataTransfer.files[0]; if (file) uploadDataset(file); });

async function uploadDataset(file) {
  if (agentBusy || businessBusy) return;
  if (file.size > 50 * 1024 * 1024) { toast('Please choose a file smaller than 50 MB.', true); return; }
  if (!/\.(csv|parquet|pq|tsv|txt|psv|xlsx|xls|xlsm|ods|json|jsonl|ndjson|xml|feather|arrow|orc|avro)$/i.test(file.name)) { toast('Please choose one of the supported data file formats listed on Upload.', true); return; }
  showPage('overview');
  agentProgress(1, 'Uploading and profiling your file', 'Checking columns, missing values, duplicates, and suitable tests. Please wait.', true);
  const button = document.querySelector('.file-picker span');
  button.textContent = 'Uploading...';
  try {
    const data = new FormData();
    data.append('file', file);
    const payload = await api('/api/datasets', { method: 'POST', body: data });
    state.lastRun = null;
    state.dataset = payload.dataset;
    state.profile = payload.profile;
    state.recommendations = payload.recommendations;
    state.businessRules = []; state.businessClarifications = [];
    state.businessDocumentFailure = null;
    state.reviewDraft = null;
    state.explorerSearch = '';
    state.explorerSort = { column: '', direction: 'asc' };
    state.explorerOffset = 0;

    byId('review-page-content').replaceChildren();
    byId('business-page-content').replaceChildren();
    byId('results-page-content').replaceChildren();
    byId('welcome-upload').classList.add('hidden');
    byId('workspace-content').classList.remove('hidden');
    byId('notice').classList.add('hidden');
    renderOverview();
    byId('upload-heading').textContent = 'Your file is ready';
    byId('upload-help').textContent = [...(state.dataset.import_notes || []), 'Review the summary below, then choose your checks.'].join(' ');
    agentProgress(2, 'Profiling complete. Suggested tests are ready.', 'Explore the profile below, then choose tests and add your business rules.');
    showPage('overview');
    toast(`Profiled ${payload.dataset.name} · ${numberFormat(payload.profile.row_count)} rows, ${payload.profile.column_count} columns`);
  } catch (error) {
    agentProgress(state.dataset ? 2 : 0, 'Unable to profile this file', error.message + ' Try again or choose another file.', false, true);
    showNotice(error.message, 'error');
  } finally {
    button.textContent = 'Choose file';
    fileInput.value = '';
  }
}

function renderOverview() {
  byId('upload-help').textContent = [...(state.dataset?.import_notes || []), 'Review the summary below, then choose your checks.'].join(' ');
  const profile = state.profile;
  byId('active-dataset-name').textContent = state.dataset.filename || state.dataset.name;
  byId('active-dataset-source').textContent = state.dataset.filename || '';
  const uploadMetrics = [
    { label: 'Total Records', value: profile.row_count, icon: 'M4 5c0-3 16-3 16 0s-16 3-16 0M4 5v14c0 3 16 3 16 0V5M4 12c0 3 16 3 16 0' },
    { label: 'Total Columns', value: profile.column_count, icon: 'M3 4h18v16H3zM9 4v16M15 4v16' },
    { label: 'Null Values', value: profile.missing_values?.count ?? 0, icon: 'M12 8v5M12 16h.01M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0' },
    { label: 'Duplicate Rows', value: profile.duplicate_row_count, icon: 'M8 8h12v12H8zM4 16V4h12' },
  ];
  byId('dashboard-metrics').innerHTML = uploadMetrics.map(item =>
    `<div class="metric"><span class="upload-metric-icon" aria-hidden="true"><svg viewBox="0 0 24 24" focusable="false"><path d="${item.icon}"/></svg></span><div class="upload-metric-copy"><span class="metric-label">${escapeHtml(item.label)}</span><span class="metric-value">${numberFormat(item.value)}</span></div></div>`
  ).join('');
  byId('notice').classList.add('hidden');
}

function metric(label, value, sub) {
  return `<div class="metric"><span class="metric-label">${escapeHtml(label)}</span><span class="metric-value">${escapeHtml(value)}</span><span class="metric-sub">${escapeHtml(sub)}</span></div>`;
}

function pysparkType(column) {
  if (column.pyspark_type) return column.pyspark_type;
  const dtype = String(column.physical_type || '').toLowerCase();
  if (dtype.startsWith('datetime')) return 'TimestampType';
  if (dtype.startsWith('timedelta')) return 'DayTimeIntervalType';
  return ({ int8: 'ByteType', int16: 'ShortType', int32: 'IntegerType', int64: 'LongType', uint8: 'ShortType', uint16: 'IntegerType', uint32: 'LongType', uint64: 'DecimalType(20, 0)', float32: 'FloatType', float64: 'DoubleType', bool: 'BooleanType', boolean: 'BooleanType' })[dtype] || 'StringType';
}

function primarySemantic(column) {
  return column.semantic_candidates?.[0] || { type: 'UNKNOWN', confidence: 0, evidence: ['No evidence is available.'] };
}

function renderProfile() {
  const profile = state.profile;
  if (!profile) return;
  const content = byId('profile-page-content');
  content.innerHTML = `
    <section class="file-table-section"><h2>File Summary</h2><div class="column-table-wrap"><table class="column-table file-summary-table"><tbody>
      <tr><th scope="row">File name</th><td>${escapeHtml(state.dataset.filename || state.dataset.name)}</td></tr>
      ${state.dataset.import_notes?.length ? `<tr><th scope="row">Import details</th><td>${state.dataset.import_notes.map(escapeHtml).join(" ")}</td></tr>` : ""}
      <tr><th scope="row">Rows</th><td>${numberFormat(profile.row_count)}</td></tr>
      <tr><th scope="row">Columns</th><td>${numberFormat(profile.column_count)}</td></tr>
      <tr><th scope="row">Nulls</th><td>${numberFormat(profile.missing_values.count)} cells (${percentage(profile.missing_values.percentage)})</td></tr>
      <tr><th scope="row">Duplicate rows</th><td>${numberFormat(profile.duplicate_row_count)}</td></tr>
      <tr><th scope="row">Memory</th><td>${(profile.memory_bytes / 1024 / 1024).toFixed(2)} MB</td></tr>
    </tbody></table></div></section>
    <section class="file-table-section"><h2>Column Details</h2>
    <div class="column-table-wrap"><table class="column-table"><thead><tr><th scope="col">Column</th><th scope="col">Data type</th><th scope="col">Meaning</th><th scope="col">Nulls</th><th scope="col">Distinct values</th><th scope="col">Details</th></tr></thead><tbody>${profile.columns.map((column, index) => profileRow(column, index)).join('')}</tbody></table></div></section>
    <section class="explorer"><div class="section-head" style="margin-top:28px"><div><span class="eyebrow">DATA EXPLORER</span><h2>Inspect the original records</h2></div><span id="explorer-count" class="muted"></span></div>
      <div class="report-controls"><label for="explorer-search">Search records</label><input class="explorer-search" id="explorer-search" placeholder="Search all dataset values" value="${escapeHtml(state.explorerSearch)}"><button class="button button-quiet" id="explorer-search-button">Search</button></div>
      <div class="column-table-wrap" id="explorer-table"></div><div class="pagination"><button id="explorer-prev">← Previous</button><span id="explorer-page"></span><button id="explorer-next">Next →</button></div>
    </section>`;
  content.querySelectorAll('.semantic-save').forEach((button) => button.addEventListener('click', () => saveSemantic(button.dataset.column, button.closest('.semantic-editor'))));
  const search = byId('explorer-search');
  byId('explorer-search-button').addEventListener('click', () => { state.explorerSearch = search.value; state.explorerOffset = 0; loadExplorer(); });
  search.addEventListener('keydown', (event) => { if (event.key === 'Enter') { state.explorerSearch = search.value; state.explorerOffset = 0; loadExplorer(); } });
  byId('explorer-prev').addEventListener('click', () => { state.explorerOffset = Math.max(0, state.explorerOffset - 50); loadExplorer(); });
  byId('explorer-next').addEventListener('click', () => { state.explorerOffset += 50; loadExplorer(); });
  loadExplorer();
}

function profileRow(column, index) {
  const primary = primarySemantic(column);
  const choices = semanticChoices.map(type => `<option value="${type}" ${type === primary.type ? 'selected' : ''}>${type.replaceAll('_', ' ')}</option>`).join('');
  const stats = Object.entries(column.statistics || {}).map(([key, value]) => `<tr><th scope="row">${escapeHtml(key.replaceAll('_', ' '))}</th><td>${escapeHtml(typeof value === 'object' && value !== null ? JSON.stringify(value) : value ?? '—')}</td></tr>`).join('');
  const top = (column.top_values || []).map(item => `<tr><td>${escapeHtml(item.value)}</td><td>${numberFormat(item.count)}</td></tr>`).join('');
  return `<tr><th scope="row" class="column-name-cell">${escapeHtml(column.name)}</th><td>${escapeHtml(pysparkType(column))}</td><td><span class="file-meaning">${escapeHtml(primary.type.replaceAll('_', ' '))}</span><div class="muted">${Math.round(primary.confidence * 100)}% confidence</div><details class="file-meaning-editor"><summary>Edit meaning</summary><div class="semantic-editor"><select aria-label="Meaning for ${escapeHtml(column.name)}">${choices}</select><input maxlength="160" placeholder="Describe this column" aria-label="Describe ${escapeHtml(column.name)}"><button class="semantic-save" data-column="${escapeHtml(column.name)}">Save meaning</button></div></details></td><td>${numberFormat(column.null_count)}<div class="muted">${percentage(column.null_percentage)}</div></td><td>${numberFormat(column.distinct_count)}<div class="muted">${percentage(column.uniqueness_percentage)} unique</div></td><td><details><summary>Statistics & frequent values</summary>${stats ? `<table class="file-stat-table"><caption>Statistics</caption><tbody>${stats}</tbody></table>` : '<p class="muted">No statistics available.</p>'}${top ? `<table class="file-stat-table"><caption>Frequent values</caption><thead><tr><th>Value</th><th>Count</th></tr></thead><tbody>${top}</tbody></table>` : ''}</details></td></tr>`;
}
async function saveSemantic(column, editor) {
  if (agentBusy || businessBusy) return;
  const datasetId = state.dataset.dataset_id;
  const semanticType = editor.querySelector('select').value;
  const explanation = editor.querySelector('input').value.trim();
  if (!explanation && semanticType === 'UNKNOWN') { toast('Choose a meaning and add a short explanation.', true); return; }
  agentProgress(2, 'Saving column meaning', 'Please wait for the updated profile.', true);
  try {
    const payload = await api(`/api/datasets/${encodeURIComponent(datasetId)}/semantics`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ column, semantic_type: semanticType, explanation }),
    });
    state.profile = payload.profile;
    state.recommendations = payload.recommendations;
    agentProgress(2, 'Meaning saved', 'Your profile is updated.');
    renderProfile(); renderOverview();
    toast(`Saved the user-provided meaning for ${column}.`);
  } catch (error) { toast(error.message, true); }
  finally { agentProgress(2, 'Ready', 'Review your file.'); }
}

let explorerRequest = 0;
async function loadExplorer() {
  if (!state.dataset) return;
  const request = ++explorerRequest;
  const datasetId = state.dataset.dataset_id;
  const params = new URLSearchParams({offset: String(state.explorerOffset), limit: '50', search: state.explorerSearch, sort_by: state.explorerSort.column, sort_direction: state.explorerSort.direction});
  const target = byId('explorer-table');
  if (!target) return;
  target.setAttribute('aria-busy', 'true');
  byId('explorer-count').textContent = 'Loading records…';
  byId('explorer-prev').disabled = true; byId('explorer-next').disabled = true;
  try {
    const payload = await api(`/api/datasets/${encodeURIComponent(datasetId)}/rows?${params}`);
    if (request !== explorerRequest || state.dataset?.dataset_id !== datasetId || target !== byId('explorer-table')) return;
    renderExplorerTable(payload);
    byId('explorer-count').textContent = `Rows ${numberFormat(payload.rows.length ? payload.offset + 1 : 0)}–${numberFormat(payload.offset + payload.rows.length)} of ${numberFormat(payload.total_count)}`;
    byId('explorer-page').textContent = `Page ${Math.floor(payload.offset / 50) + 1}`;
    byId('explorer-prev').disabled = payload.offset <= 0;
    byId('explorer-next').disabled = payload.offset + payload.rows.length >= payload.total_count;
  } catch (error) {
    if (request !== explorerRequest || target !== byId('explorer-table')) return;
    target.innerHTML = `<p class="notice error">${escapeHtml(error.message)}</p><button type="button" class="button button-quiet" id="explorer-retry">Try again</button>`;
    byId('explorer-count').textContent = 'Records could not be loaded.';
    byId('explorer-retry').onclick = loadExplorer;
  } finally { if (request === explorerRequest) target.setAttribute('aria-busy', 'false'); }
}

function renderExplorerTable(payload) {
  const columns = payload.columns;
  const rows = payload.rows;
  const html = rows.length ? `<table class="column-table"><thead><tr>${columns.map((column) => `<th><button class="sort-button" data-sort-column="${escapeHtml(column)}" aria-label="Sort by ${escapeHtml(column)}">${escapeHtml(column)}${state.explorerSort.column === column ? (state.explorerSort.direction === 'asc' ? ' ↑' : ' ↓') : ' ↕'}</button></th>`).join('')}</tr></thead><tbody>${rows.map((row) => `<tr>${columns.map((column) => `<td>${escapeHtml(row[column] ?? '—')}</td>`).join('')}</tr>`).join('')}</tbody></table>` : '<div class="empty-state">No matching records in this page.</div>';
  byId('explorer-table').innerHTML = html;
  byId('explorer-table').querySelectorAll('[data-sort-column]').forEach((button) => button.addEventListener('click', () => {
    const column = button.dataset.sortColumn;
    state.explorerSort = { column, direction: state.explorerSort.column === column && state.explorerSort.direction === 'asc' ? 'desc' : 'asc' };
    state.explorerOffset = 0;
    loadExplorer();
  }));
}

function executableChecks() {
  const source = state.rules.length ? state.rules : state.recommendations;
  const catalog = source.some(rule => rule.catalog_number) ? source.filter(rule => rule.catalog_number) : source;
  return catalog.map(rule => {
    const advice = state.recommendations.find(item => item.rule_id === rule.rule_id);
    const check = { applicability: 'OPTIONAL', confidence: 0, columns: [], evidence: [], ...rule, ...advice };
    if (['NOT_APPLICABLE', 'CANNOT_EVALUATE'].includes(check.applicability)) check.applicability = 'OPTIONAL';
    return check;
  });
}

function categorizedCheckPicker(selected) {
  const rules = executableChecks();
  const selectedIds = new Set(selected.map(rule => rule.rule_id));
  return checkCatalog.map(group => `<details class="picker-category"><summary>${escapeHtml(group.name)} <span>${group.checks.length}</span></summary><div>${group.checks.map(check => {
    if (check.number === 19) return `<button type="button" class="catalog-subcheck" data-open-business-rules title="${escapeHtml(check.description)}"><span>+ ${escapeHtml(check.name)}</span><small>Open Business Rules</small></button>`;
    const rule = rules.find(item => item.catalog_number === check.number) || (check.number === 5 ? rules.find(item => item.predicate === 'unique_rows') : null);
    const chosen = rule && (selectedIds.has(rule.rule_id) || (check.number === 5 && selected.some(item => item.predicate === 'unique_rows')));
    return `<button type="button" class="catalog-subcheck ${rule ? 'add-optional-check' : ''}" ${rule ? `data-rule-id="${escapeHtml(rule.rule_id)}"` : ''} ${!rule || chosen ? 'disabled' : ''} title="${escapeHtml(check.description)}"><span>${chosen ? '&#10003;' : rule ? '+' : '&#8212;'} ${escapeHtml(check.name)}</span><small>${chosen ? 'Already included' : rule ? 'Configure & add' : 'Not supported by the current engine'}</small></button>`;
  }).join('')}</div></details>`).join('');
}

function renderReview() {
  const target = byId('review-page-content');
  if (!state.dataset) return;
  const catalog = executableChecks();
  const mandatory = catalog.filter(rule => rule.predicate === 'unique_rows');
  const available = catalog.filter(rule => rule.predicate !== 'unique_rows');
  const suggested = state.reviewDraft
    ? state.reviewDraft.cards.map(card => available.find(rule => rule.rule_id === card.id)).filter(Boolean)
    : available.filter(rule => rule.applicability === 'RECOMMENDED');
  const optional = available.filter(rule => !suggested.includes(rule));
  target.innerHTML = `<div class="review-main">
    <section class="mandatory-checks"><h2>Mandatory checks <span>Automatic</span></h2>${mandatory.map(rule => renderRuleCard({ ...rule, mandatory: true })).join('')}</section>
    <div class="checks-heading"><h2>Suggested tests</h2><details class="add-check-picker"><summary>+ Add check</summary><div><p class="check-catalog-label">Available checks</p>${categorizedCheckPicker([...mandatory, ...suggested])}</div></details></div>
    <div id="selected-checks">${suggested.map(renderRuleCard).join('')}</div>
  </div><aside class="review-sidebar">
    <section class="business-highlight">
      <div class="business-upload-actions"><label class="business-upload-button button button-primary"><input id="business-document" type="file"><svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M12 16V3m-5 5 5-5 5 5M4 16v4h16v-4"/></svg>Upload Business Rules</label><small class="muted">Text, PDF, Word, Excel or PowerPoint &middot; up to 10 MB. Documents must contain readable text.</small></div>
      <form class="business-form" id="business-form"><label for="business-input">Write your rules</label><textarea id="business-input" maxlength="100000" rows="6" placeholder="Describe your rules naturally. For example: Product code must start with P. Quantity must be between 1 and 100." aria-describedby="business-input-help"></textarea><small id="business-input-help" class="muted">Rules are interpreted offline without an API key. Use your field names or a clear description and include expected values. No clarification questions are asked. Unclear or unsupported rules are marked not applied. Review the interpreted rules before validation.</small><button class="button button-primary" type="submit">Generate rules</button></form>
      <p id="draft-status" class="muted" role="status" aria-live="polite"></p><div id="business-feedback" role="status" aria-live="polite"></div><div class="business-generated-heading"><h3>Rules ready for review</h3></div><div id="business-document-error"></div><div id="business-rule-list">${renderBusinessRules()}</div>
    </section>

  </aside><nav class="step-navigation review-step-navigation" aria-label="Business rules step navigation"><div class="step-navigation-info"><strong>Ready to validate?</strong><span id="approval-count" aria-live="polite"></span></div><div class="step-navigation-buttons"><button class="button button-quiet" type="button" data-step-previous><span aria-hidden="true">&larr;</span> Previous</button><button id="approve-run" class="button button-primary" type="button">Run validation</button></div></nav>`;
  const businessTarget = byId('business-page-content');
  byId('business-upload-slot').replaceChildren(target.querySelector('.business-upload-actions'));
  businessTarget.replaceChildren(target.querySelector('.business-highlight'), target.querySelector('.review-step-navigation'));
  businessTarget.querySelector('[data-step-previous]').addEventListener('click', () => showPage('review'));
  businessTarget.oninput = () => { saveReviewDraft(); updateApprovalCount(); };
  businessTarget.onchange = () => { saveReviewDraft(); updateApprovalCount(); };
  target.insertAdjacentHTML('beforeend', `<nav class="step-navigation review-step-navigation" aria-label="Choose checks step navigation"><div class="step-navigation-info"><strong>Checks selected</strong><span>Next, optionally add business rules.</span></div><div class="step-navigation-buttons"><button class="button button-quiet" type="button" data-step-previous>&larr; Previous</button><button class="button button-primary" type="button" data-step-business>Next: Business Rules (optional) &rarr;</button></div></nav>`);
  restoreReviewDraft();
  renderBusinessDocumentFailure();
  target.oninput = event => { if (event.target.matches('.column-search')) { const query=event.target.value.trim().toLowerCase(); const picker=event.target.closest('.column-picker'); picker.querySelectorAll('.column-option').forEach(option=>{option.hidden=!option.textContent.toLowerCase().includes(query);}); picker.querySelector('.column-empty').hidden=[...picker.querySelectorAll('.column-option')].some(option=>!option.hidden); return; } syncSuggestedParameters(event.target); saveReviewDraft(); updateApprovalCount(); };
  target.onchange = event => { if (event.target.matches('.reference-file-input')) { uploadReferenceFile(event.target); return; } syncSuggestedParameters(event.target); saveReviewDraft(); updateApprovalCount(); };
  target.onclick = event => {
    if (agentBusy || businessBusy) return;
    if (event.target.closest('[data-step-business], [data-open-business-rules]')) { saveReviewDraft(); showPage('business'); byId('business-input')?.focus({ preventScroll: true }); return; }
    if (event.target.closest('[data-step-previous]')) { showPage('overview'); return; }
    const bulk = event.target.closest('[data-column-action]');
    if (bulk) {
      const picker=bulk.closest('.column-picker');
      const options=[...picker.querySelectorAll('[data-param-column]')];
      options.forEach(option=>{option.checked=bulk.dataset.columnAction==='all';});
      if (options.length) syncSuggestedParameters(options[0]);
      saveReviewDraft(); updateApprovalCount(); return;
    }
    const remove = event.target.closest('.delete-test');
    if (remove) {
      saveReviewDraft();
      state.reviewDraft.cards = state.reviewDraft.cards.filter(card => card.id !== remove.dataset.ruleId);
      renderReview(); saveReviewDraft(); toast('Test removed. You can add it again with + Add check.'); return;
    }
    const add = event.target.closest('.add-optional-check');
    if (add) {
      saveReviewDraft();
      state.reviewDraft.cards.unshift({ id: add.dataset.ruleId, checked: true, controls: [] });
      renderReview(); saveReviewDraft();
      const newCard = byId('selected-checks').firstElementChild;
      newCard?.scrollIntoView({ block: 'center', behavior: 'smooth' });
      newCard?.querySelector('[data-param-key], .rule-toggle')?.focus({ preventScroll: true });
    }
  };
  byId('business-form').addEventListener('submit', parseBusinessRule);
  byId('business-document').addEventListener('change', uploadBusinessDocument);
  bindBusinessRuleRemoval();
  byId('approve-run').addEventListener('click', runValidation);
  updateApprovalCount();
  saveReviewDraft();
}

function bindBusinessRuleRemoval() {
  byId('business-rule-list').querySelectorAll('.edit-business-rule').forEach(button => button.addEventListener('click', () => {
    if (agentBusy || businessBusy) return;
    const item = button.closest('.business-rule-item');
    const form = item.querySelector('.business-rule-editor');
    form.hidden = false;
    button.hidden = true;
    form.querySelector('textarea').focus();
  }));
  byId('business-rule-list').querySelectorAll('.cancel-business-edit').forEach(button => button.addEventListener('click', () => {
    if (agentBusy || businessBusy) return;
    const item = button.closest('.business-rule-item');
    const form = item.querySelector('.business-rule-editor');
    form.hidden = true;
    form.querySelector('textarea').value = state.businessRules[Number(form.dataset.index)].original_requirement;
    form.querySelector('[role="alert"]').textContent = '';
    item.querySelector('.edit-business-rule').hidden = false;
    item.querySelector('.edit-business-rule').focus();
  }));
  byId('business-rule-list').querySelectorAll('.business-rule-editor').forEach(form => form.addEventListener('submit', saveBusinessRuleEdit));
  byId('business-rule-list').querySelectorAll('.remove-business-rule').forEach(button => button.addEventListener('click', () => {
    if (agentBusy || businessBusy) return;
    state.businessRules.splice(Number(button.dataset.index), 1);
    byId('business-rule-list').innerHTML = renderBusinessRules();
    bindBusinessRuleRemoval();
    updateApprovalCount(); saveReviewDraft();
  }));
}

function applyBusinessBatch(result) {
  let added = 0;
  for (const rule of result.rules || []) {
    if (!state.businessRules.some(existing => JSON.stringify(existing) === JSON.stringify(rule))) { state.businessRules.push(rule); added++; }
  }
  state.businessClarifications = result.unresolved || [];
  byId('business-feedback').innerHTML = `<div class="business-response ${added ? 'parsed' : ''}"><strong>${added} rule${added === 1 ? '' : 's'} added. Review the interpretations below.</strong><p>${escapeHtml(result.message || '')}</p></div>`;
  renderClarificationChoices();
  byId('business-rule-list').innerHTML = renderBusinessRules();
  bindBusinessRuleRemoval();
  updateApprovalCount(); saveReviewDraft();
}

function renderClarificationChoices() {
  const feedback = byId('business-feedback');
  if (!feedback) return;
  feedback.querySelector('.business-clarifications')?.remove();
  const pending = state.businessClarifications || [];
  if (!pending.length) return;
  const container = document.createElement('div'); container.className = 'business-clarifications';
  container.innerHTML = pending.map((item,index) => `<section class="business-clarification"><strong>Not applied: ${escapeHtml(item.requirement)}</strong><p>${escapeHtml(item.reason || 'This rule could not be interpreted automatically.')}</p>${item.candidate_columns?.length ? `<small class="muted">Matching fields: ${escapeHtml(item.candidate_columns.map(column => column.name).join(', '))}</small>` : ''}<button type="button" class="button button-quiet" data-exclude-unapplied="${index}">Exclude this rule</button></section>`).join('');
  feedback.appendChild(container);
  container.querySelectorAll('[data-exclude-unapplied]').forEach(button => button.onclick = () => {
    if (agentBusy || businessBusy) return;
    const item = pending[Number(button.dataset.excludeUnapplied)];
    const editor = byId('business-input');
    const lines = editor.value.split('\n');
    const index = lines.findIndex(line => line.trim() === item.requirement.trim());
    if (index >= 0) lines.splice(index, 1);
    editor.value = lines.join('\n');
    state.businessClarifications = state.businessClarifications.filter(entry => entry !== item);
    renderClarificationChoices(); updateApprovalCount(); saveReviewDraft();
  });
}

function renderBusinessDocumentFailure() {
  const target = byId('business-document-error');
  if (!target) return;
  const failure = state.businessDocumentFailure;
  target.innerHTML = failure ? `<div class="notice error" role="alert"><strong>${escapeHtml(failure.filename)} was not added.</strong><p>${escapeHtml(failure.message)}</p><p>Upload the document again, paste its rules, or explicitly exclude this document before validation.</p><button type="button" class="button button-quiet" id="exclude-failed-document">Continue without this document</button></div>` : '';
  if (failure) byId('exclude-failed-document').onclick = () => {
    if (agentBusy || businessBusy) return;
    state.businessDocumentFailure = null; renderBusinessDocumentFailure(); saveReviewDraft(); updateApprovalCount();
  };
}
async function uploadBusinessDocument(event) {
  const input = event.target;
  const file = input.files[0];
  if (!file || businessBusy || agentBusy) return;
  setBusinessBusy(true);
  byId('business-feedback').textContent = `Reading ${file.name} and matching rules to your dataset...`;
  try {
    if (file.size > 10 * 1024 * 1024) throw new Error('Choose a document up to 10 MB.');
    const body = new FormData(); body.append('file', file);
    const result = await api(`/api/datasets/${encodeURIComponent(state.dataset.dataset_id)}/business-rules/document`, {method:'POST', body});
    const unresolved = (result.unresolved || []).map(item => item.requirement).join('\n');
    if (unresolved) byId('business-input').value = [byId('business-input').value.trim(), unresolved].filter(Boolean).join('\n');
    state.businessDocumentFailure = null;
    applyBusinessBatch(result);
  } catch (error) {
    state.businessDocumentFailure = {filename: file.name, message: error.message};
    byId('business-feedback').textContent = error.message;
  } finally {
    input.value = ''; renderBusinessDocumentFailure(); setBusinessBusy(false); saveReviewDraft();
  }
}

async function uploadReferenceFile(input) {
  const file = input.files[0];
  if (!file || agentBusy || businessBusy) return;
  const card = input.closest('.rule-card');
  const status = card.querySelector('.reference-status');
  if (file.size > 50 * 1024 * 1024 || !/\.(csv|parquet|pq|tsv|txt|psv|xlsx|xls|xlsm|ods|json|jsonl|ndjson|xml|feather|arrow|orc|avro)$/i.test(file.name)) { status.textContent = 'Choose a supported data file up to 50 MB.' + (card.dataset.referenceName ? ' Still using ' + card.dataset.referenceName + '.' : ' No reference file is selected.'); input.value = ''; return; }
  setBusinessBusy(true); input.disabled = true; status.textContent = 'Uploading reference file...';
  try {
    const data = new FormData(); data.append('file', file);
    const payload = await api('/api/datasets', {method:'POST', body:data});
    const textarea = card.querySelector('[data-value-type="json"]');
    const parameters = JSON.parse(textarea.value);
    parameters.reference_dataset_id = payload.dataset.dataset_id;
    parameters.reference_filename = file.name;
    textarea.value = JSON.stringify(parameters, null, 2);
    card.dataset.referenceName = file.name;
    status.textContent = 'Reference ready: ' + file.name;
    saveReviewDraft();
  } catch (error) { status.textContent = error.message + (card.dataset.referenceName ? ' Replacement was not added; still using ' + card.dataset.referenceName + '.' : ' No reference file is selected.'); }
  finally { setBusinessBusy(false); input.value = ''; }
}

function renderRuleCard(rule) {
  const checked = rule.mandatory || rule.applicability === 'RECOMMENDED' ? 'checked' : '';
  const disabled = rule.mandatory || rule.applicability === 'NOT_APPLICABLE' ? 'disabled' : '';
  const confidence = rule.applicability === 'NOT_APPLICABLE' ? '—' : `${Math.round(rule.confidence * 100)}% confidence`;
  const evidence = (rule.evidence || []).map(escapeHtml).join(' · ');
  const aiAdvice = rule.ai_assessment ? `<div class="rule-evidence ai-evidence"><strong>AI advisory:</strong> ${escapeHtml(rule.ai_assessment.reason)} ${rule.ai_assessment.evidence.map(escapeHtml).join(' · ')}</div>` : '';
  return `<article class="rule-card" data-rule-id="${escapeHtml(rule.rule_id)}"><div class="rule-card-head"><input class="rule-toggle" type="checkbox" ${checked} ${disabled} aria-label="Enable ${escapeHtml(rule.name)}"><div class="rule-card-info"><div class="rule-title-line"><strong>${escapeHtml(rule.name)}</strong><span class="status-chip ${rule.applicability.toLowerCase()}">${escapeHtml(rule.mandatory ? 'Mandatory' : ({ RECOMMENDED: 'Suggested', REQUIRES_USER_INPUT: 'Needs settings', OPTIONAL: 'Optional', NOT_APPLICABLE: 'Unavailable' })[rule.applicability] || rule.applicability)}</span><span class="rule-confidence">${confidence}</span></div></div>${rule.mandatory ? '' : `<button type="button" class="icon-button delete-test" data-rule-id="${escapeHtml(rule.rule_id)}" aria-label="Delete ${escapeHtml(rule.name)} test" title="Remove test">${deleteIcon}</button>`}</div><details class="check-details"><summary>Details</summary><p class="rule-description">${escapeHtml(rule.description)}</p><div class="rule-evidence">${evidence}${rule.columns?.length ? ` · Candidate columns: ${rule.columns.map(escapeHtml).join(', ')}` : ''}</div>${aiAdvice}</details><div class="config-form">${renderRuleConfig(rule)}</div></article>`;
}

function columnSelect(label, predicate, options = {}) {
  const columns = state.profile.columns;
  const numeric = options.numeric ? columns.filter((column) => column.statistics?.mean !== null && column.statistics?.mean !== undefined) : columns;
  const preferred = options.preferred;
  const opts = numeric.map((column) => `<option value="${escapeHtml(column.name)}" ${column.name === preferred ? 'selected' : ''}>${escapeHtml(column.name)}</option>`).join('');
  return `<label class="config-field ${options.wide ? 'wide' : ''}">${label}<select data-config="${predicate}" data-key="${options.key || 'column'}">${opts}</select></label>`;
}

function renderRuleConfig(rule) {
  switch (rule.predicate) {
    case 'not_null':
    case 'unique': {
      const preferred = rule.columns?.length ? rule.columns : rule.predicate === 'not_null' ? state.profile.columns.map((column) => column.name) : [];
      const options = state.profile.columns.map((column) => `<option value="${escapeHtml(column.name)}" ${preferred.includes(column.name) ? 'selected' : ''}>${escapeHtml(column.name)}</option>`).join('');
      return `<label class="config-field wide">Choose columns (hold Ctrl to select more than one)<select multiple data-config="${rule.predicate}" data-key="columns">${options}</select></label>`;
    }
    case 'unique_rows': return '<span class="config-placeholder">Evaluates complete rows as-is. No column selection required.</span>';
    case 'semantic_format': return columnSelect('Evidence-backed format column', rule.predicate, { preferred: rule.columns?.[0] });
    case 'numeric_bounds': return `${columnSelect('Column', rule.predicate, { numeric: true, preferred: rule.columns?.[0] })}<label class="config-field">Minimum · optional<input type="number" step="any" data-config="numeric_bounds" data-key="minimum" placeholder="No minimum assumed"></label><label class="config-field">Maximum · optional<input type="number" step="any" data-config="numeric_bounds" data-key="maximum" placeholder="No maximum assumed"></label><span class="config-placeholder">Define at least one explicit bound before enabling this check.</span>`;
    case 'allowed_values':
    case 'in_reference': return `${columnSelect('Column', rule.predicate, { preferred: rule.columns?.[0] })}<label class="config-field wide">${rule.predicate === 'in_reference' ? 'Reference values' : 'Approved values'} · comma-separated<input data-config="${rule.predicate}" data-key="${rule.predicate === 'in_reference' ? 'reference_values' : 'values'}" placeholder="Enter values you approve"></label>`;
    case 'column_comparison': return `<label class="config-field">First column<select data-config="${rule.predicate}" data-key="left">${state.profile.columns.map((column) => `<option value="${escapeHtml(column.name)}">${escapeHtml(column.name)}</option>`).join('')}</select></label><label class="config-field">Operator<select data-config="${rule.predicate}" data-key="operator"><option value="<=">≤</option><option value="<">&lt;</option><option value="==">=</option><option value="!=">≠</option><option value=">=">≥</option><option value=">">&gt;</option></select></label><label class="config-field wide">Second column<select data-config="${rule.predicate}" data-key="right">${state.profile.columns.map((column) => `<option value="${escapeHtml(column.name)}">${escapeHtml(column.name)}</option>`).join('')}</select></label>`;
    case 'regex_match': return `${columnSelect('Text column', rule.predicate, { preferred: rule.columns?.[0] })}<label class="config-field wide">Approved regular expression<input data-config="${rule.predicate}" data-key="pattern" placeholder="e.g. ^[A-Z]{2}-\\d+$"></label>`;
    case 'castable_type': return `${columnSelect('Column', rule.predicate, { preferred: rule.columns?.[0] })}<label class="config-field">Target type<select data-config="${rule.predicate}" data-key="target_type"><option value="number">Number</option><option value="date">Date / time</option><option value="boolean">Boolean</option></select></label>`;
    default: return renderPluginConfig(rule);
  }
}

function syncSuggestedParameters(control) {
  if (!control.matches('[data-param-key]')) return;
  const card = control.closest('.rule-card');
  const store = card.querySelector('[data-value-type="json"]');
  const parameters = JSON.parse(store.value);
  const key = control.dataset.paramKey;
  if (control.dataset.paramJson) {
    try { const value = JSON.parse(control.value); if (!value || Array.isArray(value) || typeof value !== 'object' || !Object.keys(value).length) throw Error(); parameters[key] = value; control.setCustomValidity(''); }
    catch { control.setCustomValidity('Enter a non-empty JSON object mapping column names to expected types.'); return; }
  }
  else if (control.dataset.paramColumn) parameters[key] = [...card.querySelectorAll('[data-param-column]')].filter(item=>item.dataset.paramKey===key&&item.checked).map(item=>item.dataset.paramColumn);
  else if (control.dataset.paramArray) parameters[key] = control.value.split(',').map(v=>v.trim()).filter(Boolean).map(v=>control.dataset.paramNumeric==='true'?Number(v):v);
  else if (control.type==='checkbox') parameters[key]=control.checked;
  else if (control.type==='number') parameters[key]=control.value===''?null:Number(control.value);
  else parameters[key]=control.value;
  store.value=JSON.stringify(parameters);
  if (control.dataset.paramColumn) { const picker=control.closest('.column-picker'); if(picker) picker.querySelector('.column-selection-count').textContent=`${parameters[key].length} of ${state.profile.columns.length} selected`; }
}

function renderPluginConfig(rule) {
  const schema = rule.configuration || {};
  const defaults = rule.configuration_defaults || {};
  const fields = Object.entries(schema).map(([key, descriptor]) => {
    if (!descriptor || typeof descriptor !== 'object' || !descriptor.type) return '';
    if (descriptor.type === 'json') {
      const parameters = defaults[key] ?? {};
      const columnKeys = ['column','left','right','when_column','reference_column'];
      const listColumnKeys = ['columns','keys'];
      const fields = Object.entries(parameters).filter(([name]) => !['reference_dataset_id','reference_filename','now'].includes(name)).map(([name,value]) => {
        const label = ({columns:'Columns to check',keys:'Matching columns',column:'Column',left:'First column',right:'Second column',maximum_hours:'How recent must the data be? (hours)',expected:'Expected number of records',baseline_counts:'Previous record counts',maximum_deviation_percent:'Allowed change (%)',iqr_multiplier:'Outlier sensitivity',maximum_distance:'Allowed distribution change',when_column:'When this column',when_values:'Has these values',value:'Expected value',reference_column:'Column in the reference file'})[name] || name.replaceAll('_',' ');
        if (name === 'schema') return `<label class="config-field wide">Expected schema<textarea data-param-key="schema" data-param-json="true" rows="5" aria-describedby="schema-help">${escapeHtml(JSON.stringify(value, null, 2))}</textarea><small id="schema-help">Map expected column names to types, for example {"id": "StringType", "amount": "DoubleType"}. Add or remove fields to define your expectation.</small></label>`;
        if (columnKeys.includes(name)) return `<label class="config-field">${label}<select data-param-key="${name}"><option value="">Choose a column</option>${state.profile.columns.map(c=>`<option value="${escapeHtml(c.name)}" ${c.name===value?'selected':''}>${escapeHtml(c.name)}</option>`).join('')}</select></label>`;
        if (listColumnKeys.includes(name)) return `<div class="config-field wide"><span>${label}</span><details class="column-picker"><summary><span class="column-selection-count">${(value||[]).length} of ${state.profile.columns.length} selected</span><span class="column-picker-edit">Change columns <span aria-hidden="true">&#9662;</span></span></summary><div class="column-picker-panel"><input class="column-search" type="search" placeholder="Search columns&#8230;" aria-label="Search ${label.toLowerCase()}"><div class="column-picker-actions"><button type="button" data-column-action="all">Select all</button><button type="button" data-column-action="clear">Clear all</button></div><div class="column-options">${state.profile.columns.map(c=>`<label class="column-option"><input type="checkbox" data-param-key="${name}" data-param-column="${escapeHtml(c.name)}" ${(value||[]).includes(c.name)?'checked':''}><span>${escapeHtml(c.name)}</span></label>`).join('')}<p class="column-empty" hidden>No matching columns.</p></div></div></details></div>`;
        if (Array.isArray(value)) return `<label class="config-field wide">${label}<input data-param-key="${name}" data-param-array="true" data-param-numeric="${['bins','baseline_counts'].includes(name)}" value="${escapeHtml(value.join(', '))}" placeholder="Separate values with commas"></label>`;
        if (typeof value==='boolean') return `<label class="config-field">${label}<input type="checkbox" data-param-key="${name}" ${value?'checked':''}></label>`;
        return `<label class="config-field">${label}<input data-param-key="${name}" type="${typeof value==='number'||value===null?'number':'text'}" step="any" value="${escapeHtml(value??'')}" placeholder="Tell us your expectation"></label>`;
      }).join('');
      return `<p class="config-placeholder wide">Suggested from your file. Adjust only if needed.</p>${fields}<textarea hidden data-config="${escapeHtml(rule.predicate)}" data-key="${escapeHtml(key)}" data-value-type="json">${escapeHtml(JSON.stringify(parameters))}</textarea>${parameters.reference_dataset_id !== undefined ? '<label class="config-field wide">Upload the file to compare with<input type="file" class="reference-file-input" accept=".csv,.parquet,.pq,.tsv,.txt,.psv,.xlsx,.xls,.xlsm,.ods,.json,.jsonl,.ndjson,.xml,.feather,.arrow,.orc,.avro"><span class="reference-status" role="status"></span></label>' : ''}`;
    }
    const required = descriptor.required ? ' · required' : '';
    const label = `${escapeHtml(descriptor.description || key.replaceAll('_', ' '))}${required}`;
    if (descriptor.type === 'column') {
      const options = state.profile.columns.map((column) => `<option value="${escapeHtml(column.name)}" ${column.name === defaults[key] ? 'selected' : ''}>${escapeHtml(column.name)}</option>`).join('');
      return `<label class="config-field">${label}<select data-config="${escapeHtml(rule.predicate)}" data-key="${escapeHtml(key)}">${options}</select></label>`;
    }
    if (descriptor.type === 'enum' && Array.isArray(descriptor.values)) {
      return `<label class="config-field">${label}<select data-config="${escapeHtml(rule.predicate)}" data-key="${escapeHtml(key)}">${descriptor.values.map((value) => `<option value="${escapeHtml(value)}" ${value === defaults[key] ? 'selected' : ''}>${escapeHtml(value)}</option>`).join('')}</select></label>`;
    }
    if (descriptor.type === 'boolean') {
      return `<label class="config-field"><span>${label}</span><input type="checkbox" data-config="${escapeHtml(rule.predicate)}" data-key="${escapeHtml(key)}" ${defaults[key] ? 'checked' : ''}></label>`;
    }
    if (descriptor.type === 'array') {
      return `<label class="config-field wide">${label}<textarea data-config="${escapeHtml(rule.predicate)}" data-key="${escapeHtml(key)}" placeholder="Separate values with commas"></textarea></label>`;
    }
    const inputType = ['integer', 'number'].includes(descriptor.type) ? 'number' : 'text';
    const step = descriptor.type === 'integer' ? '1' : 'any';
    const minimum = descriptor.minimum !== undefined ? `min="${escapeHtml(descriptor.minimum)}"` : '';
    const defaultValue = defaults[key] ?? '';
    return `<label class="config-field">${label}<input type="${inputType}" step="${step}" ${minimum} data-config="${escapeHtml(rule.predicate)}" data-key="${escapeHtml(key)}" value="${escapeHtml(defaultValue)}"></label>`;
  }).filter(Boolean);
  return fields.length ? fields.join('') : '<span class="config-placeholder">No configuration is required.</span>';
}

function renderBusinessRules() {
  return state.businessRules.map((rule, index) => `<div class="business-rule-item"><strong>${escapeHtml(rule.original_requirement)}</strong><br><span>${escapeHtml(formatBusinessRule(rule))}</span>${rule.mapping_reason ? `<small class="business-mapping-reason">${escapeHtml(rule.mapping_reason)}</small>` : ''}<div class="business-rule-actions"><button type="button" class="edit-business-rule" data-index="${index}" aria-label="Edit business rule" title="Edit business rule"><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m16 3 5 5-12 12-6 1 1-6L16 3Zm-2 2 5 5"/></svg></button><button type="button" class="remove-business-rule icon-button" data-index="${index}" aria-label="Delete business rule" title="Remove business rule">${deleteIcon}</button></div><form class="business-rule-editor" data-index="${index}" hidden><label>Rule condition<textarea required maxlength="100000" rows="3">${escapeHtml(rule.original_requirement)}</textarea></label><p role="alert"></p><div class="business-edit-buttons"><button type="submit" class="button button-primary">Save changes</button><button type="button" class="button button-quiet cancel-business-edit">Cancel</button></div></form></div>`).join('');
}

async function saveBusinessRuleEdit(event) {
  event.preventDefault();
  if (agentBusy || businessBusy) return;
  const form = event.currentTarget;
  const text = form.querySelector('textarea').value.trim();
  const error = form.querySelector('[role="alert"]');
  if (!text) { error.textContent = 'Enter a rule condition.'; return; }
  const index = Number(form.dataset.index);
  setBusinessBusy(true);
  error.textContent = '';
  try {
    const result = await api(`/api/datasets/${encodeURIComponent(state.dataset.dataset_id)}/business-rules/batch`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({text})});
    if (result.rules?.length !== 1 || result.unresolved?.length) {
      error.textContent = result.unresolved?.[0]?.reason || 'Enter one supported rule condition. Your original rule is unchanged.';
      return;
    }
    state.businessRules[index] = result.rules[0];
    saveReviewDraft();
    byId('business-rule-list').innerHTML = renderBusinessRules();
    bindBusinessRuleRemoval();
    const edited = byId('business-rule-list').querySelectorAll('.edit-business-rule')[index];
    edited?.focus();
    toast('Rule updated. Review its interpretation before running validation.');
  } catch (failure) { error.textContent = failure.message; }
  finally { setBusinessBusy(false); }
}

function formatBusinessRule(rule) {
  if (rule.type === 'compound') return rule.rules.map(child => `(${formatBusinessRule(child)})`).join(rule.mode === 'all' ? ' AND ' : ' OR ');
  if (rule.type === 'conditional') return `If ${formatBusinessRule(rule.when)}, then ${formatBusinessRule(rule.then)}`;
  if (rule.type === 'literal_comparison') return `${rule.column} ${rule.operator} ${JSON.stringify(rule.value)}`;
  if (rule.type === 'text_match') return `${rule.column} ${rule.match.replaceAll('_', ' ')} ${JSON.stringify(rule.value)}`;
  if (rule.type === 'null_check') return `${rule.column} must be ${rule.is_null ? 'null' : 'not null'}`;
  if (rule.type === 'text_length') return `${rule.column} must contain ${{'==':'exactly', '>=':'at least', '<=':'at most'}[rule.operator]} ${rule.length} characters`;
  if (rule.type === 'data_type') return `${rule.column} must contain valid ${rule.expected_type} values`;
  if (rule.type === 'comparison') return `${rule.column} ${rule.operator} ${rule.value}`;
  if (rule.type === 'cross_column') return `${rule.left} ${rule.operator} ${rule.right}`;
  if (rule.type === 'starts_with') return `${rule.column} must start with ${JSON.stringify(rule.prefix)} (case-sensitive)`;
  if (rule.type === 'not_empty') return `${rule.column} must be non-null and non-empty`;
  if (rule.type === 'unique') return `${rule.column} must be unique`;
  if (rule.type === 'numeric_comparison') return `${rule.column} must be numeric and ${rule.operator} ${rule.value}`;
  if (rule.type === 'allowed_values') return `${rule.column} must be one of: ${rule.values.join(', ')}`;
  if (rule.type === 'not_future') return `${rule.column} must be a valid timestamp at or before the validation time`;
  if (rule.type === 'arithmetic') return `${rule.column} = ${rule.multiply.join(' \u00d7 ')} \u2212 ${rule.subtract} (tolerance ${rule.tolerance})`;
  if (rule.type === 'conditional_dates') return `When ${rule.when_column} = ${rule.when_value}: ${rule.column} is required and must be at or after ${rule.not_before}`;
  return 'Structured business condition';
}

async function generateBusinessRules() {
  const input = byId('business-input');
  const text = input.value.trim();
  if (!text || businessBusy || agentBusy) return;
  setBusinessBusy(true);
  byId('business-feedback').textContent = 'Reading your requirements and matching rules to your dataset...';
  try {
    const result = await api(`/api/datasets/${encodeURIComponent(state.dataset.dataset_id)}/business-rules/batch`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({text})});
    // Only interpreted requirements leave the editor. Anything unresolved must be
    // corrected or explicitly removed by the user before a run can be approved.
    input.value = (result.unresolved || []).map(item => item.requirement).join('\n');
    applyBusinessBatch(result);
    if (input.value.trim()) {
      toast('Some requirements could not be added. Correct or remove the remaining text before validation.', true);
    } else {
      toast('Rules added. Review their interpretations, then click Run validation.');
    }
    byId('business-rule-list').scrollIntoView({block:'nearest'});
  } catch (error) { byId('business-feedback').textContent = error.message; toast(error.message, true); }
  finally { setBusinessBusy(false); saveReviewDraft(); }
}
async function parseBusinessRule(event) {
  event.preventDefault();
  await generateBusinessRules();
}

function readConfiguration(card, predicate) {
  const config = {};
  card.querySelectorAll(`[data-config="${predicate}"]`).forEach((control) => {
    const key = control.dataset.key;
    let value = control.value;
    if (control.type === 'checkbox') value = control.checked;
    if (control.multiple) value = [...control.selectedOptions].map((option) => option.value);
    if (control.dataset.valueType === 'json') {
      try { value = JSON.parse(value); if (!value || Array.isArray(value) || typeof value !== 'object') throw new Error(); } catch { throw new Error('Check parameters must be a valid JSON object.'); }
    } else if (control.tagName === 'TEXTAREA' || ['values', 'reference_values'].includes(key)) value = value.split(',').map((item) => item.trim()).filter(Boolean);
    if (control.type === 'number') {
      if (value !== '') value = Number(value);
      else return;
    }
    if (value !== '' && value !== undefined) config[key] = value;
  });

  return config;
}

function updateApprovalCount() {
  updateCheckSettings();
  const count = [...document.querySelectorAll('.rule-card .rule-toggle:checked')].length + state.businessRules.length;
  const catalog = executableChecks();
  const needsSettings = [...document.querySelectorAll('.rule-card')].filter(card => card.querySelector('.rule-toggle').checked && checkSettingsError(card, catalog.find(rule => rule.rule_id === card.dataset.ruleId))).length;
  const summary = byId('approval-count');
  const runButton = byId('approve-run');
  const pendingText = !!byId('business-input')?.value.trim();
  if (runButton) {
    runButton.disabled = (count === 0 && !pendingText) || businessBusy || agentBusy || !!state.businessDocumentFailure;
    if (!agentBusy) runButton.textContent = pendingText ? 'Generate & review rules' : 'Run validation';
  }
  if (summary) summary.textContent = state.businessDocumentFailure ? 'The uploaded business document was not added. Retry or explicitly exclude it before validation.' : pendingText ? 'The rules in the editor have not been added. Generate and review them before validation.' : count ? `${count} check${count === 1 ? '' : 's'} selected. ${needsSettings ? `Complete settings for ${needsSettings} check${needsSettings === 1 ? '' : 's'}.` : 'Ready to run validation.'}` : 'Select at least one check to continue.';
}

function checkSettingsError(card, rule) {
  if (rule.predicate === 'unique_rows') return '';
  try {
    const config = readConfiguration(card, rule.predicate);
    const missing = value => value == null || (typeof value === 'string' && !value.trim()) || (Array.isArray(value) && !value.length);
    for (const control of card.querySelectorAll('[data-config], [data-param-key]')) {
      if (!control.checkValidity()) return control.validationMessage;
    }
    if (['not_null', 'unique'].includes(rule.predicate) && missing(config.columns)) return 'Choose at least one column.';
    if (rule.predicate === 'numeric_bounds') {
      if (missing(config.minimum) && missing(config.maximum)) return 'Enter a minimum or maximum.';
      if (config.minimum != null && config.maximum != null && config.minimum > config.maximum) return 'Minimum must be less than or equal to maximum.';
    }
    if (['allowed_values', 'in_reference'].includes(rule.predicate) && missing(config.values || config.reference_values)) return 'Enter at least one approved value.';
    if (rule.predicate === 'regex_match' && missing(config.pattern)) return 'Enter a regular expression.';
    for (const [key, descriptor] of Object.entries(rule.configuration || {})) {
      if (descriptor.required && missing(config[key])) return `Provide ${key.replaceAll('_', ' ')}.`;
    }
    if (rule.catalog_number) {
      const parameters = config.parameters;
      if (!parameters || typeof parameters !== 'object') return 'Complete the check settings.';
      const expected = rule.parameter_example || rule.configuration?.parameters?.example || rule.configuration_defaults?.parameters || {};
      for (const key of Object.keys(expected)) {
        if (rule.catalog_number === 7 && ['minimum', 'maximum'].includes(key)) continue;
        if (missing(parameters[key])) return `Provide ${key.replaceAll('_', ' ')}.`;
      }
      if (rule.catalog_number === 7) {
        if (missing(parameters.minimum) && missing(parameters.maximum)) return 'Enter a minimum or maximum.';
        if (parameters.minimum != null && parameters.maximum != null && parameters.minimum > parameters.maximum) return 'Minimum must be less than or equal to maximum.';
      }
      if (parameters.reference_dataset_id === 'UPLOAD_REFERENCE_FILE') return 'Upload a reference file for this check.';
      for (const [key, value] of Object.entries(parameters)) {
        if (typeof value === 'number' && !Number.isFinite(value)) return `Enter a valid number for ${key.replaceAll('_', ' ')}.`;
        if (Array.isArray(value) && value.some(item => typeof item === 'number' && !Number.isFinite(item))) return `Enter valid numbers for ${key.replaceAll('_', ' ')}.`;
      }
      if (parameters.pattern) {
        try { new RegExp(parameters.pattern); } catch { return 'Enter a valid regular expression.'; }
      }
    }
    return '';
  } catch (error) { return error.message; }
}
function updateCheckSettings() {
  const catalog = executableChecks();
  byId('review-page-content').querySelectorAll('.rule-card').forEach(card => {
    const rule = catalog.find(item => item.rule_id === card.dataset.ruleId);
    if (!rule) return;
    const error = checkSettingsError(card, rule);
    const badge = card.querySelector('.status-chip');
    if (badge && rule.predicate !== 'unique_rows') {
      badge.textContent = error ? 'Needs settings' : 'Ready';
      badge.classList.remove('requires_user_input', 'recommended', 'optional');
      badge.classList.add(error ? 'requires_user_input' : 'recommended');
      badge.title = error || 'Required settings are complete.';
    }
    if (!error || !card.querySelector('.rule-toggle').checked) {
      card.classList.remove('settings-invalid');
      card.querySelector('.check-settings-error')?.remove();
    }
  });
}
function validateCheckSettings() {
  const catalog = executableChecks();
  let firstInvalid = null;
  let firstMessage = '';
  byId('review-page-content').querySelectorAll('.rule-card').forEach(card => {
    if (!card.querySelector('.rule-toggle').checked) return;
    const rule = catalog.find(item => item.rule_id === card.dataset.ruleId);
    if (!rule) return;
    const error = checkSettingsError(card, rule);
    card.querySelector('.check-settings-error')?.remove();
    card.classList.toggle('settings-invalid', !!error);
    if (error) {
      const message = document.createElement('p');
      message.className = 'check-settings-error'; message.setAttribute('role', 'alert'); message.textContent = error;
      card.appendChild(message);
      if (!firstInvalid) { firstInvalid = card; firstMessage = `${rule.name}: ${error}`; }
    }
  });
  updateCheckSettings();
  if (!firstInvalid) return true;
  toast(firstMessage, true);
  firstInvalid.scrollIntoView({block:'center'});
  const picker = firstInvalid.querySelector('.column-picker');
  if (picker) picker.open = true;
  firstInvalid.querySelector('[data-param-key], .reference-file-input, [data-config]:not([hidden])')?.focus();
  return false;
}

async function runValidation(event) {
  if (agentBusy || businessBusy) return;
  if (document.querySelector('.business-rule-editor:not([hidden])')) { toast('Save or cancel your rule edit before running validation.', true); return; }
  if (state.businessDocumentFailure) { renderBusinessDocumentFailure(); toast('The business document was not added. Retry or exclude it before validation.', true); return; }
  if (byId('business-input')?.value.trim()) {
    await generateBusinessRules();
    return; // Newly generated interpretations require a separate approval click.
  }
  saveReviewDraft();
  const button = event.currentTarget;
  let selected;
  try { selected = [...document.querySelectorAll('.rule-card')].flatMap((card) => {
    if (!card.querySelector('.rule-toggle').checked) return [];
    const recommendation = executableChecks().find((item) => item.rule_id === card.dataset.ruleId);
    return [{ rule_id: card.dataset.ruleId, configuration: readConfiguration(card, recommendation.predicate) }];
  }); } catch (error) { toast(error.message, true); return; }
  if (!selected.length && !state.businessRules.length) { toast('Select and configure at least one check before approving.', true); return; }
  if (!validateCheckSettings()) return;
  setLoading(button, true, 'Validating…');
  try {
    agentProgress(4, 'Processing your tests and business rules', 'Checking records and preparing accepted rows, rejected rows, and your quality report.', true);
    const payload = await api('/api/runs', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ dataset_id: state.dataset.dataset_id, selected_rules: selected, business_rules: state.businessRules, approved: true, accepted_name: state.reviewDraft?.acceptedName?.trim() || undefined, rejected_name: state.reviewDraft?.rejectedName?.trim() || undefined }),
    });
    agentProgress(6, 'Processing complete', 'Accepted records, rejected records, and your quality report are ready.');
    state.lastRun = payload;
    renderResults(payload);
    showPage('results');
    toast(`Validation complete · ${numberFormat(payload.rejected_count)} rejected`);
  } catch (error) { agentProgress(3, 'Processing could not finish', error.message + ' Review settings and try again.', false, true); toast(error.message, true); }
  finally { setLoading(button, false); }
}

function renderRegistry() {
  const target = byId('rules-page-content');
  const total = checkCatalog.reduce((count, group) => count + group.checks.length, 0);
  byId('catalog-help').textContent = `Explore ${total} data quality checks, grouped into ${checkCatalog.length} categories.`;
  const catalog = checkCatalog.map((group) => `<section class="catalog-category"><div class="section-head"><h2>${escapeHtml(group.name)}</h2><span>${group.checks.length} checks</span></div><div class="catalog-checks">${group.checks.map(check => `<article class="registry-item"><div><h3>${check.number}. ${escapeHtml(check.name)}</h3></div><p>${escapeHtml(check.description)}</p></article>`).join('')}</div></section>`).join('');
  target.innerHTML = `<div class="catalog-intro"><strong>${total} checks across ${checkCatalog.length} categories</strong><p>Explore the data quality check catalog below. All 19 checks can run with the required expectations and reference data. Configure them from + Add check.</p></div>${catalog}`;
}

let historyRequest = 0;
async function renderHistory() {
  const request = ++historyRequest;
  const userId = state.userId;
  const target = byId('history-page-content');
  target.innerHTML = '<div class="empty-state" role="status">Loading your checked files…</div>';
  try {
    const runs = await api('/api/runs');
    if (request !== historyRequest || userId !== state.userId) return;
    if (!runs.length) { target.innerHTML = '<div class="empty-state">No files checked yet. Upload a file and run checks to see it here.</div>'; return; }
    target.innerHTML = `<div class="column-table-wrap"><table class="history-table"><thead><tr><th>File name</th><th>Checked on</th><th>Score</th><th>Accepted</th><th>Rejected</th><th>Run ID</th></tr></thead><tbody>${runs.map((run) => `<tr data-run-id="${escapeHtml(run.run_id)}"><td><button type="button" class="text-button history-open">${escapeHtml(run.dataset_filename || run.dataset_name)}</button></td><td>${escapeHtml(new Date(run.validated_at).toLocaleString())}</td><td class="history-score">${run.score ? `${Number(run.score.value).toFixed(1)}` : '—'}</td><td>${numberFormat(run.accepted_count)}</td><td>${numberFormat(run.rejected_count)}</td><td>${escapeHtml(run.run_id.slice(0, 10))}…</td></tr>`).join('')}</tbody></table></div>`;
    target.querySelectorAll('tbody tr').forEach((row) => row.querySelector('.history-open').addEventListener('click', async () => {
      if (agentBusy || businessBusy) return;
      agentProgress(1, 'Opening report', 'Please wait for the selected report.', true);
      try { const run = await api(`/api/runs/${row.dataset.runId}`); state.lastRun = run; agentProgress(0, 'Ready', 'Your report is ready.'); showPage('results'); }
      catch (error) { agentProgress(0, 'Unable to open report', error.message, false, true); toast(error.message, true); }
    }));
  } catch (error) { if (request !== historyRequest || userId !== state.userId) return; target.innerHTML = `<div class="notice error">${escapeHtml(error.message)}</div><button type="button" class="button button-quiet" id="history-retry">Try again</button>`; byId('history-retry').onclick = renderHistory; }
}

function renderResults(run) {
  const nameKey = `dq-downloads:${state.userId}:${run.run_id}`;
  if (!state.downloadNames[run.run_id]) {
    try { const saved = JSON.parse(sessionStorage.getItem(nameKey)); if (saved && typeof saved.accepted === 'string' && typeof saved.rejected === 'string') state.downloadNames[run.run_id] = saved; } catch {}
  }
  const outputNames = state.downloadNames[run.run_id] || run.outputs || {};
  const total = Number(run.row_count ?? (Number(run.accepted_count) + Number(run.rejected_count))) || 0;
  const accepted = Number(run.accepted_count) || 0, rejected = Number(run.rejected_count) || 0;
  const failures = run.failures || [];
  const complete = Number(run.failure_count ?? failures.length) <= failures.length;
  const percent = (n, base = total) => base ? (n / base * 100).toFixed(1) : '0.0';
  const label = value => String(value).replaceAll('_', ' ').replace(/\b\w/g, c => c.toUpperCase());
  const colors = { critical: '#ef3038', high: '#ff9800', medium: '#ffc400', low: '#258cec', unspecified: '#8795a6' };
  const checks = (run.rules_selected || []).map(rule => {
    const result = run.check_results?.find(item => item.rule_id === rule.rule_id);
    const issues = failures.filter(item => item.rule_id === rule.rule_id);
    const failedRows = new Set(issues.filter(item => item.row_index != null && item.row_index !== 'DATASET').map(item => String(item.row_index))).size;
    const status = result?.status === 'PASS' ? 'Passed' : result?.status === 'FAIL' || issues.length ? 'Failed' : 'Unavailable';
    const config = rule.configuration || {};
    const condition = config.original_requirement || (config.type ? formatBusinessRule(config) : Object.entries(config).map(([key, value]) => `${key.replaceAll('_', ' ')}: ${typeof value === 'object' ? JSON.stringify(value) : value}`).join('; ')) || rule.description || rule.predicate || 'Applied to uploaded data';
    return { ...rule, category: label(rule.category || 'Other'), severity: String(rule.severity || 'unspecified').toLowerCase(), status, failedRows: complete || status === 'Passed' ? failedRows : null, condition };
  });
  const passed = checks.filter(c => c.status === 'Passed').length, failed = checks.filter(c => c.status === 'Failed').length;
  const categories = [...new Set(checks.map(c => c.category))].map(name => ({ name, passed: checks.filter(c => c.category === name && c.status === 'Passed').length, failed: checks.filter(c => c.category === name && c.status === 'Failed').length }));
  const severityCounts = {};
  failures.forEach(item => { const raw = String(item.severity || 'unspecified').toLowerCase(); const key = Object.hasOwn(colors, raw) ? raw : 'unspecified'; severityCounts[key] = (severityCounts[key] || 0) + 1; });
  const donut = (segments, count, name) => {
    const sum = segments.reduce((n, item) => n + item.count, 0);
    let angle = -90;
    const arcs = segments.filter(item => item.count).map(item => {
      const sweep = item.count / sum * 360;
      const middle = (angle + sweep / 2) * Math.PI / 180;
      const text = sweep >= 24 ? `<text x="${100 + 72 * Math.cos(middle)}" y="${100 + 72 * Math.sin(middle)}" fill="${item.color === colors.medium || item.color === colors.high ? '#102045' : 'white'}" text-anchor="middle" dominant-baseline="middle" font-size="12" font-weight="600">${percent(item.count, sum)}%</text>` : '';
      const ring = `<circle cx="100" cy="100" r="72" fill="none" stroke="${item.color}" stroke-width="44" pathLength="100" stroke-dasharray="${item.count / sum * 100} ${100 - item.count / sum * 100}" transform="rotate(${angle} 100 100)"/>`;
      angle += sweep;
      return {ring,text};
    });
    return `<div class="report-donut-layout"><svg class="report-donut" viewBox="0 0 200 200" role="img" aria-label="${escapeHtml(segments.map(item => `${item.name}: ${item.count} (${percent(item.count, sum)}%)`).join(', ') || 'No issues')}"><circle cx="100" cy="100" r="72" fill="none" stroke="#e6edf5" stroke-width="44"/>${arcs.map(a => a.ring).join('')}${arcs.map(a => a.text).join('')}<text x="100" y="96" text-anchor="middle" font-size="24" font-weight="700" fill="#102045">${numberFormat(count)}</text><text x="100" y="116" text-anchor="middle" font-size="12" fill="#102045">${name}</text></svg><div class="report-legend">${segments.map(item => `<div><i style="background:${item.color}"></i><span>${escapeHtml(item.name)}<br>${numberFormat(item.count)} (${percent(item.count, sum)}%)</span></div>`).join('') || '<span>No issues found.</span>'}</div></div>`;
  };
  const chartMax = Math.max(1, ...categories.map(c => c.passed + c.failed));
  const chartWidth = Math.max(440, categories.length * 75 + 40);
  const slot = (chartWidth - 40) / Math.max(1, categories.length);
  const ticks = Array.from({length: Math.min(5, chartMax) + 1}, (_, i) => Math.round(chartMax * i / Math.min(5, chartMax)));
  const gridLines = ticks.map(n => {
    const y = 158 - n / chartMax * 136;
    return `<line x1="28" x2="${chartWidth - 4}" y1="${y}" y2="${y}" stroke="#e3ebf5"/>` +
      `<text x="20" y="${y + 4}" font-size="10" text-anchor="end" fill="#607188">${n}</text>`;
  }).join('');
  const categoryBars = categories.map((category, index) => {
    const x = 32 + index * slot;
    const width = slot * 0.64;
    const center = x + width / 2;
    const passedHeight = category.passed / chartMax * 136;
    const failedHeight = category.failed / chartMax * 136;
    const parts = [
      `<rect x="${x}" y="${158 - passedHeight}" width="${width}" height="${passedHeight}" fill="#27b565"/>`,
      `<rect x="${x}" y="${158 - passedHeight - failedHeight}" width="${width}" height="${failedHeight}" fill="#ef4444"/>`,
    ];
    if (category.passed) {
      parts.push(`<text x="${center}" y="${158 - passedHeight / 2 + 4}" text-anchor="middle" fill="white" font-size="12">${category.passed}</text>`);
    }
    if (category.failed) {
      parts.push(`<text x="${center}" y="${158 - passedHeight - failedHeight / 2 + 4}" text-anchor="middle" fill="white" font-size="12">${category.failed}</text>`);
    }
    const words = category.name.split(' ').map((word, wordIndex) =>
      `<tspan x="${center}" dy="${wordIndex ? 11 : 0}">${escapeHtml(word)}</tspan>`
    ).join('');
    parts.push(`<text x="${center}" y="174" text-anchor="middle" fill="#102045" font-size="9">${words}</text>`);
    return parts.join('');
  }).join('');
  const categoryDescription = categories.map(category =>
    `${category.name}: ${category.passed} passed, ${category.failed} failed`
  ).join('; ');
  const categoryChart = `<svg viewBox="0 0 ${chartWidth} 200" role="img" aria-label="${escapeHtml(categoryDescription)}">${gridLines}${categoryBars}</svg>`;
  const icon = kind => {
    if (kind === 'check' || kind === 'cross') {
      const path = kind === 'check' ? 'M8 16l5 5 11-12' : 'M10 10l12 12M22 10L10 22';
      return '<svg viewBox="0 0 32 32"><circle cx="16" cy="16" r="15" fill="currentColor"/>' +
        `<path d="${path}" fill="none" stroke="white" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
    }
    if (kind === 'file') {
      return '<svg viewBox="0 0 32 32"><path d="M6 2h14l7 7v21H6z" fill="currentColor"/><path d="M20 2v8h7M11 15h11M11 20h11M11 25h8" fill="none" stroke="white" stroke-width="2"/></svg>';
    }
    return '<span>&#9881;</span>';
  };
  const card = (name, n, tone, symbol, sub = '') => `<div class="report-metric ${tone}"><div class="report-icon" aria-hidden="true">${icon(symbol)}</div><div><strong>${name}</strong><b>${numberFormat(n)}</b>${sub ? `<small>(${sub}%)</small>` : ''}</div></div>`;
  const date = new Date(run.validated_at);
  byId('results-page-content').innerHTML = `
    <article id="dq-summary-report" class="dq-report reference-report">
      <div class="report-toolbar" data-export-control><details id="report-download-menu" class="report-download-menu"><summary class="button button-primary">&#8595; Download report</summary><div class="report-format-options">${[['pdf','Print / Save as PDF'],['png','PNG image'],['jpeg','JPEG image'],['webp','WebP image'],['svg','SVG image'],['html','HTML report'],['csv','CSV check details'],['json','JSON run data']].map(([format,name]) => `<button type="button" data-report-format="${format}">${name}</button>`).join('')}</div></details></div><header class="report-banner"><div><h1>Data Quality Summary Report</h1><p>${escapeHtml(run.dataset_filename || run.dataset_name || 'Dataset')}</p></div><div class="report-metadata"><div><strong>Total Records:</strong> ${numberFormat(total)}</div><div><strong>Dataset:</strong> ${escapeHtml(run.dataset_filename || run.dataset_name || 'Unavailable')}</div><div><strong>Report Time:</strong> ${escapeHtml(Number.isNaN(date.getTime()) ? 'Unavailable' : date.toLocaleString())}</div>${run.environment ? `<div>Environment: ${escapeHtml(run.environment)}</div>` : ''}${run.layer ? `<div>Layer: ${escapeHtml(run.layer)}</div>` : ''}</div></header>
      ${run.score ? `<section class="report-score"><strong>Quality score: ${Number(run.score.value).toFixed(1)} / 100</strong><details><summary>How this score is calculated</summary><p>${escapeHtml(run.score.formula)}</p><ul>${Object.entries(run.score.breakdown || {}).map(([category, value]) => `<li>${escapeHtml(category)}: ${Number(value).toFixed(1)} / 100</li>`).join('')}</ul></details></section>` : ''}
      <div class="report-metrics">${card('Total Records',total,'blue','file')}${card('Valid Records',accepted,'green','check',percent(accepted))}${card('Invalid Records',rejected,'red','cross',percent(rejected))}${card('Total DQ Checks',checks.length,'blue','gear')}${card('Passed Checks',passed,'green','check',percent(passed,checks.length))}${card('Failed Checks',failed,'red','cross',percent(failed,checks.length))}</div>
      <div class="report-charts"><section class="report-panel"><h2>Record Quality Distribution</h2>${donut([{name:'Valid Records',count:accepted,color:'#27b565'},{name:'Invalid Records',count:rejected,color:colors.critical}],total,'Records')}</section><section class="report-panel report-category"><div class="report-chart-heading"><h2>Data Quality Check Results by Category</h2><div><span class="passed-key">&#9632; Passed</span><span class="failed-key">&#9632; Failed</span></div></div>${categories.length ? categoryChart : '<p class="muted">No checks recorded.</p>'}</section><section class="report-panel"><h2>Data Quality Issues by Severity</h2>${donut(Object.keys(colors).filter(k => severityCounts[k]).map(k => ({name:label(k),count:severityCounts[k],color:colors[k]})), failures.length, 'Issues')}${!complete ? '<p class="report-note">Severity distribution shows the available audit sample.</p>' : ''}</section></div>
      <section class="report-panel report-checks"><h2>Data Quality Check Details</h2><div class="column-table-wrap"><table class="column-table dq-check-table"><thead><tr><th>#</th><th>DQ Check Name</th><th>Category</th><th>Description</th><th>Rule / Condition</th><th>Status</th><th>Total Records</th><th>Failed Records</th><th>Failure %</th><th>Severity</th></tr></thead><tbody>${checks.map((c,i) => `<tr><td>${i+1}</td><td>${escapeHtml(c.name || c.rule_id)}</td><td>${escapeHtml(c.category)}</td><td>${escapeHtml(c.description || 'Unavailable')}</td><td>${escapeHtml(c.condition)}</td><td><span class="report-badge ${c.status.toLowerCase()}">${c.status}</span></td><td>${numberFormat(total)}</td><td>${c.failedRows === null ? 'Unavailable' : numberFormat(c.failedRows)}</td><td>${c.failedRows === null ? 'Unavailable' : percent(c.failedRows) + '%'}</td><td><span class="report-badge severity-${Object.hasOwn(colors,c.severity) ? c.severity : 'unspecified'}">${escapeHtml(label(c.severity))}</span></td></tr>`).join('') || '<tr><td colspan="10">No checks recorded.</td></tr>'}</tbody></table></div></section>
      ${checks.some(c => c.status === 'Unavailable') ? '<p class="report-note">Some check results are unavailable for this run.</p>' : ''}
      ${run.warnings?.length ? `<p class="notice warning">${run.warnings.map(escapeHtml).join(' ')}</p>` : ''}
      <p class="dq-report-footnote">Severity counts individual issues. Failed records are distinct within each check; a record can fail multiple checks. Dataset-level issues have no record index. Run: ${escapeHtml(run.run_id)}</p>
    </article>
    <section class="results-download-card"><h2>Download files</h2><p>Download records that passed validation or need corrections.</p><div class="results-file-downloads"><label>Accepted file name<input id="accepted-name" maxlength="100" value="${escapeHtml(outputNames.accepted || run.dataset_name + '_accepted')}"></label><button class="button button-quiet" type="button" data-download-records="accepted">Download accepted records</button><label>Rejected file name<input id="rejected-name" maxlength="100" value="${escapeHtml(outputNames.rejected || run.dataset_name + '_rejected')}"></label><button class="button button-quiet" type="button" data-download-records="rejected">Download rejected records</button></div></section>
    <details class="dq-audit-details"><summary>View rejected-record details (${numberFormat(run.failure_count)} issues)</summary><div class="report-controls"><label for="failure-filter">Search rejected-record issues</label><input class="explorer-search" id="failure-filter" placeholder="Filter failures by rule, column, or reason"><button type="button" class="button button-quiet" id="download-errors" aria-label="Download errors as PDF" title="Download errors as PDF"><svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3v12m-5-5 5 5 5-5M4 16v4a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-4"/></svg></button></div><div id="failure-list" class="failure-list"></div></details>`;
  byId('results-page-content').querySelectorAll('[data-report-format]').forEach(button => button.addEventListener('click', () => downloadQualityReport(button.dataset.reportFormat)));
  byId('results-page-content').querySelectorAll('[data-download-records]').forEach(button => button.addEventListener('click', () => downloadRecordFile(button.dataset.downloadRecords)));
  ['accepted', 'rejected'].forEach(output => {
    byId(output + '-name').oninput = () => {
      state.downloadNames[run.run_id] = {accepted: byId('accepted-name').value, rejected: byId('rejected-name').value};
      try { sessionStorage.setItem(nameKey, JSON.stringify(state.downloadNames[run.run_id])); } catch {}
    };
  });
  byId('download-errors').addEventListener('click', downloadErrorDetails);
  renderFailures(run.failures || []);
  byId('failure-filter').addEventListener('input', event => renderFailures(run.failures || [], event.target.value, 0));
}

function observedValue(value) { return value == null ? 'Missing' : value === '' ? '(empty)' : String(value); }
function renderFailures(failures, query = '', offset = 0) {
  const target = byId('failure-list');
  const needle = query.toLowerCase();
  const matching = failures.filter(item => `${item.rule_name} ${item.column ?? ''} ${item.reason} ${observedValue(item.actual_value)}`.toLowerCase().includes(needle));
  const pageSize = 100;
  const filtered = matching.slice(offset, offset + pageSize);
  if (!filtered.length) { target.innerHTML = `<div class="empty-state">${failures.length ? 'No issues match your search.' : 'All rows passed your selected checks. Download the passed rows above.'}</div>`; return; }
  target.innerHTML = `<p class="muted" role="status">Displaying ${numberFormat(offset + 1)}&ndash;${numberFormat(offset + filtered.length)} of ${numberFormat(matching.length)} matching issues. Download rejected records for all per-record reasons.</p><div class="failure-row failure-head"><span>Status</span><span>Rule / row</span><span>Column</span><span>Reason</span><span>Observed value</span></div>${filtered.map(failure => `<div class="failure-row"><span class="fail-status">${escapeHtml(failure.status)}</span><span class="failure-cell">${escapeHtml(failure.rule_name)}<br><span class="muted">${failure.row_index === 'DATASET' ? 'Entire dataset' : 'Row ' + escapeHtml(failure.row_index)}</span></span><span class="failure-cell">${escapeHtml(failure.column || 'Complete row')}</span><span class="failure-cell failure-reason">${escapeHtml(failure.reason)}<br><span class="muted">${escapeHtml(failure.expected_condition)}</span></span><span class="failure-cell" title="${escapeHtml(observedValue(failure.actual_value))}">${escapeHtml(observedValue(failure.actual_value))}</span></div>`).join('')}<div class="pagination"><button type="button" id="failure-prev" ${offset === 0 ? 'disabled' : ''}>&#8592; Previous issues</button><span>Page ${Math.floor(offset / pageSize) + 1} of ${Math.ceil(matching.length / pageSize)}</span><button type="button" id="failure-next" ${offset + pageSize >= matching.length ? 'disabled' : ''}>Next issues &#8594;</button></div>`;
  byId('failure-prev').onclick = () => renderFailures(failures, query, Math.max(0, offset - pageSize));
  byId('failure-next').onclick = () => renderFailures(failures, query, offset + pageSize);
}

async function initialize() {
  try {
    const [health, rules] = await Promise.all([api('/api/health'), api('/api/rules')]);
    state.rules = rules;
    const aiEnabled = health.llm?.configured === true;
    const aiDot = byId('ai-status-dot');
    aiDot.classList.toggle('ai-enabled', aiEnabled);
    aiDot.setAttribute('aria-label', aiEnabled ? 'AI is enabled' : 'AI is not enabled');
    aiDot.title = aiEnabled ? 'AI is enabled' : 'AI is not enabled';
    renderRegistry();
    updateFlow();
    await openPageFromUrl();
  } catch (error) { showNotice(`The local API could not be reached: ${error.message}. Start the app using the setup instructions.`, 'error'); }
}


agentProgress(0, 'Ready for your file', 'Upload a supported data file. The agent will guide you through each step.');

// Mobile navigation is a disclosure; normal page links keep their existing behavior.
const navigationToggle = byId('navigation-toggle');
const navigationRail = document.querySelector('.rail');
const compactNavigation = window.matchMedia('(max-width: 900px)');
function setNavigationOpen(open, restoreFocus = false) {
  navigationRail.classList.toggle('navigation-open', open);
  navigationToggle.setAttribute('aria-expanded', String(open));
  navigationToggle.setAttribute('aria-label', open ? 'Close navigation' : 'Open navigation');
  if (restoreFocus) navigationToggle.focus();
}
navigationToggle.addEventListener('click', () => {
  setNavigationOpen(navigationToggle.getAttribute('aria-expanded') !== 'true');
});
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && navigationToggle.getAttribute('aria-expanded') === 'true') {
    setNavigationOpen(false, true);
  }
});
document.addEventListener('click', (event) => {
  if (!compactNavigation.matches || navigationToggle.getAttribute('aria-expanded') !== 'true') return;
  const link = event.target.closest('[data-page]');
  if (link && !link.disabled) {
    setNavigationOpen(false);
    const heading = document.querySelector('.page.active h1');
    if (heading) { heading.setAttribute('tabindex', '-1'); heading.focus({preventScroll:true}); }
  } else if (!navigationRail.contains(event.target)) {
    setNavigationOpen(false);
  }
});
compactNavigation.addEventListener('change', () => setNavigationOpen(false));

async function downloadErrorDetails() {
  const button = byId('download-errors');
  if (button.disabled) return;
  button.disabled = true;
  try {
    const run = state.lastRun;
    const response = await fetch(`/api/runs/${encodeURIComponent(run.run_id)}/errors.pdf`);
    if (response.status === 401 && window.showLogin) window.showLogin();
    if (!response.ok) {
      let message = 'Could not download errors. Try again.';
      try { const body = await response.json(); message = body.detail || message; } catch {}
      throw new Error(message);
    }
    saveDownload(await response.blob(), `${run.dataset_name || 'validation'}_errors.pdf`);
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; }
}
