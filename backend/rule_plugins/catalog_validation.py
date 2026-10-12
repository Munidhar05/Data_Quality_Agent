from __future__ import annotations
import math
import json
import re
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
from backend.profiler import _pyspark_type


CATALOG = json.loads(Path(__file__).resolve().parents[1].joinpath("catalog_checks.json").read_text(encoding="utf-8"))
IMPLEMENTATIONS = {1: 1, 2: 4, 3: 11, 4: 17, 5: 19, 6: 21, 7: 27, 8: 28, 9: 29, 10: 50, 11: 37, 12: 43, 13: 56, 14: 63, 15: 66, 16: 77, 17: 78, 18: 69, 19: 80}
EXAMPLES = {1: {'schema': {'id': 'LongType'}, 'exact': True}, 2: {'column': 'id', 'type': 'LongType'}, 3: {'columns': ['id']}, 4: {'columns': ['id', 'name']}, 5: {}, 6: {'columns': ['id', 'date']}, 7: {'column': 'amount', 'minimum': 0, 'maximum': 100}, 8: {'column': 'status', 'values': ['active', 'inactive']}, 9: {'column': 'code', 'pattern': '^[A-Z]{2}[0-9]{3}$'}, 10: {'column': 'parent_id', 'reference_column': 'id', 'reference_dataset_id': 'UPLOAD_REFERENCE_FILE'}, 11: {'keys': ['id'], 'columns': ['amount'], 'reference_dataset_id': 'UPLOAD_REFERENCE_FILE', 'tolerance': 0}, 12: {'left': 'start', 'operator': '<=', 'right': 'end'}, 13: {'column': 'updated_at', 'maximum_hours': 24, 'now': '2026-10-09T00:00:00Z'}, 14: {'expected': 100}, 15: {'baseline_counts': [100, 110, 105], 'maximum_deviation_percent': 20}, 16: {'column': 'amount', 'iqr_multiplier': 1.5}, 17: {'column': 'amount', 'reference_column': 'amount', 'reference_dataset_id': 'UPLOAD_REFERENCE_FILE', 'bins': [0, 10, 20, 100], 'maximum_distance': 0.2}, 18: {'keys': ['id'], 'columns': ['amount'], 'reference_dataset_id': 'UPLOAD_REFERENCE_FILE', 'tolerance': 0}, 19: {'when_column': 'status', 'when_values': ['active'], 'column': 'amount', 'operator': '>', 'value': 0}}

def _require(p, *names):
    for name in names:
        if name not in p or p[name] is None or p[name] == '':
            raise ValueError(f"Provide {name.replace('_', ' ')}.")

def _cols(frame, p, key='columns'):
    _require(p, key)
    names = p[key]
    if not isinstance(names, list) or not names or any((name not in frame.columns for name in names)):
        raise ValueError(f'{key} must be a non-empty list of existing columns.')
    return names

def _series(frame, p, key='column'):
    _require(p, key)
    if p[key] not in frame.columns:
        raise ValueError(f'Unknown column: {p[key]}')
    return frame[p[key]]

def _reference(p):
    from backend.storage import LocalFileStorage
    _require(p, 'reference_dataset_id')
    if p['reference_dataset_id'] == 'UPLOAD_REFERENCE_FILE':
        raise ValueError('Upload the reference file first.')
    try:
        return LocalFileStorage().load_dataset(p['reference_dataset_id'])
    except (ValueError, FileNotFoundError) as error:
        raise ValueError(str(error)) from error

def _num(s):
    return pd.to_numeric(s, errors='coerce')

def _date(s):
    return pd.to_datetime(s, errors='coerce', format='mixed', utc=True)

def _compare(a, op, b):
    ops = {'<': lambda: a < b, '<=': lambda: a <= b, '>': lambda: a > b, '>=': lambda: a >= b, '==': lambda: a == b, '!=': lambda: a != b}
    if op not in ops:
        raise ValueError('Use <, <=, >, >=, == or !=.')
    return ops[op]()

def _missing(s):
    return s.isna() | s.astype(str).str.strip().eq('')

def _types(s, expected):
    t = str(expected).lower().replace('type', '')
    if t in {'string', 'str'}:
        return s.map(lambda x: isinstance(x, str))
    if t in {'integer', 'int', 'long', 'short', 'byte'}:
        x = _num(s)
        bits = {'byte': 8, 'short': 16, 'integer': 32, 'int': 32, 'long': 64}[t]
        return x.notna() & (x % 1 == 0) & x.ge(-2 ** (bits - 1)) & x.lt(2 ** (bits - 1))
    if t in {'number', 'float', 'double', 'decimal'}:
        return _num(s).notna() & _num(s).map(lambda x: math.isfinite(x) if pd.notna(x) else False)
    if t in {'date', 'timestamp', 'datetime'}:
        return _date(s).notna()
    if t in {'bool', 'boolean'}:
        return s.astype(str).str.lower().isin(['true', 'false', '1', '0', 'yes', 'no'])
    if t == 'null':
        return s.isna()
    raise ValueError(f'Unsupported configured data type: {expected}')

def _aggregate(s, op):
    if op not in {'sum', 'mean', 'median', 'min', 'max', 'count', 'std'}:
        raise ValueError('Unsupported aggregate.')
    if op == 'count':
        return s.count()
    x = _num(s)
    if x.isna().any():
        raise ValueError('Aggregate requires non-missing numeric values.')
    return getattr(x, op)()

def _bounds(v, p):
    if 'minimum' not in p and 'maximum' not in p:
        raise ValueError('Provide a minimum or maximum.')
    if 'minimum' in p and 'maximum' in p and (float(p['minimum']) > float(p['maximum'])):
        raise ValueError('Minimum exceeds maximum.')
    return pd.notna(v) and ('minimum' not in p or v >= float(p['minimum'])) and ('maximum' not in p or v <= float(p['maximum']))

def evaluate(number, frame, p):
    """Return an invalid-record mask; dataset-level violations reject all records."""
    good = pd.Series(False, index=frame.index, dtype=bool)

    def global_result(valid):
        result = pd.Series(not bool(valid), index=frame.index, dtype=bool)
        result.attrs['dataset_failed'] = not bool(valid)
        return result
    n = IMPLEMENTATIONS[number]
    if n in {1}:
        _require(p, 'schema')
        schema = p['schema']
        if not isinstance(schema, dict) or not schema:
            raise ValueError('Supply an expected schema object.')
        if not set(schema).issubset(frame.columns):
            return global_result(False)
        exact = p.get('exact', n != 10 and (not p.get('allow_extra', False)))
        if exact and set(schema) != set(frame.columns):
            return global_result(False)
        for c, t in schema.items():
            good |= ~_types(frame[c], t) & frame[c].notna()
        return good
    if n in {4}:
        s = _series(frame, p)
        _require(p, 'type')
        return s.notna() & ~_types(s, p['type'])
    if n in {11, 17}:
        return frame[_cols(frame, p)].apply(_missing).any(axis=1)
    if n == 19:
        return frame.duplicated(keep='first')
    if n in {21}:
        return frame.duplicated(subset=_cols(frame, p), keep='first')
    if n in {27}:
        s = _series(frame, p)
        return s.notna() & ~_num(s).map(lambda x: _bounds(x, p))
    if n in {28}:
        s = _series(frame, p)
        _require(p, 'values')
        return s.notna() & ~s.astype(str).isin(list(map(str, p['values'])))
    if n in {29}:
        s = _series(frame, p)
        _require(p, 'pattern')
        return s.notna() & ~s.astype(str).str.fullmatch(p['pattern'])
    if n in {50}:
        s = _series(frame, p)
        ref = _reference(p)
        rs = _series(ref, {'column': p.get('reference_column', p['column'])})
        invalid = s.notna() & ~s.astype(str).isin(rs.dropna().astype(str))
        if n == 54:
            _require(p, 'minimum_percent')
            return global_result((100 * (1 - invalid.mean()) if len(s) else 100) >= float(p['minimum_percent']))
        return invalid
    if n in {37, 69}:
        keys = _cols(frame, p, 'keys')
        cols = _cols(frame, p)
        ref = _reference(p)
        _cols(ref, p, 'keys')
        _cols(ref, p)
        if ref.duplicated(subset=keys).any():
            raise ValueError('Reference keys must be unique for record comparison.')
        lookup = {tuple((str(row[k]) for k in keys)): row for _, row in ref.iterrows()}
        tolerance = float(p.get('tolerance', 0))
        for index, row in frame.iterrows():
            target = lookup.get(tuple((str(row[k]) for k in keys)))
            valid = target is not None
            if valid:
                for c in cols:
                    a, b = (row[c], target[c])
                    if pd.isna(a) and pd.isna(b):
                        continue
                    if pd.isna(a) or pd.isna(b):
                        valid = False
                        break
                    try:
                        equal = abs(float(a) - float(b)) <= tolerance
                    except (ValueError, TypeError):
                        equal = str(a) == str(b)
                    if not equal:
                        valid = False
                        break
            good.at[index] = not valid
        if n == 69 and len(frame) != len(ref):
            return global_result(False)
        return good
    if n in {43}:
        left = _series(frame, p, 'left')
        right = _series(frame, p, 'right')
        _require(p, 'operator')
        if p.get('as_dates', n in {48, 84}):
            left, right = (_date(left), _date(right))
        return left.isna() | right.isna() | ~_compare(left, p['operator'], right)
    if n in {56}:
        dt = _date(_series(frame, p))
        threshold = 'maximum_days' if n == 100 else 'maximum_hours'
        _require(p, threshold)
        now = pd.to_datetime(p.get('now', datetime.now(timezone.utc).isoformat()), utc=True)
        ages = (now - dt).dt.total_seconds()
        limit = float(p[threshold]) * (86400 if n == 100 else 3600)
        return dt.isna() | (ages < 0) | (ages > limit)
    if n in {63}:
        if n in {63, 96}:
            _require(p, 'expected')
            valid = len(frame) == int(p['expected'])
        else:
            _require(p, 'minimum' if n == 64 else 'maximum')
            valid = _bounds(len(frame), p)
        if n == 96:
            _require(p, 'required_columns')
            valid = valid and set(p['required_columns']).issubset(frame.columns)
        return global_result(valid)
    if n == 66:
        _require(p, 'baseline_counts', 'maximum_deviation_percent')
        values = pd.Series(p['baseline_counts'], dtype=float)
        if values.empty:
            raise ValueError('Baseline counts cannot be empty.')
        baseline = values.mean()
        deviation = abs(len(frame) - baseline) / abs(baseline) * 100 if baseline else 0 if len(frame) == 0 else float('inf')
        return global_result(deviation <= float(p['maximum_deviation_percent']))
    if n == 77:
        s = _num(_series(frame, p))
        _require(p, 'iqr_multiplier')
        q1, q3 = s.quantile([0.25, 0.75])
        iqr = q3 - q1
        k = float(p['iqr_multiplier'])
        return s.isna() | (s < q1 - k * iqr) | (s > q3 + k * iqr)
    if n == 78:
        s = _num(_series(frame, p))
        ref = _reference(p)
        rs = _num(_series(ref, {'column': p.get('reference_column', p['column'])}))
        _require(p, 'bins', 'maximum_distance')
        bins = p['bins']
        if len(bins) < 2 or any((a >= b for a, b in zip(bins, bins[1:]))):
            raise ValueError('Provide increasing bin boundaries.')
        if s.isna().any() or rs.isna().any() or (not s.between(bins[0], bins[-1]).all()) or (not rs.between(bins[0], bins[-1]).all()):
            raise ValueError('Bins must cover all numeric values in both datasets.')
        a = pd.cut(s, bins, include_lowest=True).value_counts(normalize=True, sort=False)
        b = pd.cut(rs, bins, include_lowest=True).value_counts(normalize=True, sort=False)
        return global_result(float((a - b).abs().sum() / 2) <= float(p['maximum_distance']))
    if n == 80:
        when = _series(frame, p, 'when_column')
        s = _series(frame, p)
        _require(p, 'when_values', 'operator', 'value')
        value = p['value']
        if isinstance(value, (float, int)):
            s = _num(s)
        return when.isin(p['when_values']) & (s.isna() | ~_compare(s, p['operator'], value))
    raise ValueError(f'No evaluator for catalog check {n}.')

class CatalogRule:
    def __init__(self, group, check):self.group,self.check=group,check;self.number=check['number']
    def get_metadata(self):
        return {'rule_id':f'DQ-CATALOG-{self.number:03d}','version':'2.0','name':self.check['name'],'category':self.group['name'],'catalog_number':self.number,'description':self.check['description']+'. Dataset-level violations reject every record.','applies_to':'uploaded data and user-configured expectations','predicate':'unique_rows' if self.number == 5 else 'catalog_check','severity':'medium','configuration':{'parameters':{'type':'json','required':True,'description':'Check parameters (JSON)','example':EXAMPLES[self.number]}},'parameter_example':EXAMPLES[self.number]}
    def check_applicability(self, profile):
        parameters = self.generate_configuration(profile)['parameters']
        ready = self.number in {3,4,5} or (self.number == 6 and bool(parameters.get('columns'))) or (self.number == 16 and bool(parameters.get('column')))
        return {'applicability':'RECOMMENDED' if ready else 'REQUIRES_USER_INPUT','confidence':0.9 if ready else 0.5,'columns':parameters.get('columns',[]),'evidence':['Settings suggested from the uploaded file. Review them before running.' if ready else 'This check needs a business expectation or reference data.'],'requires_user_input':not ready}
    def generate_configuration(self, profile):
        columns=profile.get('columns',[])
        names=[column['name'] for column in columns]
        first=names[0] if names else ''
        identifiers=[c['name'] for c in columns if any(x.get('type')=='IDENTIFIER' and x.get('confidence',0)>=0.8 for x in c.get('semantic_candidates',[]))]
        numeric=[c['name'] for c in columns if c.get('statistics',{}).get('mean') is not None and c['name'] not in identifiers]
        dates=[c['name'] for c in columns if any(x.get('type') in {'DATE','TIMESTAMP'} for x in c.get('semantic_candidates',[]))]
        missing=[c['name'] for c in columns if c.get('null_count',0)>0]
        n=self.number
        from copy import deepcopy
        p=deepcopy(EXAMPLES[n])
        for key in ['column','left','right','actual_column','when_column']:
            if key in p:p[key]=''
        if n==1:p={'schema':{c['name']:c.get('pyspark_type','StringType') for c in columns},'exact':True}
        elif n==2:p={'column':first,'type':columns[0].get('pyspark_type','StringType') if columns else 'StringType'}
        elif n in {3,4}:p={'columns':missing or names}
        elif n==5:p={}
        elif n==6:p={'columns':identifiers}
        elif n==7:p={'column':numeric[0] if numeric else first,'minimum':None,'maximum':None}
        elif n==8:p={'column':first,'values':[]}
        elif n==9:
            emails=[c['name'] for c in columns if any(x.get('type')=='EMAIL' for x in c.get('semantic_candidates',[]))]
            p={'column':emails[0] if emails else first,'pattern':r'[^\s@]+@[^\s@]+\.[^\s@]+' if emails else ''}
        elif n==10:p={'column':first,'reference_column':first,'reference_dataset_id':''}
        elif n in {11,18}:p={'keys':identifiers[:1] or names[:1],'columns':[c for c in names if c not in (identifiers[:1] or names[:1])],'reference_dataset_id':'','tolerance':0}
        elif n==12:p={'left':dates[0] if len(dates)>1 else '', 'right':dates[1] if len(dates)>1 else '', 'operator':'<='}
        elif n==13:p={'column':dates[0] if dates else '', 'maximum_hours':None, 'now':datetime.now(timezone.utc).isoformat()}
        elif n==14:p={'expected':None}
        elif n==15:p={'baseline_counts':[],'maximum_deviation_percent':None}
        elif n==16:p={'column':numeric[0] if numeric else '', 'iqr_multiplier':1.5}
        elif n==17:
            c=next((c for c in columns if c['name'] in numeric),None)
            p={'column':c['name'] if c else '', 'reference_column':c['name'] if c else '', 'reference_dataset_id':'','bins':[], 'maximum_distance':0.2}
        elif n==19:p={'when_column':'','when_values':[],'column':'','operator':'>','value':None}
        return {'parameters':p}
    def validate(self,data,configuration):
        p=configuration.get('parameters', {} if self.number == 5 else None)
        if not isinstance(p,dict):raise ValueError(f'{self.check["name"]}: supply a JSON parameters object.')
        if set(EXAMPLES[self.number])-set(p):raise ValueError('Missing parameters: '+', '.join(sorted(set(EXAMPLES[self.number])-set(p))))
        try:mask=evaluate(self.number,data,p).fillna(True).astype(bool)
        except (KeyError,TypeError,OverflowError,re.error) as error:raise ValueError(f'{self.check["name"]}: invalid configuration ({error}).') from error
        rule=self.get_metadata();failures=[]
        for index in data.index[mask]:
            failures.append({'rule_id':rule['rule_id'],'rule_name':rule['name'],'column':p.get('column'),'row_index':int(index) if isinstance(index,int) else str(index),'status':'FAIL','reason':f'{rule["name"]} failed the configured expectation.','severity':rule['severity'],'actual_value':json.dumps(data.loc[index].to_dict(),default=str),'expected_condition':json.dumps(p,default=str)})
        if data.empty and mask.attrs.get('dataset_failed'):
            failures.append({'rule_id':rule['rule_id'],'rule_name':rule['name'],'column':None,'row_index':'DATASET','status':'FAIL','reason':'Empty dataset fails the configured expectation.','severity':'medium','actual_value':'0 records','expected_condition':json.dumps(p)})
        return failures
    def explain_failure(self,result):return result['reason']

RULE_PLUGINS=[CatalogRule(group,check) for group in CATALOG for check in group['checks']]
