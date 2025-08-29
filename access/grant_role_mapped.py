#!/usr/bin/env python3
# %pip install pyyaml databricks-sql-connector

import os
import sys
import yaml
import argparse
from typing import List, Dict, Any
from databricks import sql

# ---------------------------
# Databricks SQL execution
# ---------------------------
def run_sql(sql_text: str, dry_run: bool):
    if dry_run:
        print(f"[DRY RUN] {sql_text}")
    else:
        print(f"[EXECUTING] {sql_text}")
        # Connect to Databricks SQL Warehouse
        with sql.connect(
            server_hostname=os.environ.get("DATABRICKS_HOST"),
            http_path=os.environ.get("DATABRICKS_HTTP_PATH"),
            access_token=os.environ.get("DATABRICKS_TOKEN")
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql_text)

# ---------------------------
# Helpers
# ---------------------------
def principals_from_config(cfg: Dict[str, Any]) -> List[str]:
    return list(cfg.get("SP", [])) + list(cfg.get("GROUPS", []))

def norm_list(x):
    return x if isinstance(x, list) else []

def ensure_folder_exists(folder: str):
    if not os.path.exists(folder):
        print(f"❌ Grants folder '{folder}' not found.")
        sys.exit(1)

def list_yaml_files(folder: str) -> List[str]:
    files = [f for f in os.listdir(folder) if f.endswith((".yml", ".yaml"))]
    if not files:
        print(f"⚠️ No YAML files found in '{folder}'.")
        sys.exit(1)
    return files

# ---------------------------
# Grant Functions
# ---------------------------
def grant_catalog_basic(cfg: Dict[str, Any], dry: bool):
    for catalog in norm_list(cfg.get("USE_CATALOG", [])):
        for p in principals_from_config(cfg):
            run_sql(f"GRANT USE CATALOG ON CATALOG {catalog} TO `{p}`", dry)

def grant_schema_basic(cfg: Dict[str, Any], dry: bool):
    cats = norm_list(cfg.get("USE_CATALOG", []))
    for schema in norm_list(cfg.get("USE_SCHEMA", [])):
        for cat in cats:
            for p in principals_from_config(cfg):
                run_sql(f"GRANT USE SCHEMA ON SCHEMA {cat}.{schema} TO `{p}`", dry)

def grant_catalog_advanced(cfg: Dict[str, Any], dry: bool):
    for entry in norm_list(cfg.get("CATALOG_GRANTS", [])):
        if isinstance(entry, str):
            for p in principals_from_config(cfg):
                run_sql(f"GRANT USE CATALOG ON CATALOG {entry} TO `{p}`", dry)
        elif isinstance(entry, dict):
            catalog = entry["catalog"]
            priv = entry.get("privilege", "USE CATALOG")
            principals = entry.get("principals", principals_from_config(cfg))
            for p in principals:
                run_sql(f"GRANT {priv} ON CATALOG {catalog} TO `{p}`", dry)

def grant_schema_advanced(cfg: Dict[str, Any], dry: bool):
    for entry in norm_list(cfg.get("SCHEMA_GRANTS", [])):
        if isinstance(entry, str):
            if "." not in entry:
                for cat in norm_list(cfg.get("USE_CATALOG", [])):
                    for p in principals_from_config(cfg):
                        run_sql(f"GRANT USE SCHEMA ON SCHEMA {cat}.{entry} TO `{p}`", dry)
            else:
                cat, sch = entry.split(".", 1)
                for p in principals_from_config(cfg):
                    run_sql(f"GRANT USE SCHEMA ON SCHEMA {cat}.{sch} TO `{p}`", dry)
        elif isinstance(entry, dict):
            schema = entry["schema"]
            if "." not in schema:
                for cat in norm_list(cfg.get("USE_CATALOG", [])):
                    priv = entry.get("privilege", "USE SCHEMA")
                    principals = entry.get("principals", principals_from_config(cfg))
                    for p in principals:
                        run_sql(f"GRANT {priv} ON SCHEMA {cat}.{schema} TO `{p}`", dry)
            else:
                cat, sch = schema.split(".", 1)
                priv = entry.get("privilege", "USE SCHEMA")
                principals = entry.get("principals", principals_from_config(cfg))
                for p in principals:
                    run_sql(f"GRANT {priv} ON SCHEMA {cat}.{sch} TO `{p}`", dry)

# Table/View-level grants
def grant_role_mapped(cfg: Dict[str, Any], dry: bool):
    ROLE_PRIV_MAP = {
        "READER": "SELECT",
        "EDITOR": "MODIFY",
        "OWNER": "OWNERSHIP",
        "MAINTAINER": "ALL PRIVILEGES",
        "ALLPRIVILEGES": "ALL PRIVILEGES",
    }

    for role, privilege in ROLE_PRIV_MAP.items():
        items = norm_list(cfg.get(role, []))
        for item in items:
            parts = item.split(".")
            if len(parts) < 3:
                continue  # schema/catalog-level handled separately

            catalog = parts[0]
            table_or_view = parts[-1]
            schema = ".".join(parts[1:-1])

            for p in principals_from_config(cfg):
                run_sql(f"GRANT {privilege} ON TABLE {catalog}.{schema}.{table_or_view} TO `{p}`", dry)

# Wildcard grants
def grant_wildcard(cfg: Dict[str, Any], dry: bool):
    for entry in norm_list(cfg.get("WILD_CARD_READER", [])):
        if isinstance(entry, str):
            cat, sch, patt = entry.split(".", 2)
            for p in principals_from_config(cfg):
                run_sql(f"GRANT SELECT ON TABLE {cat}.{sch}.{patt} TO `{p}`", dry)
        elif isinstance(entry, dict):
            pattern = entry["pattern"]
            cat, sch, patt = pattern.split(".", 2)
            privilege = entry.get("privilege", "SELECT")
            principals = entry.get("principals", principals_from_config(cfg))
            for p in principals:
                run_sql(f"GRANT {privilege} ON TABLE {cat}.{sch}.{patt} TO `{p}`", dry)

# ---------------------------
# Process YAML
# ---------------------------
def process_yaml_file(path: str, dry_run: bool, only_files: List[str] = None):
    if only_files and os.path.basename(path) not in only_files:
        return
    with open(path, "r") as f:
        cfg = yaml.safe_load(f) or {}

    print(f"\n📂 Processing file: {path}")

    grant_catalog_basic(cfg, dry_run)
    grant_schema_basic(cfg, dry_run)
    grant_catalog_advanced(cfg, dry_run)
    grant_schema_advanced(cfg, dry_run)
    grant_role_mapped(cfg, dry_run)
    grant_wildcard(cfg, dry_run)

# ---------------------------
# CLI
# ---------------------------
def main():
    parser = argparse.ArgumentParser(description="Databricks grant generator")
    parser.add_argument("--grants-folder", default="Grants", help="Folder with YAML files")
    parser.add_argument("--dry-run", action="store_true", help="Print SQL only")
    parser.add_argument("--files", nargs="*", help="Specific YAML filenames")
    parser.add_argument("--full-run", action="store_true", help="Process all YAML files")
    parser.add_argument("--modified-files", action="store_true", help="Process only modified YAML files")
    args = parser.parse_args()

    ensure_folder_exists(args.grants_folder)
    all_yaml = list_yaml_files(args.grants_folder)

    files_to_run = []

    if args.full_run:
        files_to_run = all_yaml
    elif args.modified_files:
        result = os.popen("git diff --name-only origin/main -- Grants/").read()
        modified = [f.strip() for f in result.splitlines() if f.strip().endswith((".yml", ".yaml"))]
        files_to_run = [f for f in all_yaml if f in modified]
        if not files_to_run:
            print("⚠️ No modified YAML files found.")
            sys.exit(0)
    elif args.files:
        files_to_run = [f for f in all_yaml if f in args.files]
        if not files_to_run:
            print("⚠️ No matching YAML files to run.")
            sys.exit(0)
    else:
        files_to_run = all_yaml

    for fname in files_to_run:
        process_yaml_file(os.path.join(args.grants_folder, fname), args.dry_run)

    print("\n✅ Completed" + (" (Dry Run)" if args.dry_run else ""))

if __name__ == "__main__":
    main()
