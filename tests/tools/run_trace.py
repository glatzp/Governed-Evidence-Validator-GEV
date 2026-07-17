# DEV TOOL — BYPASSES GOVERNED SYSTEM
# DO NOT USE FOR GOVERNED EXECUTION

import pandas as pd
import json
from main import run_validation_pipeline
import os

source_file = r"C:\Users\glatz\OneDrive\Documents\Job_Salary.csv"
output_file = r"C:\Users\glatz\Desktop\Output Validator\ai_output_flawed.csv"

user_spec = {
  "task_type": "filter",
  "source": {
    "file_name": "job_salary_test.csv",
    "primary_key": "Job_ID"
  },
  "parameters": {
    "filter": {
      "column": "salary",
      "operator": ">",
      "value": 150000,
      "value_type": "numeric"
    }
  },
  "comparison": {
    "column_normalization": "lower_trim",
    "strict_schema": True,
    "preview_required": True
  },
  "version": "1.0"
}

mapped_spec = {
    "task_type": "filter",
    "primary_key": user_spec["source"]["primary_key"],
    "filter": user_spec["parameters"]["filter"]
}

df = pd.read_csv(source_file)
df.columns = [c.lower().strip() for c in df.columns]

expected_df = df[pd.to_numeric(df["salary"]) > 150000].copy()

flawed_df = expected_df.copy()
flawed_df = flawed_df[flawed_df["job_id"] != 12]
row_2 = df[df["job_id"] == 2]
flawed_df = pd.concat([flawed_df, row_2])
flawed_df.loc[flawed_df["job_id"] == 4, "salary"] = 200000
flawed_df.to_csv(output_file, index=False)

final_report = run_validation_pipeline(source_file, output_file, mapped_spec, preview_confirmed=True)

print(json.dumps(final_report, indent=2))
