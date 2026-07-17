import pandas as pd
import json
# DEV TOOL — BYPASSES GOVERNED SYSTEM
# DO NOT USE FOR GOVERNED EXECUTION

import os
from main import run_validation_pipeline
import tempfile

def write_temp_csv(df):
    fd, path = tempfile.mkstemp(suffix=".csv")
    os.close(fd)
    df.to_csv(path, index=False)
    return path

test_spec = {
    "task_type": "filter",
    "primary_key": "Job_ID",
    "filter": {
        "column": "salary",
        "operator": ">",
        "value": 150000,
        "value_type": "numeric"
    }
}

# 1. Missing Source Column Test
source_df = pd.DataFrame({
    "Job_ID": [1, 2],
    # Missing 'salary'
})
src_path = write_temp_csv(source_df)
out_path = write_temp_csv(source_df)

report_source = run_validation_pipeline(src_path, out_path, test_spec, preview_confirmed=True)
os.unlink(src_path); os.unlink(out_path)

print("### 1. missing_columns")
print(json.dumps(report_source, indent=2))

# 2. Missing AI Output Column Test
source_df_valid = pd.DataFrame({
    "Job_ID": [1, 2],
    "salary": [100000, 200000]
})
out_df_invalid = pd.DataFrame({
    "Job_ID": [2]
    # Missing 'salary'
})
src_path_2 = write_temp_csv(source_df_valid)
out_path_2 = write_temp_csv(out_df_invalid)

report_output = run_validation_pipeline(src_path_2, out_path_2, test_spec, preview_confirmed=True)
os.unlink(src_path_2); os.unlink(out_path_2)

print("\n### 2. missing_output_columns")
print(json.dumps(report_output, indent=2))
