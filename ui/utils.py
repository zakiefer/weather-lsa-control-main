from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any


def to_csv(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ""
    # collect columns
    cols: list[str] = []
    for r in rows:
        for k in r.keys():
            if k not in cols:
                cols.append(k)
    import io

    buf = io.StringIO()
    buf.write(",".join(cols) + "\n")
    for r in rows:
        values = []
        for c in cols:
            v = r.get(c)
            if v is None:
                values.append("")
            else:
                s = str(v)
                if any(ch in s for ch in [",", "\n", '"']):
                    s = '"' + s.replace('"', '""') + '"'
                values.append(s)
        buf.write(",".join(values) + "\n")
    return buf.getvalue()


def project(row: dict[str, Any], cols: list[str]) -> dict[str, Any]:
    return {c: row.get(c) for c in cols}


# Heuristics to convert snake_case or camelCase to human-friendly titles
def _humanize_key(key: str) -> str:
    if not key:
        return key
    k = str(key)
    # Common special cases
    specials = {
        "id": "ID",
        "cap_id": "CAP",
        "capId": "CAP",
        "lsa": "LSA",
        "gaql": "GAQL",
    }
    if k in specials:
        return specials[k]
    # Insert spaces before capitals (camelCase -> camel Case)
    k = re.sub(r"(?<!^)(?=[A-Z])", " ", k)
    # Replace underscores and dashes with spaces
    k = k.replace("_", " ").replace("-", " ")
    # Normalize multiple spaces
    k = re.sub(r"\s+", " ", k).strip()
    # Title case but preserve common acronyms
    title = k.title()
    title = title.replace("Gaql", "GAQL").replace("Lsa", "LSA")
    # Nicer verbs/nouns (collapse common *_at fields)
    # Handle both beginning-of-string and mid-string occurrences.
    title = title.replace("Created At", "Created")
    title = title.replace("Issued At", "Issued")
    title = title.replace("Effective At", "Effective")
    return title


def prettify_headers(
    rows: Iterable[dict[str, Any]] | None,
    mapping: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """
    Return a new list of rows with human-friendly column titles.

    - Uses a provided mapping of old->new header names when supplied.
    - Falls back to heuristics for snake_case and camelCase.
    - Only changes keys for display; values are left intact.
    """
    if not rows:
        return []
    out: list[dict[str, Any]] = []
    mapping = mapping or {}
    for r in rows:
        new: dict[str, Any] = {}
        for k, v in r.items():
            pretty = mapping.get(k) or _humanize_key(k)
            new[pretty] = v
        out.append(new)
    return out


# Common renames used across pages (can be extended per page)
DEFAULT_HISTORY_MAP = {
    "issued_at": "Issued",
    "effective_at": "Effective",
    "areas": "Areas",
    "source": "Source",
    "severity": "Severity",
    "hash": "Hash",
    "cap_id": "CAP",
    "id": "ID",
}

DEFAULT_LEADS_MAP = {
    "google_ads_lead_id": "Lead ID",
    "account_id": "Account ID",
    "business_name": "Business",
    "created_at": "Created",
    "lead_type": "Type",
    "lead_category": "Category",
    "charge_status": "Charge Status",
    "lead_price": "Lead Price",
    "currency_code": "Currency",
    "postal_code": "Postal Code",
    "phone_last4": "Phone (last4)",
}

DEFAULT_AGGREGATES_MAP = {
    "accountId": "Account ID",
    "businessName": "Business",
    "currencyCode": "Currency",
    "currentPeriodChargedLeads": "Charged Leads",
    "previousPeriodChargedLeads": "Prev Charged",
    "currentPeriodTotalCost": "Spend",
    "previousPeriodTotalCost": "Prev Spend",
    "currentPeriodPhoneCalls": "Calls",
    "previousPeriodPhoneCalls": "Prev Calls",
    "currentPeriodConnectedPhoneCalls": "Connected Calls",
    "previousPeriodConnectedPhoneCalls": "Prev Connected",
    "averageFiveStarRating": "Avg Rating",
    "totalReview": "Reviews",
}
