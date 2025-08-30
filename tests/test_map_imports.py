import importlib


def test_map_imports_and_overlays_resolve():
    mod = importlib.import_module("ui.pages.Map")
    # Ensure imported overlay functions come from ui.map_layers
    assert getattr(mod, "add_historical_timeline").__module__ == "ui.map_layers"
    assert getattr(mod, "add_lsr_layers").__module__ == "ui.map_layers"

def test_map_imports_and_no_stubs():
    mod = importlib.import_module("ui.pages.Map")
    # Ensure imported overlay helpers come from ui.map_layers
    assert hasattr(mod, "add_historical_timeline")
    assert hasattr(mod, "add_lsr_layers")
    # Resolve the module where these functions are defined
    src_mod1 = getattr(mod.add_historical_timeline, "__module__", "")
    src_mod2 = getattr(mod.add_lsr_layers, "__module__", "")
    assert src_mod1 == "ui.map_layers"
    assert src_mod2 == "ui.map_layers"

    # Verify stub names are not defined locally anymore
    assert not hasattr(mod, "_extract_county_fips_stub")
    assert not hasattr(mod, "_alert_matches_filters_stub")
    assert not hasattr(mod, "_first_polygon_centroid_stub")
