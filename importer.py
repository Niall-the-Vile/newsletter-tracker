"""Reads CSV/XLSX mailing-list files and maps them onto the people table's columns.

Handles the two header styles already in use:
  - Master/CLP style: First Name, Middle Name, Last Name, Address 1, Address 2, City,
    State, Zip, Verified, Home Phone, Cell Phone, Email, Church Name, Conference, Title, Source
  - Our Health style: Name, PrimaryAddressLine1, PrimaryAddressLine2, PrimaryCity,
    PrimaryState, PrimaryZip
"""
import csv
import os

FIELD_ALIASES = {
    "first_name": ["first name", "first", "firstname"],
    "middle_name": ["middle name", "middle"],
    "last_name": ["last name", "last", "lastname"],
    "full_name": ["name", "full name"],
    "address1": ["address 1", "address1", "primaryaddressline1", "street"],
    "address2": ["address 2", "address2", "primaryaddressline2"],
    "city": ["city", "primarycity"],
    "state": ["state", "primarystate"],
    "zip": ["zip", "zip code", "primaryzip", "postal code"],
    "home_phone": ["home phone", "phone"],
    "cell_phone": ["cell phone", "mobile phone", "mobile"],
    "email": ["email", "e-mail"],
    "church_name": ["church name", "church"],
    "conference": ["conference"],
    "title": ["title"],
    "verified": ["verified"],
    "source": ["source"],
}


def _normalize(header):
    return str(header).strip().lower()


def build_mapping(headers):
    """Returns {our_field: header_name} for headers that matched a known alias."""
    normalized = {_normalize(h): h for h in headers if h is not None}
    mapping = {}
    for field, aliases in FIELD_ALIASES.items():
        for alias in aliases:
            if alias in normalized:
                mapping[field] = normalized[alias]
                break
    return mapping


def read_csv_rows(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        rows = [row for row in reader]
    return headers, rows


def read_xlsx_rows(path):
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.worksheets[0]
    rows_iter = ws.iter_rows(values_only=True)
    headers = list(next(rows_iter))
    rows = []
    for values in rows_iter:
        row = {headers[i]: values[i] for i in range(len(headers)) if i < len(values)}
        rows.append(row)
    wb.close()
    return headers, rows


def load_file(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".csv":
        return read_csv_rows(path)
    elif ext in (".xlsx", ".xlsm"):
        return read_xlsx_rows(path)
    else:
        raise ValueError(f"Unsupported file type: {ext}")


def map_row(row, mapping):
    """Converts one raw row (dict keyed by original header) into a people-table dict."""
    out = {}
    for field, header in mapping.items():
        if field == "full_name":
            continue
        val = row.get(header)
        if field == "zip" and isinstance(val, (int, float)) and val == val:
            out[field] = str(int(val)).zfill(5)
        else:
            out[field] = str(val).strip() if val is not None else None

    if "full_name" in mapping and not out.get("first_name") and not out.get("last_name"):
        raw = row.get(mapping["full_name"])
        if raw:
            parts = str(raw).strip().split()
            if len(parts) == 1:
                out["first_name"] = parts[0]
            elif len(parts) == 2:
                out["first_name"], out["last_name"] = parts
            elif len(parts) > 2:
                out["first_name"] = parts[0]
                out["middle_name"] = " ".join(parts[1:-1])
                out["last_name"] = parts[-1]

    # Normalize blank strings to None
    for k, v in list(out.items()):
        if isinstance(v, str) and v.strip() == "":
            out[k] = None
        if isinstance(v, float) and v != v:  # NaN
            out[k] = None
    return out
