from ui.utils import prettify_headers


def test_created_header_mapping():
    rows = [{"created_at": "2025-08-16", "other": 1}]
    pretty = prettify_headers(rows)
    # Guard against regressions: created_at should map to simple "Created"
    assert pretty[0]["Created"] == "2025-08-16"
    assert pretty[0]["Other"] == 1
