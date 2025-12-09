import json
import argparse
import sys
import logging
from datetime import datetime, timezone
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.apps import App


# ----------------- LOGGER -----------------
def initialize_logger(name: str = __name__, level: int = logging.INFO) -> logging.Logger:
    """Initialize logger with consistent formatting."""
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
    """Load a JSON configuration file safely."""
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
    """Check if an exception entry has expired."""
    if not isinstance(exception_entry, dict) or "app_name" not in exception_entry:
        logger.warning(f"Invalid exception entry: {exception_entry}")
        return False, None

    app_name = exception_entry["app_name"]
    expiry_str = exception_entry.get("expiry", "")

    if not expiry_str:
        return False, app_name  # Never expires

    try:
        expiry_date = datetime.strptime(expiry_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) >= expiry_date, app_name
    except ValueError:
        logger.error(f"Invalid expiry date for {app_name}: {expiry_str}")
        return False, app_name


# ----------------- DATABRICKS APP ACTIONS -----------------
def get_all_apps(client: WorkspaceClient):
    """Fetch all apps in the workspace."""
    try:
        apps = list(client.apps.list())
        logger.info(f"Fetched {len(apps)} apps.")
        return apps
    except Exception as e:
        logger.error(f"Error fetching apps: {e}")
        return []


def stop_expired_app(client: WorkspaceClient, app: App, dry_run: bool):
    """Stop a Databricks app."""
    if dry_run:
        logger.info(f"DRY RUN: Would stop app '{app.name}'.")
        return
    try:
        client.apps.stop_and_wait(app.name)
        logger.info(f"Stopped app '{app.name}'.")
    except Exception as e:
        logger.error(f"Error stopping '{app.name}': {e}")


def delete_expired_app(client: WorkspaceClient, app: App, dry_run: bool):
    """Delete a Databricks app."""
    if dry_run:
        logger.info(f"DRY RUN: Would delete app '{app.name}'.")
        return
    try:
        client.apps.delete(app.name)
        logger.info(f"Deleted app '{app.name}'.")
    except Exception as e:
        logger.error(f"Error deleting '{app.name}': {e}")


# ----------------- CORE LOGIC -----------------
def manage_apps(client,
                max_stop_age,
                max_app_age,
                app_exceptions,
                dry_run=True,
                auto_stop=False):
    """Core logic for managing Databricks app lifecycle."""
    apps = get_all_apps(client)
    if not apps:
        logger.warning("No apps found.")
        return

    active_exceptions = {}
    for entry in app_exceptions:
        expired, app_name = check_expired_app_exception(entry)
        if app_name and not expired:
            active_exceptions[app_name] = True

    logger.info(f"Active exceptions: {', '.join(active_exceptions.keys()) if active_exceptions else 'None'}")

    for app in apps:
        logger.info(f"\n--- Checking App: {app.name} ---")

        state = (
            str(app.compute_status.state).upper()
            if getattr(app, "compute_status", None) and getattr(app.compute_status, "state", None)
            else "UNKNOWN"
        )

        def parse_time(t):
            for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
                try:
                    return datetime.strptime(t, fmt).replace(tzinfo=timezone.utc)
                except Exception:
                    continue
            raise ValueError(f"Unrecognized datetime format: {t}")

        try:
            create_time = parse_time(app.create_time)
            update_time = parse_time(app.update_time)
        except Exception as e:
            logger.error(f"Error parsing timestamps for {app.name}: {e}")
            continue

        app_age = (datetime.now(timezone.utc) - create_time).days
        update_age = (datetime.now(timezone.utc) - update_time).days

        logger.info(f"State: {state}, Created: {app_age}d, Updated: {update_age}d")

        if auto_stop and app.name not in active_exceptions and state == "ACTIVE":
            logger.warning(f"Auto-stop enabled. '{app.name}' not in exception list — stopping.")
            stop_expired_app(client, app, dry_run)
            continue

        if state == "ACTIVE" and update_age >= max_stop_age:
            logger.warning(f"'{app.name}' inactive {update_age}d — stopping (threshold: {max_stop_age}d).")
            stop_expired_app(client, app, dry_run)
            continue

        if state == "STOPPED" and update_age >= max_app_age:
            logger.warning(f"'{app.name}' stopped {update_age}d — deleting (threshold: {max_app_age}d).")
            delete_expired_app(client, app, dry_run)
            continue

        logger.info(f"No action for '{app.name}'.")


# ----------------- MAIN -----------------
def main():
    parser = argparse.ArgumentParser(description="Manage old Databricks Apps")

    parser.add_argument("-a", "--max_app_age", type=int, default=7,
                        help="Delete stopped apps older than this (days). Default: 7")

    parser.add_argument("-s", "--max_age_before_stop", type=int, default=3,
                        help="Stop active apps older than this (days). Default: 3")

    parser.add_argument("--dry_run",
                        type=lambda x: x.lower() == "true",
                        default=True,
                        help="Simulate actions (True, default) or perform them (False).")

    parser.add_argument("--auto_stop",
                        type=lambda x: x.lower() == "true",
                        default=False,
                        help="Enable auto-stop for apps not in the exception list. Default: False")

    parser.add_argument("-c", "--config_file_workspace",
                        type=str, default="workspaces.json",
                        help="Workspace configuration JSON file. Default: workspaces.json")

    parser.add_argument("-e", "--except_apps_file",
                        type=str, default="apps_exception_list.json",
                        help="Exceptions JSON file. Default: apps_exception_list.json")

    parser.add_argument("--databricks_secret_scope_name",
                        type=str, default="databricks-apps",
                        help="Secret scope name. Default: databricks-apps")

    args, unknown = parser.parse_known_args()
    if unknown:
        logger.debug(f"Ignoring unknown args: {unknown}")

    client_configs = load_config_json(args.config_file_workspace)
    app_exceptions = load_config_json(args.except_apps_file)

    # Try importing dbutils (Databricks-only)
    try:
        from pyspark.dbutils import DBUtils
        import pyspark
        spark = pyspark.sql.SparkSession.builder.getOrCreate()
        local_dbutils = DBUtils(spark)
    except Exception:
        logger.warning("dbutils not available; ensure secrets are fetched by other means.")
        local_dbutils = None

    for config in client_configs:
        logger.info(f"\n=== Workspace: {config['name']} ===")

        secret = ""
        if local_dbutils:
            try:
                secret = local_dbutils.secrets.get(
                    scope=args.databricks_secret_scope_name,
                    key=config["application_id"],
                )
            except Exception as e:
                logger.error(f"Error retrieving secret for {config['name']}: {e}")

        ws_client = WorkspaceClient(
            host=config["endpoint"],
            client_id=config["application_id"],
            client_secret=secret,
        )

        try:
            current_user = ws_client.current_user.me()
            logger.info(f"Running as: {current_user.display_name}")
        except Exception as e:
            logger.error(f"Error fetching current user: {e}")

        logger.info(f"Dry run: {args.dry_run}, Auto-stop: {args.auto_stop}")

        manage_apps(
            ws_client,
            args.max_age_before_stop,
            args.max_app_age,
            app_exceptions,
            args.dry_run,
            args.auto_stop,
        )


if __name__ == "__main__":
    main()
