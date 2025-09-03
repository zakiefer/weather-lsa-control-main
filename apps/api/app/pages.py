from fastapi import APIRouter

router = APIRouter(prefix="/v1/pages")


@router.get("")
def list_pages():
    # This can be extended to read migrate/pages-inventory.json
    return {"ok": True}


@router.get("/{name}")
def page_info(name: str):
    # Placeholder: downstream routes will be added per page during migration
    return {"page": name, "status": "stub"}
