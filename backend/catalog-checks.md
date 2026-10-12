# Data quality checks: 19 tests in 7 categories

Configure expectations for your file. Reference and distribution checks require an uploaded reference file. Dataset-level failures reject every record.

## Structural Quality

### 1. Schema Validation

Validate configured schema and required fields

```json
{
  "schema": {
    "id": "LongType"
  },
  "exact": true
}
```

### 2. Data Type Validation

Validate values against a configured data type

```json
{
  "column": "id",
  "type": "LongType"
}
```

## Completeness & Uniqueness

### 3. NULL Validation

Find missing or blank values in required columns

```json
{
  "columns": [
    "id"
  ]
}
```

### 4. Completeness Check

Find records with missing values in selected fields

```json
{
  "columns": [
    "id",
    "name"
  ]
}
```

### 5. Duplicate Check

Find identical duplicate records

```json
{}
```

### 6. Uniqueness Check

Validate a column or composite key is unique

```json
{
  "columns": [
    "id",
    "date"
  ]
}
```

## Validity & Integrity

### 7. Range Validation

Validate numeric minimum and maximum limits

```json
{
  "column": "amount",
  "minimum": 0,
  "maximum": 100
}
```

### 8. Domain Validation

Validate permitted values

```json
{
  "column": "status",
  "values": [
    "active",
    "inactive"
  ]
}
```

### 9. Format / Pattern Validation

Validate a configured regular expression

```json
{
  "column": "code",
  "pattern": "^[A-Z]{2}[0-9]{3}$"
}
```

### 10. Referential Integrity

Validate foreign keys against an uploaded reference file

```json
{
  "column": "parent_id",
  "reference_column": "id",
  "reference_dataset_id": "UPLOAD_REFERENCE_FILE"
}
```

## Accuracy & Consistency

### 11. Accuracy Check

Compare values with authoritative reference records using configured keys

```json
{
  "keys": [
    "id"
  ],
  "columns": [
    "amount"
  ],
  "reference_dataset_id": "UPLOAD_REFERENCE_FILE",
  "tolerance": 0
}
```

### 12. Consistency Check

Compare two columns using an approved operator

```json
{
  "left": "start",
  "operator": "<=",
  "right": "end"
}
```

## Freshness & Volume

### 13. Data Freshness Check

Validate timestamps against an explicit freshness limit

```json
{
  "column": "updated_at",
  "maximum_hours": 24,
  "now": "2026-10-09T00:00:00Z"
}
```

### 14. Row Count Validation

Validate the expected number of records

```json
{
  "expected": 100
}
```

### 15. Volume Anomaly Detection

Compare record count with a configured historical baseline

```json
{
  "baseline_counts": [
    100,
    110,
    105
  ],
  "maximum_deviation_percent": 20
}
```

## Statistical Quality

### 16. Statistical Outlier Detection

Identify outliers with the configured IQR multiplier

```json
{
  "column": "amount",
  "iqr_multiplier": 1.5
}
```

### 17. Data Distribution Drift

Compare numeric distributions using configured bins and an uploaded baseline

```json
{
  "column": "amount",
  "reference_column": "amount",
  "reference_dataset_id": "UPLOAD_REFERENCE_FILE",
  "bins": [
    0,
    10,
    20,
    100
  ],
  "maximum_distance": 0.2
}
```

## Reconciliation & Business Rules

### 18. Source-to-Target Reconciliation

Reconcile keys, values and record counts with an uploaded source or target

```json
{
  "keys": [
    "id"
  ],
  "columns": [
    "amount"
  ],
  "reference_dataset_id": "UPLOAD_REFERENCE_FILE",
  "tolerance": 0
}
```

### 19. Business Rule Validation

Validate a conditional business comparison

```json
{
  "when_column": "status",
  "when_values": [
    "active"
  ],
  "column": "amount",
  "operator": ">",
  "value": 0
}
```
