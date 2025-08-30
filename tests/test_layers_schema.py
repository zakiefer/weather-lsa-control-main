import importlib


def test_map_layers_importable():
    import ui.map_layers as ml

    # Basic smoke: functions exist
    for name in [
        "add_spc_outlooks",
        "add_earthquakes",
        "add_tropical",
        "add_wildfires",
        "add_historical_timeline",
        "add_lsr_layers",
        "extract_county_fips",
        "alert_matches_filters",
        "first_polygon_centroid",
    ]:
        assert hasattr(ml, name), f"ui.map_layers missing {name}"


def test_map_layers_import_and_minimal_functions():
    mod = importlib.import_module("ui.map_layers")
    for name in (
        "add_spc_outlooks",
        "add_earthquakes",
        "add_tropical",
        "add_wildfires",
        "add_historical_timeline",
        "add_lsr_layers",
        "extract_county_fips",
        "alert_matches_filters",
        "first_polygon_centroid",
    ):
        assert hasattr(mod, name)
