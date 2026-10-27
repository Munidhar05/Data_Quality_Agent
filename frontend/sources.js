/* Connection credentials live only in this form and its import request. */
(() => {
  const surface = byId('welcome-upload');
  const chooser = surface.querySelector('.file-picker');
  const actions = document.createElement('div');
  actions.className = 'upload-actions';
  chooser.before(actions);
  actions.append(chooser);
  const separator = document.createElement('span');
  separator.className = 'upload-or';
  separator.textContent = 'OR';
  actions.append(separator);
  const label = document.createElement('label');
  label.className = 'connection-picker';
  label.innerHTML = '<select id="source-select" aria-label="Data connection"><option value="">Choose a connection</option></select>';
  actions.append(label);
  const form = document.createElement('form');
  form.id = 'source-import-form';
  form.className = 'connection-form hidden';
  form.autocomplete = 'off';
  form.innerHTML = `<h3 id="connection-heading"></h3><div id="connection-fields" class="connection-fields"></div>
    <details class="connection-options"><summary>Import options</summary><label>Maximum records <input id="source-limit" type="number" min="1" max="100000" step="1" value="100000" required></label></details>
    <p class="connection-privacy">Credentials are used for this import and are not saved.</p><button type="submit" class="button button-primary">Connect and import</button>`;
  actions.after(form);
  const feedback = document.createElement('p');
  feedback.id = 'source-feedback'; feedback.setAttribute('role','status');
  surface.after(feedback);
  let types = [], generation = 0;
  async function loadTypes() {
    const current = ++generation;
    try {
      const result = await api('/api/connection-types');
      if (current !== generation) return;
      types = result;
      const selected = byId('source-select').value;
      byId('source-select').replaceChildren(new Option('Choose a connection',''));
      const groups = new Map();
      types.forEach(type => {
        if (!groups.has(type.group)) { const group=document.createElement('optgroup'); group.label=type.group; groups.set(type.group,group); byId('source-select').append(group); }
        groups.get(type.group).append(new Option(type.label,type.id));
      });
      byId('source-select').value = selected;
    } catch (error) { if(current===generation) feedback.textContent=error.message; }
  }
  function conditions() {
    const values = Object.fromEntries([...form.querySelectorAll('[data-connection-key]')].map(input=>[input.dataset.connectionKey,input.value]));
    const type=types.find(item=>item.id===byId('source-select').value);
    if(!type)return;
    type.fields.forEach(field=>{
      const input=form.querySelector(`[data-connection-key="${field.key}"]`);
      if(!input)return; // A blur/change event can arrive while switching providers.
      const visible=Object.entries(field.show_when||{}).every(([key,value])=>values[key]===value);
      input.closest('label').classList.toggle('hidden',!visible);
      input.disabled=!visible;
      input.required=visible&&field.required;
      if(!visible)input.value='';
    });
  }
  function render() {
    const type=types.find(item=>item.id===byId('source-select').value);
    byId('connection-fields').replaceChildren();
    feedback.textContent='';
    form.classList.toggle('hidden',!type);
    if(!type)return;
    byId('connection-heading').textContent=`Connect to ${type.label}`;
    const optional=document.createElement('details'); optional.className='connection-extra';
    const summary=document.createElement('summary'); summary.textContent='More connection settings'; optional.append(summary);
    const extra=document.createElement('div');extra.className='connection-fields';optional.append(extra);
    type.fields.forEach(field=>{
      const row=document.createElement('label');
      const title=document.createElement('span'); title.textContent=field.label+(field.required?'':' (optional)');row.append(title);
      let input;
      if(field.options){input=document.createElement('select');field.options.forEach(value=>input.add(new Option(value,value)));}
      else if(field.multiline){input=document.createElement('textarea');input.rows=3;if(field.secret)input.className='connection-secret';}
      else {input=document.createElement('input');input.type=field.secret?'password':field.type||'text';}
      input.dataset.connectionKey=field.key;
      input.id='connection-'+field.key;
      input.autocomplete='off'; input.spellcheck=false;
      input.required=field.required;
      if(field.placeholder)input.placeholder=field.placeholder;
      if(field.default!=null)input.value=field.default;
      if(field.type==='number'){input.min='1';input.max='65535';input.step='1';}
      row.htmlFor=input.id;row.append(input);
      (field.required?byId('connection-fields'):extra).append(row);
    });
    if(extra.children.length)byId('connection-fields').append(optional);
    conditions();
  }
  byId('source-select').addEventListener('change',render);
  byId('source-select').addEventListener('focus',()=>{if(!types.length)loadTypes();});
  form.addEventListener('change',conditions);
  form.addEventListener('submit',async event=>{
    event.preventDefault();
    if(agentBusy||businessBusy)return;
    const configuration={};
    form.querySelectorAll('[data-connection-key]').forEach(input=>{if(!input.disabled&&input.value!=='')configuration[input.dataset.connectionKey]=input.value;});
    const kind=byId('source-select').value;
    const body={kind,configuration,max_rows:Number(byId('source-limit').value)};
    const owner=state.userId;
    feedback.textContent='';
    agentProgress(1,'Connecting to your data','Importing records and preparing your data profile.',true);
    try{
      const payload=await api('/api/datasets/connect',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      if(owner!==state.userId){agentProgress(0,'Ready','Choose your data source.');return;}
      acceptImportedDataset(payload);
      const type=types.find(item=>item.id===kind);
      (type?.fields||[]).filter(field=>field.secret).forEach(field=>{const input=byId('connection-'+field.key);if(input)input.value='';});
      feedback.textContent='Connected and imported successfully.';
    }catch(error){
      agentProgress(state.dataset?2:0,'Connection could not be completed',error.message,false,true);
      feedback.textContent=error.message;
    }
  });
  window.addEventListener('dq-account-change',()=>{
    generation++;types=[];
    byId('source-select').replaceChildren(new Option('Choose a connection',''));
    byId('connection-fields').replaceChildren();form.classList.add('hidden');
    ['schema-description'].forEach(id=>{if(byId(id))byId(id).value='';});
    feedback.textContent='';if(byId('schema-feedback'))byId('schema-feedback').textContent='';
    queueMicrotask(loadTypes);
  });
  const change=document.createElement('button');change.type='button';change.className='button button-quiet';change.textContent='Connect another source';
  byId('replace-dataset').after(change);
  change.addEventListener('click',()=>{surface.classList.remove('hidden');byId('source-select').focus();surface.scrollIntoView({behavior:'smooth',block:'start'});});
  if(state.userId)loadTypes();
  const schema = document.createElement('details');
  schema.className = 'schema-check-card';
  schema.innerHTML = `<summary>Check your schema</summary><div class="schema-check-heading"><p>Describe the columns you expect in your file. Use names, short notes or ordinary sentences.</p></div>
    <form id="schema-compare-form"><label for="schema-description">What should your data contain?</label>
    <textarea id="schema-description" rows="3" maxlength="8000" required placeholder="For example: customer ID, email as text, signup date, and amount as a number"></textarea>
    <button class="button button-primary" type="submit">Check schema</button></form><div id="schema-feedback" role="status" aria-live="polite"></div>`;
  byId('profile-page-content').before(schema);
  let schemaMappings={}, schemaDataset=null, schemaDescription=null;
  const results=byId('schema-feedback');
  function renderSchema(result) {
    results.replaceChildren();
    const heading=document.createElement('p');heading.className='schema-check-summary';
    heading.textContent=result.status==='MATCH'?'Your schema expectations match.':result.status==='REVIEW'?'Some fields need your review.':'Some schema expectations do not match.';
    results.append(heading);
    const counts=document.createElement('p');counts.textContent=`${result.summary.matched} matched ? ${result.summary.needs_review} need review ? ${result.summary.mismatched} do not match`;
    results.append(counts);
    const list=document.createElement('div');list.className='schema-match-list';results.append(list);
    result.items.forEach(item=>{
      const row=document.createElement('div');row.className='schema-match-row';
      const field=document.createElement('strong');field.textContent=item.expectation;row.append(field);
      const column=document.createElement('span');column.textContent=item.column?`Matched column: ${item.column}`:'No confirmed match';row.append(column);
      const status=document.createElement('span');status.className='schema-match-status';status.textContent=({MATCHED:'Matched',MISSING:'Choose a column',AMBIGUOUS:'Choose a column',MISMATCH:'Does not match'})[item.status];row.append(status);
      const reason=document.createElement('p');reason.textContent=item.reason;row.append(reason);
      const label=document.createElement('label');label.textContent=item.column?'Change matched column':'Select the intended column';
      const select=document.createElement('select');select.setAttribute('aria-label',`Column for ${item.field}`);
      select.add(new Option('Choose a column',''));
      const ranked=item.candidates.map(candidate=>candidate.column);
      [...new Set([...ranked,...result.columns])].forEach(name=>select.add(new Option(name,name)));
      select.value=item.column||'';
      select.addEventListener('change',()=>{if(select.value)schemaMappings[item.id]=select.value;else delete schemaMappings[item.id];});
      label.append(select);row.append(label);list.append(row);
    });
    if(result.unmentioned_columns.length){const extras=document.createElement('p');extras.textContent=`${result.extra_columns_are_errors?'Unexpected columns':'Other columns in your dataset'}: ${result.unmentioned_columns.join(', ')}`;results.append(extras);}
    if(result.status==='REVIEW'){const hint=document.createElement('p');hint.textContent='Choose the intended columns above, then click Check schema again.';results.append(hint);}
  }
  byId('schema-compare-form').addEventListener('submit',async event=>{
    event.preventDefault();
    if(!state.dataset){results.textContent='Upload or connect a dataset first.';return;}
    const datasetId=state.dataset.dataset_id,owner=state.userId;
    const description=byId('schema-description').value.trim();
    if(schemaDataset!==datasetId||schemaDescription!==description)schemaMappings={};
    schemaDataset=datasetId;schemaDescription=description;
    const button=event.currentTarget.querySelector('button');button.disabled=true;button.textContent='Checking?';
    try{
      const result=await api(`/api/datasets/${datasetId}/schema/check`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({description,mappings:schemaMappings})});
      if(owner!==state.userId||datasetId!==state.dataset?.dataset_id||description!==byId('schema-description').value.trim())return;
      renderSchema(result);
    }catch(error){if(owner===state.userId&&datasetId===state.dataset?.dataset_id)results.textContent=error.message;}
    finally{button.disabled=false;button.textContent='Check schema';}
  });
})();
