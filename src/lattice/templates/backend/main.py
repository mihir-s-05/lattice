import logging
import os
from datetime import datetime, timezone
from typing import List, Dict, Optional

from fastapi import FastAPI, HTTPException, Request, Response, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
try:
    from dotenv import load_dotenv, find_dotenv
    load_dotenv(find_dotenv(), override=False)
except Exception:
    pass


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


logger = logging.getLogger("items_api")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

app = FastAPI(title=os.getenv("APP_NAME", "Items API"))

origins = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "*").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response: Response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Content-Security-Policy", "default-src 'self'")
    response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


class Item(BaseModel):
    id: str
    name: str
    description: str
    createdAt: str
    updatedAt: str


class ItemInput(BaseModel):
    name: str
    description: str = ""


class ItemRepository:
    def __init__(self) -> None:
        self._items: Dict[str, Item] = {}
        self._next_id = 1

    def list(self) -> List[Item]:
        return list(self._items.values())

    def create(self, data: ItemInput) -> Item:
        now = utcnow_iso()
        iid = str(self._next_id)
        self._next_id += 1
        item = Item(id=iid, name=data.name, description=data.description, createdAt=now, updatedAt=now)
        self._items[iid] = item
        return item

    def get(self, item_id: str) -> Optional[Item]:
        return self._items.get(item_id)

    def update(self, item_id: str, data: ItemInput) -> Optional[Item]:
        itm = self._items.get(item_id)
        if not itm:
            return None
        upd = itm.copy(update={"name": data.name, "description": data.description, "updatedAt": utcnow_iso()})
        self._items[item_id] = upd
        return upd

    def delete(self, item_id: str) -> bool:
        if item_id in self._items:
            del self._items[item_id]
            return True
        return False


repo = ItemRepository()

API_KEY = os.getenv("API_KEY")

def require_api_key(x_api_key: Optional[str] = Header(None)) -> None:
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.get("/health")
def health() -> Dict[str, bool]:
    return {"ok": True}


@app.get("/items", response_model=List[Item])
def list_items() -> List[Item]:
    return repo.list()


@app.post("/items", response_model=Item, status_code=201)
def create_item(inp: ItemInput, _: None = Depends(require_api_key)) -> Item:
    try:
        item = repo.create(inp)
        logger.info("created item id=%s", item.id)
        return item
    except Exception as e:
        logger.error("error creating item: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/items/{item_id}", response_model=Item)
def get_item(item_id: str) -> Item:
    item = repo.get(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return item


@app.put("/items/{item_id}", response_model=Item)
def update_item(item_id: str, inp: ItemInput, _: None = Depends(require_api_key)) -> Item:
    item = repo.update(item_id, inp)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    logger.info("updated item id=%s", item_id)
    return item


@app.delete("/items/{item_id}", status_code=204)
def delete_item(item_id: str, _: None = Depends(require_api_key)) -> None:
    ok = repo.delete(item_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Item not found")
    logger.info("deleted item id=%s", item_id)
    return None