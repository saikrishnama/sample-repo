%pip install openpyxl pandas
from databricks.sdk import WorkspaceClient
from datetime import datetime, timezone
import pandas as pd

w = WorkspaceClient()

# -------- Helper: Pagination -------- #
def fetch_paginated(url, results_key="dashboards"):
    """Fetch all pages using next_page_token."""
    all_items = []
    next_page_token = None

    while True:
        full_url = url
        if next_page_token:
            if "?" in url:
                full_url += f"&page_token={next_page_token}"
            else:
                full_url += f"?page_token={next_page_token}"

        response = w.api_client.do("GET", full_url)
        items = response.get(results_key) or []
        all_items.extend(items)

        next_page_token = response.get("next_page_token")
        if not next_page_token:
            break

    return all_items

# -------- Legacy SQL Dashboards -------- #
def fetch_legacy_dashboards():
    try:
        dashboards = fetch_paginated("/api/2.0/preview/sql/dashboards", results_key="results")
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

# -------- Lakeview Dashboards -------- #
def fetch_lakeview_dashboards():
    try:
        dashboards = fetch_paginated("/api/2.0/lakeview/dashboards", results_key="dashboards")
        print(f"Total Lakeview Dashboards Retrieved:",dashboards)
    except Exception as e:
        print(f"Error fetching Lakeview dashboards: {e}")
        return []

    lakeview = []
    for db in dashboards:
        modified_str = db.get("modified_time")
        last_modified = datetime.fromisoformat(modified_str.replace("Z", "+00:00")) if modified_str else None

        lakeview.append({
            "name": db.get("display_name"),
            "id": db.get("dashboard_id"),
            "type": "lakeview",
            "owner_email": db.get("creator_email", "N/A"),
            "owner_name": db.get("creator_name", "N/A"),
            "create_time": db.get("create_time", "N/A"),
            "update_time": db.get("update_time", "N/A"),
            "lifecycle_state": db.get("lifecycle_state", "N/A"),
            "path": db.get("workspace_path", "N/A"),
            "last_view": db.get("last_viewed_time", "N/A"),
            "last_modified": last_modified.isoformat() if last_modified else "N/A",
            "created_by": db.get("creator_name", "N/A"),
        })
    return lakeview

# -------- Combine & Export -------- #
def get_all_dashboards_to_excel():
    legacy = fetch_legacy_dashboards()
    lakeview = fetch_lakeview_dashboards()

    all_dashboards = legacy + lakeview
    print(f"Total Dashboards Retrieved: {len(all_dashboards)}")

    df = pd.DataFrame(all_dashboards, columns=[
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

    output_file = "dashboards_report.xlsx"
    df.to_excel(output_file, index=False)
    print(f"✅ Excel file created: {output_file}")

if __name__ == "__main__":
    get_all_dashboards_to_excel()
