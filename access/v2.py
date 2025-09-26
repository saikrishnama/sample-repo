from databricks.sdk import WorkspaceClient
from datetime import datetime, timedelta, timezone

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

def get_unused_dashboards(days_threshold=DAYS_THRESHOLD):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_threshold)
    unused = []

    dashboards = w.api_client.do(
        "GET", "/api/2.0/preview/sql/dashboards"
    )["results"]

    for db in dashboards:
        last_modified = parse_timestamp(db.get("updated_at"))
        if last_modified and last_modified < cutoff:
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
