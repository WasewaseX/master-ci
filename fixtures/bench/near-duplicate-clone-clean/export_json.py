"""JSON export: shapes the payload differently."""

def export_json(rows):
    payload = {"items": [], "count": 0}
    for row in rows:
        if not row.get("active"):
            continue
        payload["items"].append(
            {"id": row["id"], "meta": {"name": row["name"], "sum": row["total"]}}
        )
        payload["count"] += 1
    return payload
