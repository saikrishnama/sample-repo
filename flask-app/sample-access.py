import os
import yaml
from databricks import sql

# --- Databricks SQL connection ---
connection = sql.connect(
    server_hostname="<DATABRICKS_HOST>",
    http_path="<SQL_WAREHOUSE_PATH>",
    access_token="<DATABRICKS_PERSONAL_ACCESS_TOKEN>"
)
cursor = connection.cursor()

# --- Folder containing YAML files ---
yaml_folder = "/path/to/yaml/files"

def normalize_key(key: str) -> str:
    """Normalize YAML keys for consistent access."""
    return key.strip().lower().replace(" ", "_").replace("-", "_")

def safe_list(value):
    """Ensure the value is always a list."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if v is not None]
    return [str(value).strip()]

def run_grant(statement: str):
    """Execute a GRANT statement and handle errors."""
    try:
        print(f"Executing: {statement}")
        cursor.execute(statement)
    except Exception as e:
        print(f"⚠️ Failed: {statement} → {e}")

# --- Process all YAML files ---
for filename in os.listdir(yaml_folder):
    if filename.lower().endswith((".yaml", ".yml")):
        print(f"\n📄 Processing file: {filename}")
        try:
            with open(os.path.join(yaml_folder, filename), "r") as file:
                raw_data = yaml.safe_load(file) or {}
        except Exception as e:
            print(f"❌ Error reading {filename}: {e}")
            continue

        # Normalize all keys in the YAML
        data = {normalize_key(k): v for k, v in raw_data.items()}

        # Extract service principal(s)
        sp_names = safe_list(data.get("service_principal_name"))

        # Access details
        access_type = data.get("access_type", {})
        if isinstance(access_type, dict):
            access_type = {normalize_key(k): v for k, v in access_type.items()}
        else:
            print("⚠️ No valid access_type found, skipping...")
            continue

        # --- Catalog access ---
        for catalog in safe_list(access_type.get("use_catalog")):
            for sp in sp_names:
                run_grant(f"GRANT USAGE ON CATALOG `{catalog}` TO `{sp}`")

        # --- Schema access ---
        for schema in safe_list(access_type.get("schema")):
            for sp in sp_names:
                run_grant(f"GRANT USAGE ON SCHEMA `{schema}` TO `{sp}`")

        # --- Table read-only access ---
        for table in safe_list(access_type.get("select_access_tables")):
            for sp in sp_names:
                run_grant(f"GRANT SELECT ON TABLE `{table}` TO `{sp}`")

        # --- Table read-write access ---
        for table in safe_list(access_type.get("read_write")):
            for sp in sp_names:
                run_grant(f"GRANT ALL PRIVILEGES ON TABLE `{table}` TO `{sp}`")

cursor.close()
connection.close()
