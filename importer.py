"""Reads CSV/XLSX mailing-list files and maps them onto the people table's columns.

Handles the two header styles already in use:
  - Master/CLP style: First Name, Middle Name, Last Name, Address 1, Address 2, City,
    State, Zip, Verified, Home Phone, Cell Phone, Email, Church Name, Conference, Title, Source
  - Our Health style: Name, PrimaryAddressLine1, PrimaryAddressLine2, PrimaryCity,
    PrimaryState, PrimaryZip
"""
import csv
import io
import os
import re

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
    "fund": ["fund"],
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


SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "2nd", "3rd"}


def read_csv_rows(path):
    with open(path, "rb") as f:
        raw = f.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")  # Excel's "CSV" on Windows
    reader = csv.DictReader(io.StringIO(text, newline=""))
    headers = reader.fieldnames or []
    return headers, [row for row in reader]


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


def normalize_zip(val):
    """Five digits (zero-padded), or ZIP+4 as 12345-6789. Anything else is kept as typed."""
    if val is None or (isinstance(val, float) and val != val):
        return None
    text = str(int(val)) if isinstance(val, (int, float)) else str(val).strip()
    digits = re.sub(r"\D", "", text)
    if re.fullmatch(r"\d{1,5}", text):
        return text.zfill(5)
    if len(digits) == 9 and re.fullmatch(r"\d{5}[- ]?\d{4}|\d{9}", text):
        return f"{digits[:5]}-{digits[5:]}"
    return text


def _is_suffix(token):
    return token.strip(".").lower() in SUFFIXES


def split_name(raw):
    """'First Middle Last', 'Last, First', and a trailing Jr/Sr/III go to first, middle and last name."""
    text = " ".join(str(raw).split())
    if not text:
        return {}
    suffix = ""
    if "," in text:
        left, right = text.split(",", 1)
        left, right = left.split(), right.replace(",", " ").split()
        if right and _is_suffix(right[-1]) and len(right) > 1:
            suffix = right.pop()
        if left and _is_suffix(left[-1]) and len(left) > 1:
            suffix = left.pop()
        if right:
            first, middle, last = right[0], " ".join(right[1:]), " ".join(left)
        else:
            first, middle, last = "", "", " ".join(left)
    else:
        parts = text.split()
        if len(parts) > 1 and _is_suffix(parts[-1]):
            suffix = parts.pop()
        first = parts[0]
        last = parts[-1] if len(parts) > 1 else ""
        middle = " ".join(parts[1:-1])
    if suffix and last:
        last = f"{last} {suffix}"
    out = {"first_name": first, "middle_name": middle, "last_name": last}
    return {k: v for k, v in out.items() if v}


def map_row(row, mapping):
    """Converts one raw row (dict keyed by original header) into a people-table dict."""
    out = {}
    for field, header in mapping.items():
        if field == "full_name":
            continue
        val = row.get(header)
        if field == "zip":
            out[field] = normalize_zip(val)
        else:
            out[field] = str(val).strip() if val is not None else None

    if "full_name" in mapping and not out.get("first_name") and not out.get("last_name"):
        raw = row.get(mapping["full_name"])
        if raw:
            out.update(split_name(raw))

    # Normalize blank strings to None
    for k, v in list(out.items()):
        if isinstance(v, str) and v.strip() == "":
            out[k] = None
        if isinstance(v, float) and v != v:  # NaN
            out[k] = None
    return out
