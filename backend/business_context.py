"""Dataset-grounded retrieval and clarification, with no raw rows or learned defaults."""
from difflib import SequenceMatcher
import re
from backend.business_conditions import parse_condition, prefix_request, datatype_request, length_request
import json

STOP = set('a an the should must be have has is are every each all with of in across entire dataset data field column valid values value rules rule good correct reasonable proper format'.split())
SYNONYMS = {'qty':'quantity','identifier':'id','identifiers':'id','code':'id','cost':'price','total':'amount','date':'timestamp','datetime':'timestamp','emailaddress':'email'}

def words(text):
    text = re.sub(r'([a-z0-9])([A-Z])', r'\1 \2', str(text))
    return [SYNONYMS.get(w,w) for w in re.findall(r'[a-z0-9]+',text.casefold()) if w not in STOP]

def build_retrieval_index(metadata):
    """Build local searchable column documents from this upload's metadata."""
    return {'version':1, 'documents':[
        {'column':column['name'], 'terms':sorted(set(words(column['name'])) | {token for semantic in column.get('semantic_candidates', []) if isinstance(semantic, dict) and semantic.get('confidence', 0) >= .5 for token in words(semantic.get('type', ''))}),
         'content':json.dumps(column, ensure_ascii=False, default=str)}
        for column in metadata.get('columns', []) if isinstance(column, dict) and isinstance(column.get('name'), str)
    ]}

def retrieve_columns(requirement, columns, metadata):
    profile = {item['name']:item for item in metadata.get('columns',[]) if isinstance(item,dict) and item.get('name') in columns}
    prefix = prefix_request(requirement)
    datatype = datatype_request(requirement)
    length = length_request(requirement)
    unquoted = length[0] if length else datatype[0] if datatype else prefix[0] if prefix else re.sub(r'(["\']).*?\1','',requirement)
    query = words(unquoted)
    documents = {doc['column']:doc for doc in metadata.get('retrieval_index', {}).get('documents', []) if isinstance(doc, dict) and doc.get('column') in columns}
    ranked = []
    for name in columns:
        tokens = words(name)
        overlap = sum(any(token == word or SequenceMatcher(None,token,word).ratio() >= .84 for word in query) for token in tokens) / max(1,len(tokens))
        exact = bool(re.search(r'(?<!\w)' + re.escape(name) + r'(?!\w)',unquoted,re.I))
        spaced = bool(re.search(r'(?<!\w)' + re.escape(re.sub(r'[_-]+',' ',name)) + r'(?!\w)',unquoted,re.I))
        fuzzy = max((SequenceMatcher(None, token,word).ratio() for token in tokens for word in query),default=0)
        score = 1.0 if exact or spaced else min(.94, .72*overlap + .22*fuzzy)
        details = profile.get(name,{})
        if not query and (prefix or length) and details.get('physical_type') in {'str', 'string', 'object'}: score = .5
        document = documents.get(name, {})
        if query and set(query) <= set(document.get('terms', [])): score = max(score, .8)
        meaning = [item.get('type') for item in details.get('semantic_candidates',[]) if isinstance(item,dict) and item.get('confidence',0)>=.5]
        if any(word in words(' '.join(str(value) for value in meaning)) for word in query): score = max(score,.8)
        if score < .3: continue
        ranked.append({'name':name,'score':round(score,3),'physical_type':details.get('physical_type','unknown'),'semantic_types':meaning,'null_count':details.get('null_count'),'distinct_count':details.get('distinct_count'),'statistics':details.get('statistics',{}),'retrieved_document':document.get('content'),'reason':f'Matched dataset column {name} using its name and metadata.'})
    return sorted(ranked,key=lambda item:(-item['score'],item['name']))[:5]

def interpret_context(requirement, columns, metadata, minimum_score=.55):
    candidates = retrieve_columns(requirement,columns,metadata)
    if not candidates: return None
    datatype = datatype_request(requirement)
    prefix = prefix_request(requirement)
    length = length_request(requirement)
    subject = length[0] if length else datatype[0] if datatype else prefix[0] if prefix else re.split(r'\b(?:should|must)\b', requirement, maxsplit=1, flags=re.I)[0]
    subject_tokens = set(words(subject))
    matching = [name for name in columns if subject_tokens and subject_tokens <= set(words(name))]
    if len(matching) > 1: return None
    best = candidates[0]
    if best['score'] < minimum_score or (len(candidates)>1 and best['score']-candidates[1]['score']<.12): return None
    name=best['name']
    if length:
        return {'status':'PARSED','type':'text_length','column':name,'operator':length[1],'length':length[2], 'original_requirement':requirement,'mapping_reason':best['reason'],'confidence':best['score']}
    datatype = datatype_request(requirement)
    if datatype:
        return {'status':'PARSED','type':'data_type','column':name,'expected_type':datatype[1], 'original_requirement':requirement,'mapping_reason':best['reason'],'confidence':best['score']}
    prefix = prefix_request(requirement)
    if prefix:
        return {'status':'PARSED','type':'starts_with','column':name,'prefix':prefix[1],
                'original_requirement':requirement,'mapping_reason':best['reason'],'confidence':best['score']}

    # Use full-sentence matches: never ignore an extra constraint.
    sentence = requirement.strip().rstrip('.')
    subject = r'[\w -]+?'
    patterns = [
        (subject+r'\s+(?:should|must)(?:\s+be)?\s+(?:positive|greater than zero)', f'{name} must be a valid number greater than zero.'),
        (subject+r'\s+(?:should|must)(?:\s+be)?\s+(?:non-negative|not negative)', f'{name} must be a valid non-negative number.'),
        (subject+r'\s+(?:should|must)(?:\s+be)?\s+(?:required|mandatory|present|filled|not blank|not missing)', f'{name} must be non-null and non-empty.'),
        (subject+r'\s+(?:should|must)(?:\s+be)?\s+(?:unique|not repeat|not repeated|not duplicated|have no duplicates)', f'{name} must be unique.'),
        (r'(?:no duplicates? (?:in|for)|no repeated)\s+'+subject, f'{name} must be unique.'),
    ]
    for pattern, rewritten in patterns:
        if re.fullmatch(pattern,sentence,re.I):
            parsed=parse_condition(rewritten,columns)
            if parsed: return {**parsed,'original_requirement':requirement,'mapping_reason':best['reason'],'confidence':best['score']}
    # Canonicalize spelling/alias of a single subject, keeping the explicit condition intact.
    match=re.fullmatch(r'([\w -]+?)\s+((?:should|must|starts?)\b.+)',sentence,re.I)
    if match:
        parsed=parse_condition(f'{name} {match[2]}',columns)
        if parsed: return {**parsed,'original_requirement':requirement,'mapping_reason':best['reason'],'confidence':best['score']}
    return None

def clarification(requirement, columns, metadata, explanation):
    candidates=retrieve_columns(requirement,columns,metadata)
    if not candidates:
        candidates=[{'name':name,'score':0,'reason':'Select the intended column.'} for name in columns[:5]]
    datatype = datatype_request(requirement)
    if datatype:
        choices = [{'label':f"{candidate['name']} data type should be {datatype[1]}",
                    'rule':{'status':'PARSED','type':'data_type','column':candidate['name'],'expected_type':datatype[1], 'original_requirement':requirement,
                            'mapping_reason':f"You selected {candidate['name']} for this requirement."}} for candidate in candidates if candidate.get('score',0) >= .3]
        return {'requirement':requirement,'clarification':'Did you mean one of these data type rules?' if choices else 'No matching dataset column was found for this data type rule.', 'candidate_columns':candidates,'suggestions':[], 'choices':choices}
    if re.search(r'\b(?:data\s*type|type)\b', requirement, re.I):
        return {'requirement':requirement,'clarification':'The requested data type was not recognized.', 'candidate_columns':candidates,'suggestions':[], 'choices':[]}
    prefix = prefix_request(requirement)
    if prefix:
        choices = []
        for candidate in candidates:
            if candidate.get('score', 0) < .3: continue
            wording = f"{candidate['name']} must start with {json.dumps(prefix[1])}."
            rule = parse_condition(wording, columns)
            if rule:
                rule['original_requirement'] = requirement
                rule['mapping_reason'] = f"You selected {candidate['name']} for this requirement."
                choices.append({'label':f"{candidate['name']} starts with {json.dumps(prefix[1])}", 'rule':rule})
        return {'requirement':requirement,'clarification':'Did you mean one of these rules?',
                'candidate_columns':candidates,'suggestions':[], 'choices':choices}
    choices = []
    for candidate in candidates:
        if candidate.get('score', 0) < .3: continue
        name = candidate['name']
        parsed = interpret_context(requirement, [name], metadata, minimum_score=.3)
        if not parsed: continue
        kind = parsed['type']
        labels = {
            'not_empty': f'{name} must be non-null and non-empty',
            'unique': f'{name} must be unique',
            'not_future': f'{name} must not be in the future',
        }
        if kind == 'text_length':
            comparison = {'==':'exactly', '>=':'at least', '<=':'at most'}[parsed['operator']]
            label = f"{name} must contain {comparison} {parsed['length']} characters"
        elif kind == 'numeric_comparison':
            label = f"{name} {parsed['operator']} {parsed['value']}"
        elif kind == 'allowed_values':
            label = f"{name} must be one of {', '.join(parsed['values'])}"
        else:
            label = labels.get(kind)
        if label:
            parsed['mapping_reason'] = f'You selected {name} for this requirement.'
            choices.append({'label':label, 'rule':parsed})
    if choices:
        return {'requirement':requirement,'clarification':'Did you mean one of these rules?', 'candidate_columns':candidates,'suggestions':[], 'choices':choices}
    text=requirement.casefold()
    if re.search(r'start|prefix',text):
        intents=[('starts_with','Starts with a prefix','prefix')]
        question='Which column and exact prefix do you mean? Prefixes are case-sensitive.'
    elif re.search(r'unique|duplicate|repeat',text):
        intents=[('unique','Must be unique',None)]
        question='Which column must contain unique values?'
    elif re.search(r'missing|blank|empty|required|mandatory|populated',text):
        intents=[('not_empty','Must be filled',None)]
        question='Which column must be non-null and non-empty?'
    elif re.search(r'amount|price|cost|quantity|qty|age|reasonable|range|limit|high|low',text):
        intents=[('minimum','Minimum allowed value','minimum'),('maximum','Maximum allowed value','maximum')]
        question='Which column and minimum or maximum should apply? The data profile is context, not an approved business limit.'
    elif re.search(r'future',text):
        intents=[('not_future','Must not be in the future',None)]
        question='Which timestamp column should be checked?'
    else:
        intents=[('not_empty','Must be filled',None),('unique','Must be unique',None),('starts_with','Starts with a prefix','prefix'),('minimum','Minimum allowed value','minimum'),('allowed_values','Approved values','comma-separated approved values')]
        question=explanation or 'Tell me which column and condition you mean in the clarification box below.'
    return {'requirement':requirement,'clarification':question,'explanation':explanation,'candidate_columns':candidates,'suggestions':[{'intent':kind,'label':label,'value_label':value} for kind,label,value in intents]}


def clarify_requirement(requirement, reply, columns, metadata):
    """Use the original condition when a reply selects a real dataset column."""
    answer = reply.strip().rstrip('.')
    names = [name for name in columns if answer.casefold() in {name.casefold(), name.casefold().replace('_', ' '), f'use {name}'.casefold(), f'{name} only'.casefold(), f'use {name} only'.casefold()}]
    if len(names) == 1:
        rule = interpret_context(requirement, names, metadata, minimum_score=0)
        if rule:
            rule['mapping_reason'] = f'Your clarification selected {names[0]}.'
            return rule
    return None
