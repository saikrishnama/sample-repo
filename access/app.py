#!/usr/bin/env python3
# %pip install pyyaml

import os
import yaml
import argparse
from collections import defaultdict

# --- CLI arguments ---
parser = argparse.ArgumentParser(description="Apply Databricks grants from YAML files")
parser.add_argument("--grants-folder", default="Grants", help="Folder containing YAML grant files")
parser.add_argument("--dry-run", action="store_true", help="Print SQL statements without executing")
args = parser.parse_args()

GRANTS_FOLDER = args.grants_folder
DRY_RUN = args.dry_run

# --- Role → Privilege mapping ---
ROLE_PRIV_MAP = {
    "READER": "SELECT",
    "EDITOR": "MODIFY",
    "OWNER": "OWNERSHIP",
    "MAINTAINER": "ALL PRIVILEGES",
    "ALLPRIVILAGES": "ALL PRIVILEGES",  # typo in YAML handled
    "WILD_CARD_READER": "SELECT",
}

# --- Summary collector ---
summary = defaultdict(list)

def run_sql(sql, principal, privilege, obj):
    if DRY_RUN:
        print(f"[DRY RUN] {sql}")
    else:
        print(f"[EXECUTING] {sql}")
        # Uncomment inside Databricks notebook
        # spark.sql(sql)
    summary[principal].append((privilege, obj))

def process_yaml_file(filepath):
    print(f"\n📂 Processing file: {filepath}")
    with open(filepath, "r") as f:
        config = yaml.safe_load(f)

    # --- USE_CATALOG ---
    if "USE_CATALOG" in config:
        for catalog in config["USE_CATALOG"]:
            for principal in config.get("SP", []):
                sql = f"GRANT USE CATALOG ON CATALOG {catalog} TO `{principal}`"
                run_sql(sql, principal, "USE CATALOG", catalog)

    # --- USE_SCHEMA ---
    if "USE_SCHEMA" in config:
        for schema in config["USE_SCHEMA"]:
            for principal in config.get("SP", []):
                for catalog in config.get("USE_CATALOG", []):
                    sql = f"GRANT USE SCHEMA ON SCHEMA {catalog}.{schema} TO `{principal}`"
                    run_sql(sql, principal, "USE SCHEMA", f"{catalog}.{schema}")

    # --- Handle all role → privilege mappings ---
    for role, privilege in ROLE_PRIV_MAP.items():
        if role in config and config[role]:
            for item in config[role]:
                if role == "WILD_CARD_READER":
                    catalog, schema, table_pattern = item.split(".", 2)
                    sql = f"GRANT {privilege} ON TABLE {catalog}.{schema}.{table_pattern} TO `public`"
                    run_sql(sql, "public", privilege, f"{catalog}.{schema}.{table_pattern}")
                else:
                    parts = item.split(".")
                    if len(parts) == 3:
                        catalog, schema, principal = parts
                        sql = f"GRANT {privilege} ON SCHEMA {catalog}.{schema} TO `{principal}`"
                        run_sql(sql, principal, privilege, f"{catalog}.{schema}")
                    elif len(parts) == 2:
                        catalog, schema = parts
                        sql = f"GRANT {privilege} ON SCHEMA {catalog}.{schema} TO `public`"
                        run_sql(sql, "public", privilege, f"{catalog}.{schema}")
                    elif len(parts) == 1:
                        principal = parts[0]
                        for catalog in config.get("USE_CATALOG", []):
                            sql = f"GRANT {privilege} ON CATALOG {catalog} TO `{principal}`"
                            run_sql(sql, principal, privilege, catalog)

# --- Main loop ---
if not os.path.exists(GRANTS_FOLDER):
    print(f"❌ Grants folder '{GRANTS_FOLDER}' not found.")
else:
    yaml_files = [f for f in os.listdir(GRANTS_FOLDER) if f.endswith((".yml", ".yaml"))]
    if not yaml_files:
        print("⚠️ No YAML files found in Grants folder.")
    else:
        for filename in yaml_files:
            process_yaml_file(os.path.join(GRANTS_FOLDER, filename))

# --- Summary Report ---
print("\n📊 SUMMARY REPORT")
for principal, grants in summary.items():
    print(f"\n👤 Principal: {principal}")
    for privilege, obj in grants:
        print(f"   - {privilege} ON {obj}")

print("\n✅ Completed" + (" (Dry Run)" if DRY_RUN else " (Applied)"))
