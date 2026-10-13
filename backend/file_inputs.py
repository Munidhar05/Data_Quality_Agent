"""File-only imports normalized to the existing CSV validation pipeline."""
import csv
import io
import json
from pathlib import Path

import pandas as pd
from defusedxml import ElementTree

SUPPORTED_SUFFIXES = {".csv", ".parquet", ".pq", ".tsv", ".txt", ".psv", ".xlsx", ".xls", ".xlsm", ".ods", ".json", ".jsonl", ".ndjson", ".xml", ".feather", ".arrow", ".orc", ".avro"}


def record_text(value):
    # Preserve integer precision before pandas fills missing fields with floats.
    if isinstance(value, dict):
        return {key: record_text(item) for key, item in value.items()}
    if isinstance(value, list) or value is None:
        return value
    return str(value)


def convert_file(filename, content):
    suffix = Path(filename).suffix.lower()
    notes = []
    source = io.BytesIO(content)
    if suffix in {".tsv", ".psv", ".txt"}:
        text = content.decode("utf-8-sig")
        delimiter = {".tsv": "\t", ".psv": "|"}.get(suffix)
        if delimiter is None:
            delimiter = csv.Sniffer().sniff(text[:65536], delimiters=",;\t|").delimiter
        frame = pd.read_csv(io.StringIO(text), sep=delimiter, dtype=str, keep_default_na=False)
    elif suffix in {".xlsx", ".xls", ".xlsm", ".ods"}:
        with pd.ExcelFile(source) as book:
            frame = pd.read_excel(book, sheet_name=0, dtype=str, keep_default_na=False)
            notes.append("Imported worksheet: " + book.sheet_names[0] + ". Only the first worksheet is imported; export other sheets separately.")
    elif suffix in {".json", ".jsonl", ".ndjson"}:
        text = content.decode("utf-8-sig")
        records = [json.loads(line) for line in text.splitlines() if line.strip()] if suffix != ".json" else json.loads(text)
        if isinstance(records, dict):
            if len(records) == 1 and isinstance(next(iter(records.values())), list):
                records = next(iter(records.values()))
            else:
                records = [records]
        if not isinstance(records, list) or not all(isinstance(row, dict) for row in records):
            raise ValueError("JSON must contain records as objects or an array of objects.")
        frame = pd.json_normalize([record_text(record) for record in records])
        notes.append("Nested object fields use dotted column names; array values remain JSON text.")
    elif suffix == ".xml":
        root = ElementTree.fromstring(content)
        records = list(root)
        if not records or any(not list(record) for record in records):
            raise ValueError("XML must contain repeating records with simple child fields.")
        rows = []
        for record in records:
            row = dict(record.attrib)
            for field in record:
                name = field.tag.split("}")[-1]
                if list(field) or name in row:
                    raise ValueError("XML needs unique, flat fields in each record. Export nested XML as JSON or CSV.")
                row[name] = field.text or ""
            rows.append(row)
        frame = pd.DataFrame(rows)
    elif suffix in {".feather", ".arrow"}:
        frame = pd.read_feather(source)
    elif suffix == ".orc":
        frame = pd.read_orc(source)
    elif suffix == ".avro":
        from fastavro import reader
        frame = pd.json_normalize([record_text(record) for record in reader(source)])
    else:
        raise ValueError("Unsupported file type.")
    if frame.empty or not len(frame.columns):
        raise ValueError("The file contains no tabular records.")
    frame.columns = [str(column) for column in frame.columns]
    if len(set(frame.columns)) != len(frame.columns):
        raise ValueError("Column names must be unique.")
    for column in frame.columns:
        frame[column] = frame[column].map(lambda value: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value)
    return frame.to_csv(index=False).encode("utf-8"), notes
