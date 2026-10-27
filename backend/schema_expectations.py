"""Explainable matching of informal schema expectations to the current upload."""
import re
from difflib import SequenceMatcher
from backend.business_context import words
from backend.rule_plugins.catalog_validation import _types, _missing

TYPES = {'text':'StringType','string':'StringType','str':'StringType','integer':'LongType','int':'LongType','whole number':'LongType','long':'LongType','number':'DoubleType','numeric':'DoubleType','decimal':'DoubleType','float':'DoubleType','double':'DoubleType','boolean':'BooleanType','bool':'BooleanType','date':'DateType','datetime':'TimestampType','timestamp':'TimestampType'}
TYPE_PATTERN = '|'.join(re.escape(x) for x in sorted(TYPES,key=len,reverse=True))


def clean(text):
    text=re.sub(r'^\s*(?:[-*]|\d+[.)])\s*','',text).strip()
    prefix=r'^(?:(?:i\s+)?(?:please\s+)?(?:need|want|expect|include|require)(?:\s+to\s+have)?|(?:the\s+)?(?:file|dataset|table|schema)(?:\s+(?:should|must))?\s+(?:have|contain|include)|(?:expected\s+)?(?:schema|columns?|fields?))\b\s*[:=]?\s*'
    for _ in range(3):
        updated=re.sub(prefix,'',text,flags=re.I)
        if updated==text:break
        text=updated
    return text.strip().rstrip('.')


def terms(text):
    return ' '.join(words(text))


def clauses(text,columns):
    text=clean(text)
    text=re.sub(r'\b(?:only|exactly)\b','',text,flags=re.I)
    text=re.sub(r'\bno (?:other|extra) (?:columns|fields)\b','',text,flags=re.I)
    parts=[clean(x) for x in re.split(r'[\n,;]+|\s+(?:and|plus|with)\s+',text,flags=re.I) if clean(x)]
    result=[]
    for part in parts:
        # Recognize unformatted lists of actual column names without inventing names.
        mentions=[]
        for column in columns:
            aliases={column,re.sub(r'[_-]+',' ',column),' '.join(re.findall(r'[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+',column))}
            for alias in aliases:
                if not alias.strip():continue
                for match in re.finditer(r'(?<!\w)'+re.escape(alias)+r'(?!\w)',part,re.I):
                    mentions.append((match.start(),match.end()))
        spans=[]
        for start,end in sorted(set(mentions),key=lambda value:(value[0],-(value[1]-value[0]))):
            if not spans or start>=spans[-1][1]:spans.append((start,end))
        if len(spans)>1 and not part[:spans[0][0]].strip():
            result.extend(part[start:(spans[i+1][0] if i+1<len(spans) else len(part))].strip() for i,(start,end) in enumerate(spans))
        else:result.append(part)
    if not result or len(result)>100:raise ValueError('Describe between 1 and 100 expected fields.')
    return result


def expectation(part):
    required=bool(re.search(r'\b(?:required|mandatory|not null|non-null|not blank|not missing)\b',part,re.I))
    text=re.sub(r'\b(?:required|mandatory|not null|non-null|not blank|not missing)\b','',part,flags=re.I).strip()
    expected=None
    match=re.fullmatch(r'(.+?)\s*(?::|=|\bas\b|\bshould be\b|\bmust be\b|\bis\b|\bof type\b|\btype\b)\s*(?:a\s+|an\s+)?('+TYPE_PATTERN+r')\s*',text,re.I)
    if not match:
        # A trailing date may be part of a field name, e.g. "signup date".
        simple='|'.join(re.escape(x) for x in TYPES if x not in {'date','datetime','timestamp'})
        match=re.fullmatch(r'(.+?)\s+('+simple+r')',text,re.I)
    if match:text,expected=match[1].strip(),match[2].casefold()
    text=text.strip(' `"\'()[]:')
    return text,expected,required


def rank(label,columns):
    query=terms(label)
    if not query:return []
    result=[]
    for name in columns:
        candidate=terms(name)
        if label.casefold()==name.casefold():score=1.0
        elif query==candidate:score=.98
        else:
            left,right=set(query.split()),set(candidate.split())
            overlap=len(left & right)/max(len(left),len(right),1)
            fuzzy=SequenceMatcher(None,query,candidate).ratio()
            # Partial concepts such as "email" can suggest several distinct columns.
            score=max(.82 if left and left<=right else 0,.55*overlap+.45*fuzzy)
        if score>=.45:result.append({'column':name,'confidence':round(score,3)})
    return sorted(result,key=lambda x:(-x['confidence'],x['column']))[:5]


def check(frame,text,mappings=None):
    if not text.strip():raise ValueError('Describe the columns you expect in this dataset.')
    columns=[str(x) for x in frame.columns]
    parts=clauses(text,columns)
    mappings=mappings or {}
    if set(mappings)-{str(i) for i in range(len(parts))} or any(value not in columns for value in mappings.values()):
        raise ValueError('Choose an existing column for each field mapping.')
    items=[]
    used=set()
    for index,part in enumerate(parts):
        label,expected,required=expectation(part)
        candidates=rank(label,columns)
        selected=mappings.get(str(index))
        status='MATCHED'
        if selected:
            reason='Column selected by you.'
        elif not candidates or candidates[0]['confidence']<.65:
            status='MISSING';reason='No confident column match. Choose a column or clarify this field.'
        elif len(candidates)>1 and candidates[0]['confidence']-candidates[1]['confidence']<.12 and not (candidates[0]['confidence']==1 and candidates[1]['confidence']<1):
            status='AMBIGUOUS';reason='Several columns match this description. Choose the intended column.'
        else:
            selected=candidates[0]['column'];reason='Matched using the uploaded column names and common naming variations.'
        failures=0
        if selected:
            used.add(selected)
            values=frame[selected]
            missing=_missing(values)
            invalid=~_types(values,TYPES[expected]) & ~missing if expected else missing & False
            if required:invalid |= missing
            failures=int(invalid.sum())
            if failures:status='MISMATCH';reason=f'{failures} records do not meet the stated type or required-value expectation.'
        items.append({'id':str(index),'expectation':part,'field':label,'column':selected,'expected_type':expected,'required':required,'status':status,'reason':reason,'invalid_records':failures,'candidates':candidates})
    extras=[name for name in columns if name not in used]
    exact=bool(re.search(r'\b(?:only|exactly|no (?:other|extra) (?:columns|fields))\b',text,re.I))
    unresolved=sum(x['status'] in {'AMBIGUOUS','MISSING'} for x in items)
    mismatches=sum(x['status']=='MISMATCH' for x in items)
    return {'status':'REVIEW' if unresolved else 'MISMATCH' if mismatches or exact and extras else 'MATCH',
            'items':items,'columns':columns,'unmentioned_columns':extras,'extra_columns_are_errors':exact,
            'summary':{'matched':sum(x['status']=='MATCHED' for x in items),'needs_review':unresolved,'mismatched':mismatches},
            'message':'Check completed against the current uploaded data. Uncertain matches need your selection; unmentioned columns are allowed unless you asked for only these fields.'}
