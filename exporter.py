"""Writes a newsletter's active mailing list out to a mail-merge-ready CSV."""
import csv

import db

EXPORT_COLUMNS = [
    "first_name", "middle_name", "last_name", "address1", "address2",
    "city", "state", "zip", "church_name", "recipient_type", "quantity",
]

EXPORT_HEADERS = [
    "First Name", "Middle Name", "Last Name", "Address 1", "Address 2",
    "City", "State", "Zip", "Church Name", "Type", "Quantity",
]


def _csv_safe(value):
    """Prefix values that Excel/Sheets would treat as a formula, so they stay literal text."""
    text = str(value)
    if text[:1] in ("=", "+", "-", "@"):
        return "'" + text
    return text


def export_newsletter_csv(conn, newsletter_id, out_path):
    rows = db.list_subscribers(conn, newsletter_id)
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(EXPORT_HEADERS)
        count = 0
        for r in rows:
            if not r["sub_active"]:
                continue
            writer.writerow([
                _csv_safe(r["first_name"] or ""), _csv_safe(r["middle_name"] or ""), _csv_safe(r["last_name"] or ""),
                _csv_safe(r["address1"] or ""), _csv_safe(r["address2"] or ""), _csv_safe(r["city"] or ""),
                _csv_safe(r["state"] or ""), _csv_safe(r["zip"] or ""), _csv_safe(r["church_name"] or ""),
                _csv_safe(r["recipient_type"] or ""), r["quantity"],
            ])
            count += 1
    return count
