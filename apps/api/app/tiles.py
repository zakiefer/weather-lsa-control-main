from fastapi import APIRouter, Response
import httpx
import os
from PIL import Image, ImageDraw
from io import BytesIO

router = APIRouter(prefix="/v1/tiles")


@router.get("/fixture/{z}/{x}/{y}.png")
async def fixture_tile(z: int, x: int, y: int):
    # 256x256 PNG with deterministic content
    img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # Gray background
    draw.rectangle([(0, 0), (255, 255)], fill=(200, 200, 200, 255))
    text = f"z{z} x{x} y{y}"
    draw.text((10, 10), text, fill=(0, 0, 0, 255))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png")


OSM_BASE = os.getenv("OSM_TILE_BASE", "https://tile.openstreetmap.org")


@router.get("/osm/{z}/{x}/{y}.png")
async def osm_proxy(z: int, x: int, y: int):
    # Proxy OSM tile via server (respect usage; dev purposes only)
    url = f"{OSM_BASE}/{z}/{x}/{y}.png"
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(url)
        r.raise_for_status()
        return Response(content=r.content, media_type="image/png")
