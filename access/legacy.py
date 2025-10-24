%pip install openpyxl pandas
from databricks.sdk import WorkspaceClient
from datetime import datetime, timezone
import pandas as pd

w = WorkspaceClient()

# -------- Helper: Pagination for Legacy Dashboards -------- #
def fetch_legacy_dashboards_paginated():
    """Fetch ALL legacy dashboards using next_page_token."""
    all_items = []
    next_page_token = None
    base_url = "/api/2.0/preview/sql/dashboards"

    while True:
        url = base_url
        if next_page_token:
            url += f"?page_token={next_page_token}"

        response = w.api_client.do("GET", url)
        dashboards = response.get("results", [])
        all_items.extend(dashboards)

        next_page_token = response.get("next_page_token")
        if not next_page_token:
            break

    return all_items

# -------- Legacy Dashboard Collector -------- #
def fetch_legacy_dashboards():
    try:
        dashboards = fetch_legacy_dashboards_paginated()
    except Exception as e:
        print(f"Error fetching legacy dashboards: {e}")
        return []

    legacy = []
    for db in dashboards:
        created_ms = db.get("created_at")
        updated_ms = db.get("updated_at")
        created_at = datetime.fromtimestamp(created_ms / 1000, tz=timezone.utc) if created_ms else None
        updated_at = datetime.fromtimestamp(updated_ms / 1000, tz=timezone.utc) if updated_ms else None

        legacy.append({
            "name": db.get("name"),
            "id": db.get("id"),
            "type": "legacy_sql",
            "owner_email": db.get("owner_email", "N/A"),
            "owner_name": db.get("owner_user_name", "N/A"),
            "create_time": created_at.isoformat() if created_at else "N/A",
            "update_time": updated_at.isoformat() if updated_at else "N/A",
            "lifecycle_state": "ACTIVE",
            "path": "N/A",
            "last_view": "N/A",
            "last_modified": updated_at.isoformat() if updated_at else "N/A",
            "created_by": db.get("created_by", "N/A"),
        })
    return legacy

# -------- Filter by User -------- #
def filter_dashboards_by_user(dashboards, users=None):
    """
    Filter dashboards by one or more users (by email or name).
    Args:
        dashboards (list): List of dashboards.
        users (list | str | None): List or comma-separated string of user emails/names.
    Returns:
        list: Filtered dashboards.
    """
    if not users:
        return dashboards

    # Normalize user input (list or string)
    if isinstance(users, str):
        users = [u.strip().lower() for u in users.split(",")]

    filtered = []
    for db in dashboards:
        owner_email = db.get("owner_email", "").lower()
        owner_name = db.get("owner_name", "").lower()
        if any(u in owner_email or u in owner_name for u in users):
            filtered.append(db)

    print(f"Filtered {len(filtered)} dashboards (out of {len(dashboards)}) for users: {', '.join(users)}")
    return filtered

# -------- Export to Excel -------- #
def export_legacy_dashboards_to_excel(user_filter=None):
    legacy = fetch_legacy_dashboards()
    legacy = filter_dashboards_by_user(legacy, user_filter)

    print(f"Total Legacy Dashboards Retrieved: {len(legacy)}")

    df = pd.DataFrame(legacy, columns=[
        "name", "id", "type", "owner_email", "owner_name",
        "create_time", "update_time", "lifecycle_state", "path",
        "last_view", "last_modified", "created_by"
    ])

    df.rename(columns={
        "name": "Dashboard Name",
        "id": "Dashboard ID",
        "type": "Dashboard Type",
        "owner_email": "Owner Email",
        "owner_name": "Owner Name",
        "create_time": "Create Time",
        "update_time": "Update Time",
        "lifecycle_state": "Lifecycle State",
        "path": "Path",
        "last_view": "Last View",
        "last_modified": "Last Modified",
        "created_by": "Created By"
    }, inplace=True)

    output_file = "legacy_dashboards_report.xlsx"
    df.to_excel(output_file, index=False)
    print(f"✅ Excel file created: {output_file}")

if __name__ == "__main__":
    # Example usage:
    # To export all dashboards → export_legacy_dashboards_to_excel()
    # To filter for one user → export_legacy_dashboards_to_excel("user@example.com")
    # To filter for multiple → export_legacy_dashboards_to_excel("user1@example.com, user2@example.com")
    export_legacy_dashboards_to_excel("user1@example.com, user2@example.com")
