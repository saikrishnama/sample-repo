from databricks.sdk import WorkspaceClient
from datetime import datetime, timedelta, timezone
from tabulate import tabulate  # pip install tabulate

# ---------------- CONFIG ---------------- #
DAYS_THRESHOLD = 30
# ---------------------------------------- #

w = WorkspaceClient()

def parse_timestamp(ts_value):
    """Handle both int(ms) and string timestamps."""
    if ts_value is None:
        return None
    if isinstance(ts_value, (int, float)):
        # Milliseconds since epoch
        return datetime.fromtimestamp(ts_value / 1000, tz=timezone.utc)
    if isinstance(ts_value, str):
        try:
            # Parse ISO 8601 string
            return datetime.fromisoformat(ts_value.replace("Z", "+00:00"))
        except Exception:
            return None
    return None

def get_last_view_times():
    """
    Returns a dict {dashboard_id: last_viewed_datetime}
    based on query history.
    """
    last_view = {}
    cutoff = datetime.now(timezone.utc) - timedelta(days=DAYS_THRESHOLD)

    payload = {"max_results": 1000}  # adjust if needed
    response = w.api_client.do("GET", "/api/2.0/sql/history/queries", data=payload)

    for q in response.get("res", []):
        if q.get("query_source") == "dashboard":
            db_id = q.get("dashboard_id")
            end_time = parse_timestamp(q.get("end_time_ms") or q.get("end_time"))
            if db_id and end_time:
                if db_id not in last_view or end_time > last_view[db_id]:
                    last_view[db_id] = end_time
    return last_view

def get_unused_dashboards(days_threshold=DAYS_THRESHOLD):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_threshold)
    unused = []

    dashboards = w.api_client.do("GET", "/api/2.0/preview/sql/dashboards").get("results", [])
    last_viewed_map = get_last_view_times()

    for db in dashboards:
        db_id = db.get("id")
        last_modified = parse_timestamp(db.get("updated_at"))
        last_viewed = last_viewed_map.get(db_id)

        # Get last modified user
        last_modified_user = db.get("user", {}).get("display_name", "Unknown")

        # Decide "unused": no views OR last view < cutoff
        if not last_viewed or last_viewed < cutoff:
            unused.append({
                "id": db_id,
                "name": db.get("name"),
                "owner": db.get("user", {}).get("display_name", "Unknown"),
                "last_modified": last_modified.strftime("%Y-%m-%d %H:%M:%S UTC") if last_modified else "Unknown",
                "last_viewed": last_viewed.strftime("%Y-%m-%d %H:%M:%S UTC") if last_viewed else "Never",
                "last_modified_user": last_modified_user
            })
    return unused


if __name__ == "__main__":
    dashboards = get_unused_dashboards()
    print(f"\nDashboards not viewed in {DAYS_THRESHOLD}+ days: {len(dashboards)}\n")

    if dashboards:
        print(tabulate(
            dashboards,
            headers={
                "id": "ID",
                "name": "Dashboard Name",
                "owner": "Owner",
                "last_modified": "Last Modified",
                "last_viewed": "Last Viewed",
                "last_modified_user": "Last Modified User"
            },
            tablefmt="grid"
        ))
    else:
        print("✅ No unused dashboards found.")
