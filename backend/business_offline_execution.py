"""Bounded execution for offline compound rules, using existing leaf validators."""
from __future__ import annotations
import math
import pandas as pd
from backend.business_offline import EXTENDED_TYPES


def validate_extended(frame, rule, ordinal, leaf_validator):
    budget=[0]
    columns=set(frame.columns)
    original=rule.get("original_requirement", "Business requirement")
    if not isinstance(original,str): raise ValueError("Invalid rule evidence.")
    def check(node,depth):
        budget[0]+=1
        if depth>6 or budget[0]>128 or not isinstance(node,dict): raise ValueError("Business condition exceeds complexity limits.")
        kind=node.get("type")
        if not isinstance(kind,str): raise ValueError("Invalid condition type.")
        if kind=="compound":
            children=node.get("rules")
            if not isinstance(node.get("mode"),str) or node["mode"] not in {"all","any"} or not isinstance(children,list) or not 2<=len(children)<=32: raise ValueError("Invalid compound condition.")
            values=[check(child,depth+1) for child in children]
            valid=values[0]
            for value in values[1:]: valid=valid & value if node["mode"]=="all" else valid | value
            return valid
        if kind=="conditional":
            when=check(node.get("when"),depth+1)
            then=check(node.get("then"),depth+1)
            return ~when | then
        if kind in {"literal_comparison","text_match","null_check"}:
            name=node.get("column")
            if not isinstance(name,str) or name not in columns: raise ValueError("Condition references an unknown schema column.")
            source=frame[name]
            if kind=="literal_comparison":
                value=node.get("value")
                if type(value) not in {str,int,float,bool} or type(value) in {int,float} and not math.isfinite(value) or not isinstance(node.get("operator"),str) or node["operator"] not in {"==","!="}: raise ValueError("Invalid literal comparison.")
                valid=source.eq(value) if node["operator"]=="==" else source.ne(value)
                return source.notna() & valid
            if kind=="null_check":
                if type(node.get("is_null")) is not bool: raise ValueError("Invalid null predicate.")
                return source.isna() if node["is_null"] else source.notna()
            value=node.get("value"); operation=node.get("match")
            if not isinstance(value,str) or not value or not isinstance(operation,str) or operation not in {"ends_with","contains","not_contains"}: raise ValueError("Invalid literal text condition.")
            return source.map(lambda item: isinstance(item,str) and (item.endswith(value) if operation=="ends_with" else value not in item if operation=="not_contains" else value in item))
        from backend.business_conditions import TYPES
        if kind not in TYPES | {"comparison","cross_column"}: raise ValueError("Unsupported child condition.")
        if "operator" in node and not isinstance(node["operator"],str): raise ValueError("Invalid comparison operator.")
        for key in ("column","left","right","when_column","not_before","subtract"):
            if key in node and (not isinstance(node[key],str) or node[key] not in columns): raise ValueError("Unknown child schema column.")
        failures=leaf_validator(frame,{**node,"status":"PARSED","original_requirement":original},ordinal)
        failed={str(item["row_index"]) for item in failures}
        return pd.Series([str(index) not in failed for index in frame.index],index=frame.index,dtype=bool)
    valid=check(rule,0).fillna(False)
    return [{"rule_id":f"BUSINESS-{ordinal:03d}","rule_name":rule.get("source_rule_id") or "Business requirement","column":rule.get("column","Multiple fields"),"row_index":int(index) if isinstance(index,int) else str(index),"status":"FAIL","reason":f"Requirement: {original}","severity":"high","actual_value":{str(name):str(value) for name,value in frame.loc[index].items()},"expected_condition":original} for index in frame.index[~valid]]
