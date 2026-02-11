import json
import argparse
import sys
import logging
from datetime import datetime, timezone
from databricks.sdk import WorkspaceClient

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
            logger.info(f"Loaded {config_file}")
            return data
    except Exception:
        logger.warning(f"Could not load {config_file}")
        return {}

# ----------------- RESOLVE EXCEPTIONS -----------------
def resolve_workspace_exceptions(all_exceptions, workspace_name):
    if not isinstance(all_exceptions, dict):
        return []

    exceptions = all_exceptions.get(workspace_name, [])
    logger.info(
        f"Loaded {len(exceptions)} exceptions for workspace '{workspace_name}'"
    )
    return exceptions

# ----------------- EXCEPTIONS -----------------
def check_expired_app_exception(entry):
    if not isinstance(entry, dict) or "app_name" not in entry:
        return False, None

    app = entry["app_name"]
    expiry = entry.get("expiry", "")

    if not expiry:
        return False, app

    try:
        exp = datetime.strptime(expiry, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) >= exp, app
    except:
        return False, app

# ----------------- APP ACTIONS -----------------
def get_all_apps(client):
    try:
        apps = list(client.apps.list())
        logger.info(f"Found {len(apps)} apps")
        return apps
    except Exception as e:
        logger.error(f"App list failed: {e}")
        return []

def stop_app(client, app, dry_run):
    if dry_run:
        logger.info(f"DRY RUN → Stop {app.name}")
        return
    client.apps.stop_and_wait(app.name)
    logger.info(f"Stopped {app.name}")

def start_app(client, app, dry_run):
    if dry_run:
        logger.info(f"DRY RUN → Start {app.name}")
        return
    client.apps.start_and_wait(app.name)
    logger.info(f"Started {app.name}")

# ----------------- CORE LOGIC -----------------
def manage_apps(client, app_exceptions, dry_run, start_all, stop_all):
    apps = get_all_apps(client)

    local_exempt = set()
    for e in app_exceptions:
        expired, name = check_expired_app_exception(e)
        if name and not expired:
            local_exempt.add(name)

    logger.info(f"Effective exemptions: {', '.join(local_exempt) if local_exempt else 'None'}")

    for app in apps:
        state = (
            str(app.compute_status.state).upper()
            if app.compute_status and app.compute_status.state else "UNKNOWN"
        )

        logger.info(f"{app.name} → {state}")

        if start_all and state == "COMPUTESTATE.STOPPED":
            start_app(client, app, dry_run)
            continue

        if app.name in local_exempt:
            logger.info(f"Exempt → skipping {app.name}")
            continue

        if stop_all and state == "COMPUTESTATE.ACTIVE":
            stop_app(client, app, dry_run)

# ----------------- MAIN -----------------
def main():
    parser = argparse.ArgumentParser()

    parser.add_argument("--start_all", type=lambda x: x.lower() == "true", default=False)
    parser.add_argument("--stop_all", type=lambda x: x.lower() == "true", default=False)
    parser.add_argument("--dry_run", type=lambda x: x.lower() == "true", default=True)

    parser.add_argument("-c", "--config_file_workspace", default="workspaces.json")
    parser.add_argument("-e", "--except_apps_file", default="apps_exception_list.json")
    parser.add_argument("--databricks_secret_scope_name", default="databricsk-app-monitor-sp-secrets")

    args, _ = parser.parse_known_args()

    workspaces = load_config_json(args.config_file_workspace)
    all_app_exceptions = load_config_json(args.except_apps_file)

    from pyspark.dbutils import DBUtils
    import pyspark

    global spark
    spark = pyspark.sql.SparkSession.builder.getOrCreate()
    dbutils = DBUtils(spark)

    for ws in workspaces:
        logger.info(f"\n===== {ws['name']} =====")

        workspace_exceptions = resolve_workspace_exceptions(
            all_app_exceptions,
            ws["name"]
        )

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
            app_exceptions=workspace_exceptions,
            dry_run=args.dry_run,
            start_all=args.start_all,
            stop_all=args.stop_all,
        )

if __name__ == "__main__":
    main()
