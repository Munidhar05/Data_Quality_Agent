const checkCatalog = [
  {
    "name": "Structural Quality",
    "checks": [
      {
        "number": 1,
        "name": "Schema Validation",
        "description": "Validate configured schema and required fields"
      },
      {
        "number": 2,
        "name": "Data Type Validation",
        "description": "Validate values against a configured data type"
      }
    ]
  },
  {
    "name": "Completeness & Uniqueness",
    "checks": [
      {
        "number": 3,
        "name": "NULL Validation",
        "description": "Find missing or blank values in required columns"
      },
      {
        "number": 4,
        "name": "Completeness Check",
        "description": "Find records with missing values in selected fields"
      },
      {
        "number": 5,
        "name": "Duplicate Check",
        "description": "Find identical duplicate records"
      },
      {
        "number": 6,
        "name": "Uniqueness Check",
        "description": "Validate a column or composite key is unique"
      }
    ]
  },
  {
    "name": "Validity & Integrity",
    "checks": [
      {
        "number": 7,
        "name": "Range Validation",
        "description": "Validate numeric minimum and maximum limits"
      },
      {
        "number": 8,
        "name": "Domain Validation",
        "description": "Validate permitted values"
      },
      {
        "number": 9,
        "name": "Format / Pattern Validation",
        "description": "Validate a configured regular expression"
      },
      {
        "number": 10,
        "name": "Referential Integrity",
        "description": "Validate foreign keys against an uploaded reference file"
      }
    ]
  },
  {
    "name": "Accuracy & Consistency",
    "checks": [
      {
        "number": 11,
        "name": "Accuracy Check",
        "description": "Compare values with authoritative reference records using configured keys"
      },
      {
        "number": 12,
        "name": "Consistency Check",
        "description": "Compare two columns using an approved operator"
      }
    ]
  },
  {
    "name": "Freshness & Volume",
    "checks": [
      {
        "number": 13,
        "name": "Data Freshness Check",
        "description": "Validate timestamps against an explicit freshness limit"
      },
      {
        "number": 14,
        "name": "Row Count Validation",
        "description": "Validate the expected number of records"
      },
      {
        "number": 15,
        "name": "Volume Anomaly Detection",
        "description": "Compare record count with a configured historical baseline"
      }
    ]
  },
  {
    "name": "Statistical Quality",
    "checks": [
      {
        "number": 16,
        "name": "Statistical Outlier Detection",
        "description": "Identify outliers with the configured IQR multiplier"
      },
      {
        "number": 17,
        "name": "Data Distribution Drift",
        "description": "Compare numeric distributions using configured bins and an uploaded baseline"
      }
    ]
  },
  {
    "name": "Reconciliation & Business Rules",
    "checks": [
      {
        "number": 18,
        "name": "Source-to-Target Reconciliation",
        "description": "Reconcile keys, values and record counts with an uploaded source or target"
      },
      {
        "number": 19,
        "name": "Business Rule Validation",
        "description": "Validate a conditional business comparison"
      }
    ]
  }
];
