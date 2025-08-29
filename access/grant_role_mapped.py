def grant_role_mapped(cfg: Dict[str, Any], dry: bool):
    """
    Correctly map READER/EDITOR/OWNER grants:
    - Automatically grants USE SCHEMA on all schemas referenced
    - catalog.schema.table_or_view  -> TABLE-level
    - catalog.schema                -> SCHEMA-level
    - principal                     -> CATALOG-level
    """
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
            principals = principals_from_config(cfg)

            if len(parts) == 1:
                # Catalog-level grant
                for p in principals:
                    run_sql(f"GRANT {privilege} ON CATALOG {item} TO `{p}`", dry)

            elif len(parts) == 2:
                # Schema-level grant
                catalog, schema = parts
                for p in principals:
                    # Grant schema-level privilege
                    run_sql(f"GRANT {privilege} ON SCHEMA {catalog}.{schema} TO `{p}`", dry)

            else:
                # Table/view-level grant
                catalog = parts[0]
                table_or_view = parts[-1]
                schema = ".".join(parts[1:-1])

                for p in principals:
                    # Grant schema access first
                    run_sql(f"GRANT USE SCHEMA ON SCHEMA {catalog}.{schema} TO `{p}`", dry)
                    # Then table/view-level privilege
                    run_sql(f"GRANT {privilege} ON TABLE {catalog}.{schema}.{table_or_view} TO `{p}`", dry)
