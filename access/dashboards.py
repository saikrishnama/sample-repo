from databricks.sdk import WorkspaceClient
from datetime import datetime, timedelta, timezone

# ---------------- CONFIG ---------------- #
DAYS_THRESHOLD = 30
# ---------------------------------------- #

# WorkspaceClient will auto-pick up from DATABRICKS_HOST and DATABRICKS_TOKEN
# or use: WorkspaceClient(host="...", token="...")
w = WorkspaceClient()

def get_unused_dashboards(days_threshold=DAYS_THRESHOLD):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_threshold)
    unused = []

    # The dashboards API is under SQL (preview)
    dashboards = w.api_client.do(
        "GET", "/api/2.0/preview/sql/dashboards"
    )["results"]
    print(f"Found {len(dashboards)} dashboards")
    for db in dashboards:
        print(f"Checking dashboard {db['id']}")
        last_modified_ms = db.get("updated_at")
        if last_modified_ms:
            last_modified = datetime.fromtimestamp(last_modified_ms / 1000, tz=timezone.utc)
            if last_modified < cutoff:
                unused.append({
                    "id": db["id"],
                    "name": db["name"],
                    "last_modified": last_modified.isoformat()
                })
    return unused


if __name__ == "__main__":
    dashboards = get_unused_dashboards()
    print(f"Dashboards not modified in {DAYS_THRESHOLD}+ days: {len(dashboards)}")
    for u in dashboards:
        print(f"- {u['name']} (ID: {u['id']}), Last Modified: {u['last_modified']}")
