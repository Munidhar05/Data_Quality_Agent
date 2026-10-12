from __future__ import annotations

import re
from collections import Counter
from typing import Any

import pandas as pd

EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
PHONE_PATTERN = re.compile(r"^\+?[\d().\-\s]{7,}$")
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
CURRENCY_PATTERN = re.compile(r"^[\s]*[$€£¥]?\s*-?\d[\d,.]*\s*[$€£¥]?\s*$")
DATE_SHAPE_PATTERN = re.compile(r"^(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}(?:[ T].*)?|\d{1,2}[-/]\d{1,2}[-/]\d{2,4}(?:[ T].*)?|\d{1,2}[ -][A-Za-z]{3,9}[ -]\d{2,4})$")


def _pyspark_type(series: pd.Series) -> str:
    """Describe the equivalent Spark SQL type without changing stored data."""
    dtype = series.dtype
    types = pd.api.types
    if types.is_bool_dtype(dtype):
        return "BooleanType"
    if types.is_integer_dtype(dtype):
        bits = getattr(dtype, "itemsize", 8) * 8
        if types.is_unsigned_integer_dtype(dtype):
            return {8: "ShortType", 16: "IntegerType", 32: "LongType"}.get(bits, "DecimalType(20, 0)")
        return {8: "ByteType", 16: "ShortType", 32: "IntegerType", 64: "LongType"}.get(bits, "LongType")
    if types.is_float_dtype(dtype):
        return "FloatType" if getattr(dtype, "itemsize", 8) <= 4 else "DoubleType"
    if types.is_datetime64_any_dtype(dtype):
        return "TimestampType"
    if types.is_timedelta64_dtype(dtype):
        return "DayTimeIntervalType"
    values = series.dropna()
    if values.empty:
        return "NullType"
    inferred = types.infer_dtype(values, skipna=True)
    return {"boolean": "BooleanType", "integer": "LongType", "floating": "DoubleType", "mixed-integer-float": "DoubleType", "datetime": "TimestampType", "datetime64": "TimestampType", "date": "DateType", "bytes": "BinaryType"}.get(inferred, "StringType")


def _safe_value(value: Any) -> Any:
    if pd.isna(value):
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _semantic_candidates(series: pd.Series, distinct_count: int, null_count: int) -> list[dict[str, Any]]:
    values = series.dropna()
    if values.empty:
        return [{"type": "UNKNOWN", "confidence": 0.0, "evidence": ["No non-missing values are available."]}]

    strings = values.astype(str).str.strip()
    sample = strings.head(2000)
    candidates: list[dict[str, Any]] = []

    def add(kind: str, score: float, evidence: str) -> None:
        candidates.append({"type": kind, "confidence": round(min(score, 0.99), 2), "evidence": [evidence]})

    email_rate = float(sample.str.match(EMAIL_PATTERN).mean())
    if email_rate >= 0.6:
        add("EMAIL", email_rate, f"{email_rate:.0%} of sampled values match an email structure.")

    date_shapes = sample.str.match(DATE_SHAPE_PATTERN)
    date_shape_rate = float(date_shapes.mean())
    phone_rate = float(sample.str.match(PHONE_PATTERN).mean())
    if phone_rate >= 0.75 and date_shape_rate < 0.5:
        add("PHONE", phone_rate, f"{phone_rate:.0%} of sampled values match a phone-like structure.")

    if pd.api.types.is_bool_dtype(series.dtype):
        add("BOOLEAN", 0.99, "The physical values have a boolean type.")
    elif pd.api.types.is_datetime64_any_dtype(series.dtype):
        add("TIMESTAMP", 0.95, "The physical values have a datetime type.")
    elif pd.api.types.is_numeric_dtype(series.dtype):
        numeric = pd.to_numeric(values, errors="coerce").dropna()
        if not numeric.empty:
            if float(numeric.between(0, 1).mean()) >= 0.9:
                add("PERCENTAGE", 0.72, "Most numeric values fall between 0 and 1.")
            add("NUMERIC_MEASURE", 0.75, "Values have a numeric physical type.")
    else:
        if date_shape_rate >= 0.8:
            parsed_dates = pd.to_datetime(sample[date_shapes], errors="coerce", format="mixed", utc=True)
            date_rate = float(parsed_dates.notna().mean())
            if date_rate >= 0.8:
                add("DATE", date_shape_rate * date_rate, f"{date_shape_rate:.0%} of sampled values match a date-shaped pattern; {date_rate:.0%} of those parse as dates or timestamps.")
        currency_rate = float(sample.str.match(CURRENCY_PATTERN).mean())
        if currency_rate >= 0.8 and sample.str.contains(r"[$€£¥]", regex=True).any():
            add("CURRENCY", currency_rate, f"{currency_rate:.0%} of sampled values follow a currency-like format.")
        cardinality = distinct_count / max(len(values), 1)
        has_specialized_format = any(candidate["type"] in {"EMAIL", "PHONE", "DATE", "CURRENCY"} for candidate in candidates)
        if cardinality >= 0.95 and not has_specialized_format:
            add("IDENTIFIER", min(0.93, 0.65 + cardinality * 0.3), f"Distinctness is {cardinality:.1%} across non-missing values.")
        elif cardinality >= 0.6 and not has_specialized_format:
            shapes = sample.map(lambda value: re.sub(r"\d+", "#", value))
            shape_consistency = float(shapes.value_counts(normalize=True).iloc[0])
            has_letters_and_digits = float((sample.str.contains(r"[A-Za-z]") & sample.str.contains(r"\d")).mean())
            structure_rate = float(sample.str.match(IDENTIFIER_PATTERN).mean())
            if shape_consistency >= 0.75 and has_letters_and_digits >= 0.6 and structure_rate >= 0.75:
                add("IDENTIFIER", min(0.9, 0.65 + cardinality * 0.25), f"Distinctness is {cardinality:.1%}; values follow a consistent letter-and-digit identifier structure.")
        if not candidates:
            average_length = float(strings.str.len().mean())
            if distinct_count <= max(30, len(values) * 0.2):
                add("CATEGORY", 0.65, f"The column has {distinct_count} distinct values among {len(values)} observations.")
            elif average_length <= 40:
                add("TEXT", 0.55, f"Values are text with mean length {average_length:.1f} characters.")
            else:
                add("DESCRIPTION", 0.55, f"Values are longer text with mean length {average_length:.1f} characters.")

    if not candidates:
        add("UNKNOWN", 0.0, "Available values do not support a confident semantic classification.")

    if null_count:
        for candidate in candidates:
            candidate["evidence"].append(f"{null_count} values are missing.")
    return sorted(candidates, key=lambda item: item["confidence"], reverse=True)


def profile_frame(frame: pd.DataFrame) -> dict[str, Any]:
    row_count, column_count = frame.shape
    missing_total = int(frame.isna().sum().sum())
    column_profiles: list[dict[str, Any]] = []

    for name in frame.columns:
        series = frame[name]
        non_missing = series.dropna()
        null_count = int(series.isna().sum())
        distinct_count = int(series.nunique(dropna=True))
        profile: dict[str, Any] = {
            "name": str(name),
            "physical_type": str(series.dtype),
            "pyspark_type": _pyspark_type(series),
            "row_count": row_count,
            "null_count": null_count,
            "null_percentage": round(null_count / row_count * 100, 2) if row_count else 0.0,
            "distinct_count": distinct_count,
            "uniqueness_percentage": round(distinct_count / len(non_missing) * 100, 2) if len(non_missing) else 0.0,
            "duplicate_frequency": max(0, len(non_missing) - distinct_count),
            "semantic_candidates": _semantic_candidates(series, distinct_count, null_count),
            "top_values": [],
        }

        counts = series.value_counts(dropna=True).head(8)
        profile["top_values"] = [{"value": str(_safe_value(value)), "count": int(count)} for value, count in counts.items()]

        inferred_temporal = next((candidate for candidate in profile["semantic_candidates"] if candidate["type"] in {"DATE", "TIMESTAMP"} and candidate["confidence"] >= 0.8), None)
        if pd.api.types.is_numeric_dtype(series.dtype) and not pd.api.types.is_bool_dtype(series.dtype):
            numeric = pd.to_numeric(series, errors="coerce").dropna()
            profile["statistics"] = {
                "min": _safe_value(numeric.min()) if not numeric.empty else None,
                "max": _safe_value(numeric.max()) if not numeric.empty else None,
                "mean": _safe_value(numeric.mean()) if not numeric.empty else None,
                "median": _safe_value(numeric.median()) if not numeric.empty else None,
                "standard_deviation": _safe_value(numeric.std()) if len(numeric) > 1 else None,
                "quantiles": {str(q): _safe_value(numeric.quantile(q)) for q in (0.25, 0.5, 0.75)} if not numeric.empty else {},
            }
        elif pd.api.types.is_datetime64_any_dtype(series.dtype) or inferred_temporal:
            dates = pd.to_datetime(series, errors="coerce", format="mixed", utc=True).dropna()
            profile["statistics"] = {"min_date": _safe_value(dates.min()) if not dates.empty else None, "max_date": _safe_value(dates.max()) if not dates.empty else None}
        else:
            strings = non_missing.astype(str)
            lengths = strings.str.len()
            patterns = Counter(re.sub(r"[A-Za-z]+", "A", re.sub(r"\d+", "9", value)) for value in strings.head(2000))
            profile["statistics"] = {
                "minimum_length": int(lengths.min()) if not lengths.empty else None,
                "maximum_length": int(lengths.max()) if not lengths.empty else None,
                "mean_length": round(float(lengths.mean()), 2) if not lengths.empty else None,
                "common_patterns": [{"pattern": pattern[:64], "count": count} for pattern, count in patterns.most_common(5)],
            }
        column_profiles.append(profile)

    duplicate_rows = int(frame.duplicated().sum()) if column_count else 0
    return {
        "row_count": row_count,
        "column_count": column_count,
        "duplicate_row_count": duplicate_rows,
        "memory_bytes": int(frame.memory_usage(index=True, deep=True).sum()),
        "missing_values": {"count": missing_total, "percentage": round(missing_total / (row_count * column_count) * 100, 2) if row_count and column_count else 0.0},
        "overall_cardinality": round(sum(item["distinct_count"] for item in column_profiles) / max(row_count * column_count, 1), 4),
        "columns": column_profiles,
    }
