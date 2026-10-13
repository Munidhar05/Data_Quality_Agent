"""Offline natural-language business rules. No provider calls or clarification prompts."""
from __future__ import annotations
import csv
import io
import math
import re

from backend.business_conditions import parse_condition
from backend.business_context import interpret_context, retrieve_columns

EXTENDED_TYPES = {"compound", "conditional", "literal_comparison", "text_match", "null_check"}
NUMBER = r"[+-]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d+)?"
OPERATORS = {"greater than or equal to": ">=", "less than or equal to": "<=", "no less than": ">=", "no more than": "<=", "not more than": "<=", "not less than": ">=", "at least": ">=", "at most": "<=", "greater than": ">", "more than": ">", "higher than": ">", "above": ">", "exceeds": ">", "exceed": ">", "less than": "<", "lower than": "<", "below": "<", "equal to": "==", "equals": "==", "equal": "==", "not equal to": "!=", "different from": "!="}


class NotApplied(ValueError):
    def __init__(self, classification, reason, candidates=()):
        self.classification, self.reason, self.candidates = classification, reason, list(candidates)
        super().__init__(reason)


def protect(text):
    literals = []
    def replace(match):
        literals.append(match[0])
        return f"__quoted{len(literals)-1}__"
    masked = re.sub(r"""(["'])(.*?)\1""", replace, text)
    def restore(value):
        for index, literal in enumerate(literals): value = value.replace(f"__quoted{index}__", literal)
        return value
    return masked, restore


def normalize(text, columns=()):
    masked, restore = protect(text.strip())
    masked = re.sub(r"^(?:please\s+)?(?:ensure(?:\s+that)?|check(?:\s+that)?|make sure(?:\s+that)?|validate(?:\s+that)?)\s+", "", masked, flags=re.I)
    masked = re.sub(r"\b(?:has to|have to|needs to|need to|is required to)\b", "must", masked, flags=re.I)
    masked = re.sub(r"\b(?:must|should)\s+be\s+(?=starts?|begins?|ends?|contains?)", "must ", masked, flags=re.I)
    masked = re.sub(r"\b(?:can't|cannot|mustn't|shouldn't|should not)\b", "must not", masked, flags=re.I)
    saved_names = []
    for alias in sorted({alias for name in columns for alias in (name, name.replace("_", " "))}, key=len, reverse=True):
        def save_name(match):
            saved_names.append(match[0])
            return f"__schema{len(saved_names)-1}__"
        masked = re.sub(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", save_name, masked, flags=re.I)
    masked = re.sub(r"\bone of\b", "__one_of__", masked, flags=re.I)
    # Convert written integer numbers outside quoted values.
    units = dict(zip("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split(), range(20)))
    tens = dict(zip("twenty thirty forty fifty sixty seventy eighty ninety".split(), range(20, 100, 10)))
    pattern = r"\b(?:" + "|".join([*units, *tens]) + r")(?:[- ](?:" + "|".join(units) + r"))?\b"
    def number(match):
        return str(sum(units.get(word, tens.get(word, 0)) for word in re.split(r"[- ]", match[0].casefold())))
    masked = re.sub(pattern, number, masked, flags=re.I)
    masked = masked.replace("__one_of__", "one of")
    for index, alias in enumerate(saved_names): masked = masked.replace(f"__schema{index}__", alias)
    return restore(re.sub(r"\s+", " ", masked)).strip().rstrip(".")


def resolve(subject, columns, metadata):
    subject = re.sub(r"^(?:the|each|every|all)\s+|\s+(?:column|field|values?)$", "", subject.strip(), flags=re.I).strip()
    exact = [name for name in columns if subject.casefold() in {name.casefold(), name.replace("_", " ").replace("-", " ").casefold()}]
    if len(exact) == 1: return exact[0]
    if len(exact) > 1: raise NotApplied("ambiguous", "The field name matches multiple schema columns.", exact)
    candidates = retrieve_columns(subject, columns, metadata)
    if not candidates or candidates[0]["score"] < .55:
        raise NotApplied("missing", "No sufficiently matching field exists in the uploaded schema.", [c["name"] for c in candidates])
    if len(candidates) > 1 and candidates[0]["score"] - candidates[1]["score"] < .12:
        raise NotApplied("ambiguous", "Multiple schema columns match the field description.", [c["name"] for c in candidates])
    return candidates[0]["name"]


def _literal(text):
    text = text.strip()
    if len(text) >= 2 and text[0] in "\"'" and text[-1] == text[0]: return text[1:-1]
    if re.fullmatch(NUMBER, text):
        value = float(text)
        if not math.isfinite(value): raise NotApplied("incomplete", "The numeric limit is not finite.")
        return int(value) if value.is_integer() else value
    if text.casefold() in {"true", "false"}: return text.casefold() == "true"
    if re.fullmatch(r"[\w./:@+-]+", text) and text.casefold() not in {"reasonable", "valid", "proper", "correct", "appropriate", "recent", "normal"}: return text
    raise NotApplied("incomplete", "The condition has no explicit, unambiguous expected value.")


def _subject_and_predicate(text, columns, metadata, inherited=None):
    masked, restore = protect(text)
    match = re.fullmatch(r"(.+?)\s+(?:must|should|shall|will)\s+(.+)", masked, re.I)
    if not match:
        match = re.fullmatch(r"(.+?)\s+(?:is|are)\s+(.+)", masked, re.I)
    if match: return resolve(restore(match[1]), columns, metadata), restore(match[2])
    aliases = [(alias, name) for name in columns for alias in {name, name.replace("_", " ")}]
    for alias, name in sorted(aliases, key=lambda item: -len(item[0])):
        if masked.casefold().startswith(alias.casefold() + " "):
            return name, restore(masked[len(alias):].strip())
    if inherited and re.match(r"^(?:be\s+|starts?\b|begins?\b|ends?\b|contains?\b|not\b|greater than\b|less than\b|at least\b|at most\b|above\b|below\b|[><=!])", masked, re.I):
        return inherited, text
    match = re.fullmatch(r"(.+?)\s+((?:starts?|begins?|ends?|contains?|between|>=|<=|!=|==|>|<|=|at least|at most|greater than|less than|above|below|equals?|not equal to)\b.*)", masked, re.I)
    if match: return resolve(restore(match[1]), columns, metadata), restore(match[2])
    if inherited: return inherited, text
    raise NotApplied("unsupported", "The sentence does not express a supported complete condition.")


def compile_rule(text, columns, metadata, inherited=None, depth=0):
    if depth > 6: raise NotApplied("unsupported", "The rule exceeds the supported nesting depth.")
    sentence = normalize(text, columns)
    # Existing full-sentence parsers preserve the supported formulas and date policies.
    direct = parse_condition(sentence, columns) or interpret_context(sentence, columns, metadata)
    if direct: return {key:value for key,value in direct.items() if key not in {"original_requirement", "mapping_reason", "confidence"}}
    masked, restore = protect(sentence)
    conditional = re.fullmatch(r"if (.+?)(?:,?\s+then\s+|,\s*)(.+)", masked, re.I)
    if conditional:
        when = compile_rule(restore(conditional[1]), columns, metadata, depth=depth+1)
        then = compile_rule(restore(conditional[2]), columns, metadata, depth=depth+1)
        return {"status":"PARSED", "type":"conditional", "when":when, "then":then}
    negative = re.fullmatch(r"(?:no (?:missing|blank|empty) (?:values? (?:in|for) )?|(?:do not|don't) allow (?:missing|blank|empty) )(.*)", masked, re.I)
    if negative: return {"status":"PARSED", "type":"not_empty", "column":resolve(restore(negative[1]),columns,metadata)}
    duplicate = re.fullmatch(r"(?:no (?:duplicate|repeated)s?(?: (?:values?|entries))?(?: (?:in|for))? |(?:do not|don't) allow duplicates? (?:in|for) )(.*)", masked, re.I)
    if duplicate: return {"status":"PARSED", "type":"unique", "column":resolve(restore(duplicate[1]),columns,metadata)}
    name, predicate = _subject_and_predicate(sentence, columns, metadata, inherited)
    equality_wording = bool(re.match(r"^be\s+", predicate, re.I)) or bool(re.search(r"\s(?:is|are)\s", sentence, re.I)) or inherited is not None
    predicate = re.sub(r"^(?:be |have )", "", predicate, flags=re.I).strip()
    def rule(kind, **fields): return {"status":"PARSED", "type":kind, "column":name, **fields}
    if re.fullmatch(r"(?:non-null and non-empty|not null and not empty|required|mandatory|filled(?: in)?|present|populated|not (?:null|empty|blank|missing)|non-null|non-empty|never (?:blank|empty|missing))",predicate,re.I): return rule("not_empty")
    if re.fullmatch(r"(?:unique|distinct|never repeat(?:ed)?|not repeat(?:ed)?|not duplicated|have no duplicates)",predicate,re.I): return rule("unique")
    if re.fullmatch(r"(?:null|missing|empty)",predicate,re.I): return rule("null_check", is_null=True)
    if re.fullmatch(r"(?:positive(?: number)?|non-negative(?: number)?|not negative)",predicate,re.I): return rule("numeric_comparison", operator=">" if predicate.casefold().startswith("positive") else ">=", value=0)
    if re.fullmatch(r"negative(?: number)?",predicate,re.I): return rule("numeric_comparison", operator="<", value=0)
    future = re.fullmatch(r"(?:not (?:be )?in the future|not (?:be )?a future date|(?:today or earlier|in the past or today))",predicate,re.I)
    if future: return rule("not_future")
    type_match = re.fullmatch(r"(?:a |an )?(integer|whole number|number|numeric|decimal|text|string|boolean|date|timestamp)",predicate,re.I)
    if type_match:
        expected={"integer":"IntegerType","whole number":"IntegerType","number":"DoubleType","numeric":"DoubleType","decimal":"DecimalType","text":"StringType","string":"StringType","boolean":"BooleanType","date":"DateType","timestamp":"TimestampType"}[type_match[1].casefold()]
        return rule("data_type",expected_type=expected)
    range_match = re.fullmatch(r"(?:between|in the range(?: of)?) ("+NUMBER+r") (?:and|to) ("+NUMBER+r")(?: (inclusive|exclusive))?",predicate,re.I)
    if range_match:
        low,high=float(range_match[1]),float(range_match[2])
        if low>high: raise NotApplied("conflicting","The lower bound exceeds the upper bound.")
        exclusive=range_match[3] and range_match[3].casefold()=="exclusive"
        return {"status":"PARSED","type":"compound","mode":"all","rules":[rule("numeric_comparison",operator=">" if exclusive else ">=",value=low),rule("numeric_comparison",operator="<" if exclusive else "<=",value=high)]}
    membership = re.fullmatch(r"(?:one of|in|either) (.+)",predicate,re.I)
    if membership:
        part, restore_values=protect(membership[1])
        values=[_literal(restore_values(item.strip())) for item in re.split(r",\s*(?:or\s+)?|\s+or\s+",part) if item.strip()]
        if values and all(isinstance(value,str) for value in values): return rule("allowed_values",values=values)
        raise NotApplied("unsupported","Approved values must be an explicit list of text values.")
    # Split boolean clauses only outside quoted literals and comparison phrases.
    protected, restore_predicate=protect(predicate)
    protected=re.sub(r"\b(greater than|less than) or equal to\b",lambda m:m[1]+" __inclusive__",protected,flags=re.I)
    for connector,mode in [("or","any"),("and","all")]:
        parts=re.split(r"\s+"+connector+r"\s+",protected,flags=re.I)
        if len(parts)>1:
            children=[compile_rule(restore_predicate(part).replace("__inclusive__","or equal to"),columns,metadata,inherited=name,depth=depth+1) for part in parts]
            return {"status":"PARSED","type":"compound","mode":mode,"rules":children}
    prefix = re.fullmatch(r"(?:starts?|begins?|ends?) with (.+)",predicate,re.I)
    if prefix:
        literal=_literal(prefix[1])
        if not isinstance(literal,str) or not literal: raise NotApplied("incomplete","A non-empty text prefix or suffix is required.")
        return rule("text_match",match="ends_with",value=literal) if predicate.casefold().startswith("end") else rule("starts_with",prefix=literal)
    contains = re.fullmatch(r"(not )?(?:contains?|contain|includes?|include) (.+)",predicate,re.I)
    if contains:
        literal=_literal(contains[2])
        if not isinstance(literal,str) or not literal: raise NotApplied("incomplete","A non-empty literal text fragment is required.")
        return rule("text_match",match="not_contains" if contains[1] else "contains",value=literal)
    comparison=re.fullmatch(r"(>=|<=|!=|==|>|<|=)\s*(.+)",predicate)
    if comparison:
        op="==" if comparison[1]=="=" else comparison[1]; expected=comparison[2]
    else:
        comparison=re.fullmatch("("+"|".join(re.escape(term) for term in sorted(OPERATORS,key=len,reverse=True))+r") (.+)",predicate,re.I)
        date_comparison=re.fullmatch(r"(not earlier than|not before|not later than|not after|before|after|earlier than|later than) (.+)",predicate,re.I)
        if date_comparison:
            op={"not earlier than":">=","not before":">=","not later than":"<=","not after":"<=","before":"<","earlier than":"<","after":">","later than":">"}[date_comparison[1].casefold()]
            right=resolve(date_comparison[2],columns,metadata)
            return {"status":"PARSED","type":"cross_column","left":name,"right":right,"operator":op}
        if not comparison:
            if equality_wording:
                try:
                    value = _literal(predicate)
                    return rule("numeric_comparison", operator="==", value=value) if type(value) in (int,float) else rule("literal_comparison", operator="==", value=value)
                except NotApplied:
                    pass
            raise NotApplied("incomplete" if re.search(r"\b(?:reasonable|valid|correct|proper|recent|high|low|limit|format)\b",predicate,re.I) else "unsupported","The condition is incomplete or cannot be executed safely by the offline interpreter.")
        op=OPERATORS[comparison[1].casefold()]; expected=comparison[2]
    # An exact RHS schema reference has priority over an unquoted literal.
    rhs=[column for column in columns if expected.casefold() in {column.casefold(),column.replace('_',' ').casefold()}]
    if len(rhs)==1: return {"status":"PARSED","type":"cross_column","left":name,"right":rhs[0],"operator":op}
    value=_literal(expected)
    if type(value) in (int,float): return rule("numeric_comparison",operator=op,value=value)
    if op in {"==","!="}: return rule("literal_comparison",operator=op,value=value)
    raise NotApplied("unsupported","An ordered comparison needs a numeric limit or a second schema column.")


def references(rule):
    if rule["type"]=="compound": return set().union(*(references(child) for child in rule["rules"]))
    if rule["type"]=="conditional": return references(rule["when"]) | references(rule["then"])
    return {rule[key] for key in ("column","left","right","when_column","not_before","subtract") if key in rule} | set(rule.get("multiply",[]))


def parse_offline_batch(text, columns, metadata):
    table=list(csv.reader(io.StringIO(text)))
    header=[re.sub(r"[^a-z]","",value.casefold()) for value in table[0]] if table else []
    index=next((i for i,value in enumerate(header) if value in {"businessrulenlp","businessrule","requirement","ruletext","description"}),None)
    points=[]
    if index is not None:
        for row in table[1:]:
            if not any(value.strip() for value in row): continue
            if index>=len(row) or not row[index].strip(): raise ValueError("Every CSV rule row must contain a business requirement.")
            extra={key:row[header.index(field)].strip() for key,field in [("source_rule_id","ruleid"),("category","category")] if field in header and header.index(field)<len(row)}
            points.append((row[index].strip(),extra))
    else:
        masked,restore=protect(text)
        for line in re.split(r"[\r\n]+|(?<=[.!?])\s+(?=[A-Za-z])",masked):
            point=restore(re.sub(r"^\s*(?:[-*?]\s*|\d+[.)]\s*)","",line)).strip()
            if point: points.append((point,{}))
    if not points or len(points)>100: raise ValueError("Enter between 1 and 100 rule points.")
    rules,unresolved=[],[]
    for point,extra in points:
        try:
            if len(point)>4000: raise NotApplied("unsupported","The rule exceeds the 4,000-character offline interpretation limit.")
            rule=compile_rule(point,columns,metadata)
            refs=references(rule)
            if not refs <= set(columns): raise NotApplied("missing","The rule refers to a field absent from the schema.")
            rules.append({**rule,**extra,"original_requirement":point,"mapping_reason":"Interpreted offline against the uploaded schema.","evidence":[{"source":"user_requirement","quote":point},{"source":"schema","columns":sorted(refs)}]})
        except NotApplied as error:
            unresolved.append({"requirement":point,"status":"NOT_APPLIED","classification":error.classification,"reason":error.reason,"clarification":error.reason,"candidate_columns":[{"name":name} for name in error.candidates],"suggestions":[],"choices":[]})
    return {"rules":rules,"unresolved":unresolved,"mode":"offline","message":f"{len(rules)} rule(s) interpreted offline. {len(unresolved)} not applied." if unresolved else f"All {len(rules)} rule(s) interpreted offline. Review them before validation."}
