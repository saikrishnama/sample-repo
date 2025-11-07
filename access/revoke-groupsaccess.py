import pandas as pd
from databricks.sdk import WorkspaceClient

# Initialize Databricks workspace client
w = WorkspaceClient()

# Load Excel file and extract table names
excel_path = 'path_to_your_excel_file.xlsx'  # Replace with your actual file path
df = pd.read_excel(excel_path)

# Ensure the column 'table_name' exists
if 'table_name' not in df.columns:
    raise ValueError("Excel sheet must contain a 'table_name' column.")

# Catalog and schema details
catalog_name = 'prod'
schema_name = 'aw'

# Groups to revoke access from
groups_to_revoke = ['general', 'operation']
permissions_to_remove = ['SELECT', 'MODIFY', 'USAGE']

# Loop through each table and revoke access
for table in df['table_name'].dropna():
    full_table_name = f"{catalog_name}.{schema_name}.{table}"
    try:
        for group in groups_to_revoke:
            w.grants.update(
                full_table_name,
                changes=[
                    {
                        "principal": group,
                        "add": [],
                        "remove": permissions_to_remove
                    }
                ]
            )
            print(f"Revoked {permissions_to_remove} from group '{group}' on table: {full_table_name}")
    except Exception as e:
        print(f"Failed to revoke access for {full_table_name}: {e}")
