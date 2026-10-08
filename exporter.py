"""Writes a saved mailing (run header plus its snapshot rows) to a mail-merge-ready CSV.

The file is rendered only from snapshot data, so writing it again later gives the same bytes.
"""
import csv
import os

EXPORT_HEADERS = [
    "First Name", "Middle Name", "Last Name", "Address 1", "Address 2",
    "City", "State", "Zip", "Church Name", "Type", "Quantity", "Fund", "Issue label", "Delivery",
]

PLAIN_DELIVERY = {"Print": "Mail", "Email": "Email", "Both": "Both"}


def csv_safe(value):
    """Prefix values that Excel/Sheets would treat as a formula, so they stay literal text. Write time only."""
    text = "" if value is None else str(value)
    if text[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text
    return text


def csv_line(run, r):
    return [
        csv_safe(r["first_name"]), csv_safe(r["middle_name"]), csv_safe(r["last_name"]),
        csv_safe(r["address1"]), csv_safe(r["address2"]), csv_safe(r["city"]), csv_safe(r["state"]),
        csv_safe(r["zip"]), csv_safe(r["church_name"]), csv_safe(r["recipient_type"]), r["quantity"],
        csv_safe(r["fund_name"]), csv_safe(run["issue_label"]), PLAIN_DELIVERY.get(r["delivery"], r["delivery"] or ""),
    ]


def write_run_csv(out_path, run, rows):
    """Saves via a temporary file so a failed save never leaves half a file behind."""
    tmp = f"{out_path}.part"
    try:
        with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(EXPORT_HEADERS)
            for r in rows:
                writer.writerow(csv_line(run, r))
        os.replace(tmp, out_path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    return len(rows)
