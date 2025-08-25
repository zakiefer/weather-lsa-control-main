"""Folium radar layer helpers.

Acceptance criteria:
- Provide `attach_rainviewer_layer(m, name="RainViewer Radar", opacity=0.6, show=True)` that
  returns a `folium.raster_layers.TileLayer` using RainViewer tiles.
- Optionally provide `attach_radar_layer(m, provider=...)` with NOAA fallback.
"""

from __future__ import annotations

import folium
from folium.raster_layers import TileLayer

__all__ = ["attach_rainviewer_layer"]


def attach_rainviewer_layer(
    m: folium.Map,
    name: str = "RainViewer Radar",
    opacity: float = 0.6,
    show: bool = True,
) -> TileLayer:
    """Attach the RainViewer latest radar tiles to the given Folium map.

    Args:
            m: Folium map instance
            name: Layer name to show in LayerControl
            opacity: Layer opacity (0..1 supported by Leaflet; Folium passes through)
            show: Whether the layer is visible by default

    Returns:
            The created Folium TileLayer (already added to the map).
    """
    url = "https://tilecache.rainviewer.com/v2/radar/nowcast_0/256/{z}/{x}/{y}/2/1_1.png"
    layer = TileLayer(
        tiles=url,
        name=name,
        attr="© RainViewer",
        overlay=True,
        control=True,
        show=show,
        opacity=opacity,
    )
    layer.add_to(m)
    return layer


def attach_radar_layer(
    m: folium.Map,
    provider: str = "rainviewer",
    opacity: float = 0.6,
    show: bool = True,
) -> TileLayer:
    """Attach a radar layer by provider (rainviewer or noaa). Defaults to RainViewer.

    NOAA source uses IEM/NEXRAD cached tiles as a simple fallback.
    """
    provider_l = (provider or "").strip().lower()
    if provider_l == "noaa":
        url = "https://mesonet.agron.iastate.edu/cache/tile.py/1.0.0/" "nexrad-n0q-900913/{z}/{x}/{y}.png"
        layer = TileLayer(
            tiles=url,
            name="NOAA NEXRAD Latest",
            attr="IEM/NEXRAD",
            overlay=True,
            control=True,
            show=show,
            opacity=opacity,
        )
        layer.add_to(m)
        return layer
    # Default
    return attach_rainviewer_layer(m, opacity=opacity, show=show)
