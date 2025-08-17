import csv
from io import StringIO

from ui.utils import prettify_headers, to_csv


def test_to_csv_basic_and_quoting():
    rows = [
        {"a": 1, "b": "x,y"},
        {"b": 'He said "hi"', "a": 2, "c": None},
    ]
    out = to_csv(rows)
    # Parse back with csv to verify structure and quoting
    buf = StringIO(out)
    reader = csv.reader(buf)
    header = next(reader)
    assert header == ["a", "b", "c"]
    r1 = next(reader)
    r2 = next(reader)
    assert r1 == ["1", "x,y", ""]
    # Quotes should be preserved/escaped correctly
    assert r2 == ["2", 'He said "hi"', ""]


def test_to_csv_empty():
    assert to_csv([]) == ""


def test_prettify_headers_heuristics_and_mapping():
    rows = [
        {
            "id": 123,
            "cap_id": "ABC",
            "gaql": "SELECT *",
            "businessName": "Acme Co",
            "created_at": "2025-08-16",
        }
    ]
    pretty = prettify_headers(rows)
    # Heuristics and special-casing
    assert pretty[0]["ID"] == 123
    assert pretty[0]["CAP"] == "ABC"
    assert pretty[0]["GAQL"] == "SELECT *"
    assert pretty[0]["Business Name"] == "Acme Co"
    assert pretty[0]["Created"] == "2025-08-16"

    # Mapping overrides take precedence
    mapping = {"businessName": "Business"}
    pretty2 = prettify_headers(rows, mapping)
    assert pretty2[0]["Business"] == "Acme Co"
