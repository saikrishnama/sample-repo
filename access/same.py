#!/usr/bin/env python3
# %pip install pyyaml

import os
import sys
import yaml
import argparse
from typing import List, Dict, Any

# ---------------------------
# Helpers
# ---------------------------
def run_sql(sql: str, dry_run: bool):
    if dry_run:
        print(f"[DRY RUN] {sql}")
    else:
        print(f"[EXECUTING] {sql}")
        # In Databricks replace with:
        # spark.sql(sql)

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
# Core grant emission
# ---------------------------
def grant_catalog_basic(cfg: Dict[str, Any], dry: bool):
    """Backward-compat: USE_CATALOG: [cat1, cat2] -> grant USE CATALOG to SP/GROUPS."""
    for catalog in norm_list(cfg.get("USE_CATALOG", [])):
        for p in principals_from_config(cfg):
            run_sql(f"GRANT USE CATALOG ON CATALOG {catalog} TO `{p}`", dry)

def grant_schema_basic(cfg: Dict[str, Any], dry: bool):
    """Backward-compat: USE_SCHEMA: [schema, ...] -> grant USE SCHEMA to SP/GROUPS.
       Accepts either 'catalog.schema' or plain 'schema' (paired with USE_CATALOG)."""
    cats = norm_list(cfg.get("USE_CATALOG", []))
    for schema in norm_list(cfg.get("USE_SCHEMA", [])):
        if "." in schema:
            cat, sch = schema.split(".", 1)
            for p in principals_from_config(cfg):
                run_sql(f"GRANT USE SCHEMA ON SCHEMA {cat}.{sch} TO `{p}`", dry)
        else:
            # Pair each USE_SCHEMA with all USE_CATALOG entries
            for cat in cats:
                for p in principals_from_config(cfg):
                    run_sql(f"GRANT USE SCHEMA ON SCHEMA {cat}.{schema} TO `{p}`", dry)

def grant_catalog_advanced(cfg: Dict[str, Any], dry: bool):
    """
    Advanced: CATALOG_GRANTS:
      - catalog: production
        privilege: ALL PRIVILEGES   # default 'USE CATALOG'
        principals: [dbk-workflow, data_engineers]  # default SP+GROUPS
    """
    for entry in norm_list(cfg.get("CATALOG_GRANTS", [])):
        if isinstance(entry, str):
            # shorthand -> act like USE CATALOG to SP/GROUPS
            for p in principals_from_config(cfg):
                run_sql(f"GRANT USE CATALOG ON CATALOG {entry} TO `{p}`", dry)
        elif isinstance(entry, dict):
            catalog = entry["catalog"]
            priv = entry.get("privilege", "USE CATALOG")
            principals = entry.get("principals", principals_from_config(cfg))
            for p in principals:
                run_sql(f"GRANT {priv} ON CATALOG {catalog} TO `{p}`", dry)

def grant_schema_advanced(cfg: Dict[str, Any], dry: bool):
    """
    Advanced: SCHEMA_GRANTS:
      - schema: production.analytics
        privilege: ALL PRIVILEGES   # default 'USE SCHEMA'
        principals: [analyst_group]
    """
    for entry in norm_list(cfg.get("SCHEMA_GRANTS", [])):
        if isinstance(entry, str):
            # shorthand -> act like USE SCHEMA to SP/GROUPS
            if "." not in entry:
                # If only schema provided, pair with each USE_CATALOG
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
                # Pair with each USE_CATALOG when only schema given
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

def grant_role_mapped(cfg: Dict[str, Any], dry: bool):
    """
    Roles where entries look like:
      - 'catalog.schema.principal'  (schema-level grant)
      - 'catalog.schema'            (schema-level grant to SP/GROUPS)
      - 'principal'                 (catalog-level grant for every USE_CATALOG)
    """
    ROLE_PRIV_MAP = {
        "READER": "SELECT",
        "EDITOR": "MODIFY",
        "OWNER": "OWNERSHIP",
        "MAINTAINER": "ALL PRIVILEGES",
        "ALLPRIVILAGES": "ALL PRIVILEGES",  # common typo passthrough
    }
    for role, privilege in ROLE_PRIV_MAP.items():
        items = norm_list(cfg.get(role, []))
        for item in items:
            parts = item.split(".")
            if len(parts) == 3:
                catalog, schema, principal = parts
                run_sql(f"GRANT {privilege} ON SCHEMA {catalog}.{schema} TO `{principal}`", dry)
            elif len(parts) == 2:
                catalog, schema = parts
                for p in principals_from_config(cfg):
                    run_sql(f"GRANT {privilege} ON SCHEMA {catalog}.{schema} TO `{p}`", dry)
            elif len(parts) == 1:
                principal = parts[0]
                for cat in norm_list(cfg.get("USE_CATALOG", [])):
                    run_sql(f"GRANT {privilege} ON CATALOG {cat} TO `{principal}`", dry)

def grant_wildcard(cfg: Dict[str, Any], dry: bool):
    """
    WILD_CARD_READER supports two formats:

    1) Back-compat (string): "catalog.schema.table_pattern"
       -> grants SELECT to all SP/GROUPS

    2) Advanced (dict):
       - pattern: catalog.schema.table_pattern
         privilege: SELECT | MODIFY | ALL PRIVILEGES   (default SELECT)
         principals: [group_or_sp...]                  (default SP+GROUPS)
    """
    for entry in norm_list(cfg.get("WILD_CARD_READER", [])):
        if isinstance(entry, str):
            # Back-compat string
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

def process_yaml_file(path: str, dry_run: bool, only_files: List[str] = None):
    if only_files and os.path.basename(path) not in only_files:
        return
    with open(path, "r") as f:
        cfg = yaml.safe_load(f) or {}

    print(f"\n📂 Processing file: {path}")

    # Backward-compat simple grants:
    grant_catalog_basic(cfg, dry_run)
    grant_schema_basic(cfg, dry_run)

    # Advanced custom-privilege grants:
    grant_catalog_advanced(cfg, dry_run)
    grant_schema_advanced(cfg, dry_run)

    # Role-mapped grants (READER/EDITOR/OWNER/MAINTAINER/ALLPRIVILAGES):
    grant_role_mapped(cfg, dry_run)

    # Wildcard table grants with principals + privileges:
    grant_wildcard(cfg, dry_run)

# ---------------------------
# CLI
# ---------------------------
def main():
    parser = argparse.ArgumentParser(description="Databricks grant generator (dry-run friendly)")
    parser.add_argument("--grants-folder", default="Grants", help="Folder with YAML files")
    parser.add_argument("--dry-run", action="store_true", help="Print SQL only")
    parser.add_argument("--files", nargs="*", help="Specific YAML filenames to process (basename)")
    args = parser.parse_args()

    ensure_folder_exists(args.grants_folder)
    all_yaml = list_yaml_files(args.grants_folder)

    files_to_run = all_yaml if not args.files else [f for f in all_yaml if f in args.files]
    if args.files and not files_to_run:
        print("⚠️ No matching YAML files to run.")
        sys.exit(0)

    for fname in files_to_run:
        process_yaml_file(os.path.join(args.grants_folder, fname), args.dry_run, None)

    print("\n✅ Completed" + (" (Dry Run)" if args.dry_run else ""))

if __name__ == "__main__":
    main()
#########################
yaml
########
SP:
  - dbk-workflow
GROUPS:
  - analyst_group
  - data_engineers

# Simple (back-compat): grants USE CATALOG to SP + GROUPS
USE_CATALOG:
  - development
  - production

# Simple (back-compat): grants USE SCHEMA to SP + GROUPS (paired with USE_CATALOG)
USE_SCHEMA:
  - sales
  - hr

# Advanced catalog-level with custom privileges
CATALOG_GRANTS:
  - catalog: development
    privilege: ALL PRIVILEGES
    principals: [dbk-workflow]
  - production   # shorthand -> acts like USE CATALOG to SP + GROUPS

# Advanced schema-level with custom privileges
SCHEMA_GRANTS:
  - schema: production.finance
    privilege: ALL PRIVILEGES
    principals:
      - data_engineers
  - schema: development.analytics  # shorthand -> acts like USE SCHEMA to SP + GROUPS

# Role-mapped classic entries
READER:
  - development.sales.analyst
  - production.hr.data_engineer

EDITOR:
  - development.hr.data_scientist

OWNER:
  - dbk-workflow  # ownership at catalog level for each USE_CATALOG

# Wildcard tables (support custom privilege & principals)
WILD_CARD_READER:
  - pattern: development.operations.demo_%
    privilege: SELECT
    principals: [dbk-workflow, analyst_group]
  - pattern: production.reporting.sales_%
    privilege: MODIFY
    principals: [data_engineers]
##### Optional
SP:
  - reporting-service
GROUPS: []

USE_CATALOG:
  - staging

SCHEMA_GRANTS:
  - schema: staging.analytics
    privilege: OWNERSHIP
    principals: [reporting-service]

WILD_CARD_READER:
  - pattern: staging.analytics.tmp_%
    privilege: SELECT
    principals: [reporting-service]
##### 
Action yaml 
name: Databricks Grants Check

on:
  pull_request:
    paths:
      - 'Grants/**/*.yml'
      - 'Grants/**/*.yaml'
  workflow_dispatch:
    inputs:
      mode:
        description: "Run mode: full | modified"
        required: false
        default: "modified"

jobs:
  grants:
    runs-on: ubuntu-latest

    steps:
      - name: Checkout repo
        uses: actions/checkout@v3

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.10'

      - name: Install dependencies
        run: pip install pyyaml

      - name: Determine files to check
        id: files
        run: |
          if [ "${{ github.event_name }}" = "workflow_dispatch" ]; then
            if [ "${{ github.event.inputs.mode }}" = "full" ]; then
              echo "mode=full" >> $GITHUB_OUTPUT
            else
              FILES=$(git diff --name-only origin/${{ github.ref_name }} -- Grants/ | grep -E '\.ya?ml$' || true)
              if [ -n "$FILES" ]; then
                echo "mode=changed" >> $GITHUB_OUTPUT
                echo "files=$FILES" >> $GITHUB_OUTPUT
              else
                echo "mode=none" >> $GITHUB_OUTPUT
              fi
            fi
          else
            FILES=$(git diff --name-only origin/${{ github.base_ref }} -- Grants/ | grep -E '\.ya?ml$' || true)
            if [ -n "$FILES" ]; then
              echo "mode=changed" >> $GITHUB_OUTPUT
              echo "files=$FILES" >> $GITHUB_OUTPUT
            else
              echo "mode=none" >> $GITHUB_OUTPUT
            fi
          fi

      - name: Run Grants Runner (Dry Run)
        if: steps.files.outputs.mode != 'none'
        run: |
          if [ "${{ steps.files.outputs.mode }}" = "full" ]; then
            python grants_runner.py --grants-folder Grants --dry-run
          elif [ "${{ steps.files.outputs.mode }}" = "changed" ]; then
            python grants_runner.py --grants-folder Grants --dry-run --files ${{ steps.files.outputs.files }}
          fi
