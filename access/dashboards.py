%pip install openpyxl pandas 
from databricks.sdk import WorkspaceClient
from datetime import datetime, timezone
import pandas as pd

# Initialize Databricks workspace client
w = WorkspaceClient()

# -------- Legacy SQL Dashboards -------- #
def fetch_legacy_dashboards():
    try:
        response = w.api_client.do("GET", "/api/2.0/preview/sql/dashboards")
        dashboards = response.get("results", [])
        
    except Exception as e:
        print(f"Error fetching legacy dashboards: {e}")
        return []

    legacy = []
    for db in dashboards:
        last_modified_ms = db.get("updated_at")
        last_modified = (
            datetime.fromtimestamp(last_modified_ms / 1000, tz=timezone.utc)
            if last_modified_ms else None
        )

        legacy.append({
            "name": db.get("name"),
            "id": db.get("id"),
            "type": "legacy_sql",
            "owner_email": db.get("owner_email", "N/A"),
            "owner_name": db.get("owner_user_name", "N/A"),
            "last_view": "N/A",  # Not available in legacy API
            "last_modified": last_modified.isoformat() if last_modified else "N/A",
            "created_by": db.get("created_by", "N/A"),
        })
    return legacy

# -------- Lakeview Dashboards -------- #
def fetch_lakeview_dashboards():
    try:
        response = w.api_client.do("GET", "/api/2.0/lakeview/dashboards")
        print(response)
        dashboards = response.get("dashboards", [])
    except Exception as e:
        print(f"Error fetching Lakeview dashboards: {e}")
        return []

    lakeview = []
    for db in dashboards:
        modified_str = db.get("modified_time")
        last_modified = (
            datetime.fromisoformat(modified_str.replace("Z", "+00:00"))
            if modified_str else None
        )

        lakeview.append({
            "name": db.get("display_name"),
            "id": db.get("dashboard_id"),
            "type": "lakeview",
            "owner_email": db.get("creator_email", "N/A"),
            "owner_name": db.get("creator_name", "N/A"),
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

    # Convert to DataFrame
    df = pd.DataFrame(all_dashboards, columns=[
        "name", "id", "type", "owner_email", "owner_name",
        "last_view", "last_modified", "created_by"
    ])

    # Rename columns to desired Excel headers
    df.rename(columns={
        "name": "Dashboard Name",
        "id": "Dashboard ID",
        "type": "Dashboard Type",
        "owner_email": "Owner Email",
        "owner_name": "Owner Name",
        "last_view": "Last View",
        "last_modified": "Last Modified",
        "created_by": "Created By"
    }, inplace=True)

    # Save to Excel
    output_file = "dashboards_report.xlsx"
    df.to_excel(output_file, index=False)
    print(f"✅ Excel file created: {output_file}")

if __name__ == "__main__":
    get_all_dashboards_to_excel()
