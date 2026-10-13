"""Explicit structured business conditions; no executable document content."""
from __future__ import annotations
from difflib import SequenceMatcher
import math
import re
import pandas as pd
from backend.rules import OPERATORS

TYPES = {'text_length', 'data_type', 'starts_with', 'not_empty', 'unique', 'numeric_comparison', 'allowed_values', 'not_future', 'arithmetic', 'conditional_dates'}

DATA_TYPES = {name.casefold(): name for name in ('DoubleType', 'FloatType', 'DecimalType', 'IntegerType', 'LongType', 'ShortType', 'ByteType', 'StringType', 'BooleanType', 'DateType', 'TimestampType', 'NullType')}

def datatype_request(text):
    match = re.fullmatch(r'([\w -]+?)\s+(?:data\s*type|type)\s+(?:(?:should|must)\s+)?(?:be\s+)?["\']?([a-z]+)["\']?\s*\.?', text.strip(), re.I)
    if not match: return None
    subject = re.sub(r'\s+column$', '', match[1], flags=re.I).strip()
    supplied = match[2].casefold()
    aliases = {**DATA_TYPES, **{key.removesuffix('type'): value for key, value in DATA_TYPES.items()}, 'int':'IntegerType', 'str':'StringType', 'bool':'BooleanType', 'float64':'DoubleType'}
    expected = aliases.get(supplied)
    if not expected:
        ranked = sorted(((SequenceMatcher(None, supplied, key).ratio(), value) for key, value in DATA_TYPES.items()), reverse=True)
        if ranked[0][0] >= .8 and ranked[0][0] - ranked[1][0] >= .12:
            expected = ranked[0][1]
    return (subject, expected) if expected else None

def length_request(text):
    """Extract an explicit character count; preserve exact/minimum/maximum intent."""
    sentence = text.strip().rstrip('.')
    sentence = re.sub(r'[,;]?\s+(?:validate|check)\s+(?:(?:that|this|these|the)\s+)?columns?\s+only$', '', sentence, flags=re.I)
    def correct_unit(match):
        word = match[0].casefold()
        return 'characters' if word in {'char', 'chars', 'character', 'characters'} or SequenceMatcher(None, word, 'characters').ratio() >= .8 else word
    sentence = re.sub(r'\b[a-z]+\b', correct_unit, sentence, flags=re.I)
    patterns = [
        r'(?P<subject>[\w -]+?)\s+(?:(?:must|should)\s+(?:be\s+)?)?(?:contains?|have|has|be)\s+(?P<limit>only|exactly|at least|at most|no more than|minimum|maximum)?\s*(?P<count>\d+)\s+characters(?:\s+long)?',
        r'(?P<subject>[\w -]+?)\s+(?:length|character count)\s+(?:(?:must|should)\s+)?(?:be\s+)?(?P<limit>only|exactly|at least|at most|no more than|minimum|maximum)?\s*(?P<count>\d+)',
    ]
    for pattern in patterns:
        match = re.fullmatch(pattern, sentence, re.I)
        if match:
            subject = re.sub(r'\s+column$', '', match['subject'], flags=re.I).strip()
            limit = (match['limit'] or 'exactly').casefold()
            operator = '>=' if limit in {'at least', 'minimum'} else '<=' if limit in {'at most', 'maximum', 'no more than'} else '=='
            return subject, operator, int(match['count'])
    return None

def prefix_request(text):
    """Read a literal prefix, quoted or unquoted, without inventing it."""
    literal = r'(?:(?P<quote>["\'])(?P<quoted>[^\r\n]+?)(?P=quote)|(?P<bare>[\w./-]+))'
    pattern = (r'(?P<subject>[\w -]+?)\s+(?:(?:must|should)\s+(?:be\s+)?)?'
               r'(?:prefix\s+)?(?:starts?\s+(?:with\s+)?|begins?\s+with\s+)'
               r'(?:(?:the\s+)?(?:format|prefix)\s+)?' + literal + r'\s*\.?')
    match = re.fullmatch(pattern, text.strip(), re.I)
    if not match: return None
    subject = re.sub(r'^(?:each|every)\s+|\s+column$', '', match['subject'], flags=re.I).strip()
    if re.search(r'\b(?:must|should|contains?|exactly|at least|at most)\b', subject, re.I): return None
    return subject, match['quoted'] if match['quote'] else match['bare']

def parse_condition(text, columns):
    normalized = text.casefold().strip().rstrip('.')
    aliases = {}
    for column in columns:
        for alias in {column.casefold(), column.casefold().replace('_', ' ')}:
            aliases.setdefault(alias, []).append(column)
    length = length_request(text)
    if length:
        names = aliases.get(length[0].casefold(), [])
        if len(names) == 1:
            return {'status':'PARSED','type':'text_length','column':names[0],'operator':length[1],'length':length[2],'original_requirement':text}
    datatype = datatype_request(text)
    if datatype:
        names = aliases.get(datatype[0].casefold(), [])
        if len(names) == 1:
            return {'status':'PARSED', 'type':'data_type', 'column':names[0], 'expected_type':datatype[1], 'original_requirement':text}
    prefix = prefix_request(text)
    if prefix:
        subject, literal = prefix
        names = aliases.get(subject.casefold(), [])
        if len(names) == 1:
            return {'status':'PARSED', 'type':'starts_with', 'column':names[0],
                    'prefix':literal, 'original_requirement':text}

    # Longest names first; aliases must match one actual dataset column.
    for alias in sorted(aliases, key=len, reverse=True):
        if len(aliases[alias]) == 1:
            index = columns.index(aliases[alias][0])
            normalized = re.sub(r'(?<!\w)' + re.escape(alias) + r'(?!\w)', f'__column{index}__', normalized)
    token = r'__column(\d+)__'
    def col(value): return columns[int(value)]
    def result(kind, **values):
        return {'status': 'PARSED', 'type': kind, 'original_requirement': text, **values}
    match = re.fullmatch(r'(?:every \w+ must have a |each |every )?' + token + r'(?: must be| should be| must have)? (?:a )?(?:non-null and non-empty|non-empty and non-null|not null and not empty|populated|not empty|non-empty|non-null|not null)', normalized)
    if not match:
        match = re.fullmatch(r'every \w+ must have a (?:non-null and non-empty|non-empty and non-null) ' + token, normalized)
    if match: return result('not_empty', column=col(match[1]))
    match = re.fullmatch(r'(?:each |every )?' + token + r' (?:must|should) be unique(?: across (?:the )?(?:entire )?dataset)?', normalized)
    if match: return result('unique', column=col(match[1]))
    match = re.fullmatch(token + r' (?:must|should) be (?:a )?(?:valid )?(?:number )?(greater than|greater than or equal to|at least|less than|less than or equal to|at most) (zero|-?\d+(?:\.\d+)?)', normalized)
    if match:
        op = {'greater than':'>','greater than or equal to':'>=','at least':'>=','less than':'<','less than or equal to':'<=','at most':'<='}[match[2]]
        return result('numeric_comparison', column=col(match[1]), operator=op, value=0 if match[3]=='zero' else float(match[3]))
    match = re.fullmatch(token + r' (?:must|should) be (?:a )?(?:valid )?non-negative number', normalized)
    if match: return result('numeric_comparison', column=col(match[1]), operator='>=', value=0)
    match = re.fullmatch(token + r' (?:must|should) be one of (.+)', normalized)
    if match and not re.search(r'\b(?:and|must|should)\b', match[2]):
        # Take original case from the list; enum comparisons are case-sensitive.
        values_text = re.split(r'\b(?:must|should) be one of\s+', text, flags=re.I)[-1].strip().rstrip('.')
        values = [v.strip().strip('\'"') for v in re.split(r',\s*(?:or\s+)?|\s+or\s+', values_text) if v.strip()]
        if values: return result('allowed_values', column=col(match[1]), values=values)
    match = re.fullmatch(token + r' (?:must|should) not be in the future', normalized)
    if match: return result('not_future', column=col(match[1]))
    match = re.fullmatch(token + r' must equal ' + token + r' multiplied by ' + token + r' minus ' + token + r',? with an allowed difference of (\d+(?:\.\d+)?)', normalized)
    if match:
        return result('arithmetic', column=col(match[1]), multiply=[col(match[2]),col(match[3])], subtract=col(match[4]), tolerance=float(match[5]))
    match = re.fullmatch(r'for every ([\w-]+) (\w+), ' + token + r' must be populated and must not be earlier than ' + token, normalized)
    if match:
        candidates = [name for name in columns if name.casefold().replace('_',' ') == match[2] + ' status']
        if len(candidates)==1:
            value = re.match(r'for every ([\w-]+)',text,re.I)[1]
            return result('conditional_dates', when_column=candidates[0], when_value=value, column=col(match[3]), not_before=col(match[4]))
    return None

def validate_condition(frame, rule, ordinal):
    kind = rule.get('type')
    column = rule.get('column')
    if kind not in TYPES or column not in frame.columns:
        raise ValueError('Business rule contains an unsupported condition or missing column.')
    source = frame[column]
    if kind == 'text_length':
        count = rule.get('length'); operator = rule.get('operator')
        if isinstance(count, bool) or not isinstance(count, int) or count < 0 or operator not in {'==', '>=', '<='}:
            raise ValueError('Character length requires a non-negative integer and a supported comparison.')
        valid = source.map(lambda value: isinstance(value, str) and OPERATORS[operator](len(value), count))
    elif kind == 'data_type':
        from backend.rule_plugins.catalog_validation import _types
        if rule.get('expected_type') not in DATA_TYPES.values():
            raise ValueError('Unsupported business data type.')
        valid = _types(source, rule['expected_type'])
    elif kind == 'starts_with':
        prefix = rule.get('prefix')
        if not isinstance(prefix, str) or not prefix:
            raise ValueError('Provide a non-empty literal prefix for this business rule.')
        valid = source.map(lambda value: isinstance(value, str) and value.startswith(prefix))
    elif kind == 'not_empty':
        valid = source.notna() & source.astype(str).str.strip().ne('')
    elif kind == 'unique':
        valid = ~source.duplicated(keep=False)
    elif kind == 'allowed_values':
        values = rule.get('values')
        if not isinstance(values,list) or not values or any(not isinstance(v,str) for v in values):
            raise ValueError('Provide approved values for the business rule.')
        valid = source.notna() & source.astype(str).isin(values)
    elif kind == 'numeric_comparison':
        value = rule.get('value'); operator = rule.get('operator')
        if operator not in OPERATORS or isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
            raise ValueError('Provide a finite numeric threshold and supported operator.')
        numeric = pd.to_numeric(source,errors='coerce')
        valid = numeric.map(lambda v: pd.notna(v) and math.isfinite(v)) & OPERATORS[operator](numeric,value)
    elif kind == 'not_future':
        dates = pd.to_datetime(source,errors='coerce',format='mixed',utc=True)
        valid = dates.notna() & dates.le(pd.Timestamp.now(tz='UTC'))
    elif kind == 'arithmetic':
        multiply = rule.get('multiply'); subtract = rule.get('subtract'); tolerance = rule.get('tolerance')
        if not isinstance(multiply,list) or len(multiply)!=2 or any(c not in frame.columns for c in multiply) or subtract not in frame.columns:
            raise ValueError('Formula references missing columns.')
        if isinstance(tolerance,bool) or not isinstance(tolerance,(float,int)) or not math.isfinite(tolerance) or tolerance<0:
            raise ValueError('Formula tolerance must be finite and non-negative.')
        values = [pd.to_numeric(frame[c],errors='coerce') for c in [column,*multiply,subtract]]
        expected = values[1]*values[2]-values[3]
        valid = (values[0]-expected).abs().le(tolerance+1e-12)
        for value in values: valid &= value.map(lambda v: pd.notna(v) and math.isfinite(v))
    else:
        when = rule.get('when_column'); before = rule.get('not_before'); expected = rule.get('when_value')
        if when not in frame.columns or before not in frame.columns or not isinstance(expected,str):
            raise ValueError('Conditional rule references missing columns or condition.')
        applies = frame[when].eq(expected)
        dates = pd.to_datetime(source,errors='coerce',format='mixed',utc=True)
        baseline = pd.to_datetime(frame[before],errors='coerce',format='mixed',utc=True)
        valid = ~applies | (dates.notna() & baseline.notna() & dates.ge(baseline))
    condition = rule.get('original_requirement',kind)
    return [{'rule_id':f'BUSINESS-{ordinal:03d}','rule_name':rule.get('source_rule_id') or 'Business requirement','column':column,'row_index':int(index) if isinstance(index,int) else str(index),'status':'FAIL','reason':f'Requirement: {condition}','severity':'high','actual_value':str(frame.at[index,column]),'expected_condition':condition} for index in frame.index[~valid.fillna(False)]]
