"""Compare observed schemas without guessing column renames."""
import hashlib
import json
from backend.profiler import _pyspark_type


def snapshot(frame):
    columns = [{'name': str(name), 'type': _pyspark_type(frame[name]),
                'has_missing_values': bool(frame[name].isna().any())} for name in frame.columns]
    canonical = json.dumps([{'name': c['name'], 'type': c['type']} for c in columns], sort_keys=True, separators=(',', ':'))
    return {'columns': columns, 'fingerprint': hashlib.sha256(canonical.encode()).hexdigest(),
            'basis': 'Observed imported values; not source DDL or a nullability constraint.'}


def compare(baseline, current, renames=None):
    before = {c['name']: c for c in baseline['columns']}
    after = {c['name']: c for c in current['columns']}
    renames = renames or {}
    if len(set(renames.values())) != len(renames):
        raise ValueError('Rename targets must be unique.')
    for old, new in renames.items():
        if old not in before or new not in after or old in after or new in before:
            raise ValueError('Rename mappings must link a removed column to a newly added column.')
    mapped = {renames.get(name, name): value for name, value in before.items()}
    added, removed = sorted(after.keys() - mapped.keys()), sorted(mapped.keys() - after.keys())
    changes = [{'column': name, 'before': mapped[name]['type'], 'after': after[name]['type']}
               for name in sorted(mapped.keys() & after.keys()) if mapped[name]['type'] != after[name]['type']]
    missing = [{'column': name, 'before': mapped[name]['has_missing_values'], 'after': after[name]['has_missing_values']}
               for name in sorted(mapped.keys() & after.keys()) if mapped[name]['has_missing_values'] != after[name]['has_missing_values']]
    before_order = [renames.get(c['name'], c['name']) for c in baseline['columns']]
    after_order = [c['name'] for c in current['columns']]
    reordered = [x for x in before_order if x in after] != [x for x in after_order if x in mapped]
    return {'changed': bool(added or removed or changes or renames or reordered),
            'added_columns': added, 'removed_columns': removed, 'type_changes': changes,
            'renamed_columns': [{'before': old, 'after': new} for old, new in renames.items()],
            'column_order_changed': reordered, 'missing_value_changes': missing,
            'baseline': baseline, 'current': current}
