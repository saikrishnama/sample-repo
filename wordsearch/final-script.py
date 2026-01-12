import json
import argparse
import sys
import logging
from datetime import datetime, timezone
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.apps import App

# ----------------- LOGGER -----------------
def initialize_logger(name: str = __name__, level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        formatter = logging.Formatter(
            "[%(asctime)s] %(levelname)s - %(message)s", "%Y-%m-%d %H:%M:%S"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    logger.setLevel(level)
    return logger

logger = initialize_logger(__name__)

# ----------------- JSON LOADER -----------------
def load_config_json(config_file: str):
    try:
        with open(config_file, "r") as file:
            data = json.load(file)
            logger.info(f"Loaded config from {config_file}.")
            return data
    except FileNotFoundError:
        logger.warning(f"Config file not found: {config_file}. Using empty list.")
        return []
    except Exception as e:
        logger.error(f"Error loading {config_file}: {e}")
        return []

# ----------------- EXCEPTIONS -----------------
def check_expired_app_exception(exception_entry):
    if not isinstance(exception_entry, dict) or "app_name" not in exception_entry:
        return False, None

    app_name = exception_entry["app_name"]
    expiry_str = exception_entry.get("expiry", "")

    if not expiry_str:
        return False, app_name

    try:
        expiry_date = datetime.strptime(expiry_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) >= expiry_date, app_name
    except ValueError:
        logger.error(f"Invalid expiry date for {app_name}: {expiry_str}")
        return False, app_name

# ----------------- DATABRICKS APP ACTIONS -----------------
def get_all_apps(client: WorkspaceClient):
    try:
        apps = list(client.apps.list())
        logger.info(f"Fetched {len(apps)} apps.")
        return apps
    except Exception as e:
        logger.error(f"Error fetching apps: {e}")
        return []

def stop_app(client: WorkspaceClient, app: App, dry_run: bool):
    if dry_run:
        logger.info(f"DRY RUN: Would stop '{app.name}'")
        return
    client.apps.stop_and_wait(app.name)
    logger.info(f"Stopped '{app.name}'")

def start_app(client: WorkspaceClient, app: App, dry_run: bool):
    if dry_run:
        logger.info(f"DRY RUN: Would start '{app.name}'")
        return
    client.apps.start_and_wait(app.name)
    logger.info(f"Started '{app.name}'")

# ----------------- TABLE EXEMPTION LOGIC -----------------
def get_table_exempted_apps(client: WorkspaceClient):
    workspace_id = client.get_workspace_id()
    df = spark.table("app_monioring.deployed_apps.apps_monitor")

    rows = (
        df.filter(
            (df.present_tags["exempt"] == "true") &
            (df.workspace_id == workspace_id)
        )
        .select("app_name")
        .distinct()
        .collect()
    )

    apps = {r.app_name for r in rows}
    logger.info(f"Table exemptions: {', '.join(apps) if apps else 'None'}")
    return apps

# ----------------- CORE LOGIC -----------------
def manage_apps(
    client,
    app_exceptions,
    dry_run,
    start_all,
    stop_all,
):
    apps = get_all_apps(client)
    if not apps:
        return

    # -------- JSON EXEMPTIONS --------
    json_exemptions = set()
    for entry in app_exceptions:
        expired, app_name = check_expired_app_exception(entry)
        if app_name and not expired:
            json_exemptions.add(app_name)

    # -------- TABLE EXEMPTIONS --------
    table_exemptions = get_table_exempted_apps(client)

    logger.info(f"JSON exemptions: {', '.join(json_exemptions) if json_exemptions else 'None'}")
    logger.info(f"Table exemptions: {', '.join(table_exemptions) if table_exemptions else 'None'}")

    for app in apps:
        logger.info(f"\n--- Checking App: {app.name} ---")

        state = (
            str(app.compute_status.state).upper()
            if getattr(app, "compute_status", None)
            and getattr(app.compute_status, "state", None)
            else "UNKNOWN"
        )

        logger.info(f"State: {state}")

        # -------- START LOGIC --------
        if start_all and state == "COMPUTESTATE.STOPPED":
            logger.warning(f"Start-all enabled → starting '{app.name}'")
            start_app(client, app, dry_run)
            continue

        # -------- STOP LOGIC --------
        if state == "COMPUTESTATE.ACTIVE":

            if app.name in json_exemptions:
                logger.info(f"Skipping '{app.name}' → JSON exemption")
                continue

            if app.name in table_exemptions:
                logger.info(f"Skipping '{app.name}' → Table exemption")
                continue

            if stop_all or not start_all:
                logger.warning(
                    f"'{app.name}' not exempted → stopping"
                )
                stop_app(client, app, dry_run)
                continue

        logger.info(f"No action for '{app.name}'")

# ----------------- MAIN -----------------
def main():
    parser = argparse.ArgumentParser(description="Databricks App Start/Stop Manager")

    parser.add_argument("--start_all", type=lambda x: x.lower() == "true", default=True)
    parser.add_argument("--stop_all", type=lambda x: x.lower() == "true", default=False)
    parser.add_argument("--dry_run", type=lambda x: x.lower() == "true", default=False)

    parser.add_argument("-c", "--config_file_workspace", default="workspaces.json")
    parser.add_argument("-e", "--except_apps_file", default="apps_exception_list.json")
    parser.add_argument(
        "--databricks_secret_scope_name",
        default="databricsk-app-monitor-sp-secrets"
    )

    args, _ = parser.parse_known_args()

    if args.start_all and args.stop_all:
        raise ValueError("Use only one of --start_all or --stop_all")

    workspaces = load_config_json(args.config_file_workspace)
    app_exceptions = load_config_json(args.except_apps_file)

    from pyspark.dbutils import DBUtils
    import pyspark

    global spark
    spark = pyspark.sql.SparkSession.builder.getOrCreate()
    dbutils = DBUtils(spark)

    for ws in workspaces:
        logger.info(f"\n=== Workspace: {ws['name']} ===")

        secret = dbutils.secrets.get(
            scope=args.databricks_secret_scope_name,
            key=ws["application_id"],
        )

        client = WorkspaceClient(
            host=ws["endpoint"],
            client_id=ws["application_id"],
            client_secret=secret,
        )

        manage_apps(
            client=client,
            app_exceptions=app_exceptions,
            dry_run=args.dry_run,
            start_all=args.start_all,
            stop_all=args.stop_all,
        )

if __name__ == "__main__":
    main()
