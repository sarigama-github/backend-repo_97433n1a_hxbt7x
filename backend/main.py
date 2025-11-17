import os
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from bson import ObjectId

from database import db, create_document, get_documents
from schemas import CatalogItem, CollectionItem, Transaction, PriceSnapshot

app = FastAPI(title="One Piece TCG Portfolio API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----- Helpers -----

def oid(id_str: str) -> ObjectId:
    try:
        return ObjectId(id_str)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid id format")


def to_str_id(doc: Dict[str, Any]) -> Dict[str, Any]:
    if not doc:
        return doc
    doc = dict(doc)
    if doc.get("_id"):
        doc["_id"] = str(doc["_id"])
    # Make datetime json-serializable
    for k, v in list(doc.items()):
        if isinstance(v, datetime):
            doc[k] = v.isoformat()
    return doc


# Simple currency conversion (mock). Base = EUR.
RATES = {
    "EUR": 1.0,
    "USD": 1.08,
    "GBP": 0.86,
    "JPY": 162.0,
    "BTC": 1.0 / 60000.0,  # ~60k EUR/BTC
    "ETH": 1.0 / 3000.0,
}


def convert(amount_eur: float, to_currency: str) -> float:
    rate = RATES.get(to_currency.upper())
    if rate is None:
        return amount_eur
    return amount_eur * rate


@app.get("/")
def root():
    return {"name": "One Piece TCG Portfolio API", "status": "ok"}


@app.get("/schema")
def schema():
    # Very light schema description for external integrators
    return {
        "collections": [
            "catalogitem",
            "collectionitem",
            "transaction",
            "pricesnapshot",
        ],
        "models": {
            "CatalogItem": CatalogItem.model_json_schema(),
            "CollectionItem": CollectionItem.model_json_schema(),
            "Transaction": Transaction.model_json_schema(),
            "PriceSnapshot": PriceSnapshot.model_json_schema(),
        },
    }


# ----- Catalog -----

class CatalogCreate(CatalogItem):
    pass


@app.post("/catalog", status_code=201)
def add_catalog_item(payload: CatalogCreate):
    cid = create_document("catalogitem", payload)
    doc = db["catalogitem"].find_one({"_id": ObjectId(cid)})
    return to_str_id(doc)


@app.get("/catalog/search")
def search_catalog(q: str = Query("", min_length=0), limit: int = 20):
    filt = {}
    if q:
        filt = {"$or": [
            {"name": {"$regex": q, "$options": "i"}},
            {"set_name": {"$regex": q, "$options": "i"}},
            {"number": {"$regex": q, "$options": "i"}},
        ]}
    docs = db["catalogitem"].find(filt).limit(min(limit, 100))
    return [to_str_id(d) for d in docs]


@app.get("/catalog/{catalog_id}")
def get_catalog_item(catalog_id: str):
    d = db["catalogitem"].find_one({"_id": oid(catalog_id)})
    if not d:
        raise HTTPException(404, "Catalog item not found")
    return to_str_id(d)


# ----- Collection (portfolio holdings) -----

class CollectionCreate(CollectionItem):
    pass


@app.post("/collection", status_code=201)
def add_collection_item(payload: CollectionCreate):
    # ensure referenced catalog exists if provided
    if payload.catalog_id:
        if not db["catalogitem"].find_one({"_id": oid(payload.catalog_id)}):
            raise HTTPException(400, "catalog_id not found")
    new_id = create_document("collectionitem", payload)
    d = db["collectionitem"].find_one({"_id": ObjectId(new_id)})
    return to_str_id(d)


@app.get("/collection")
def list_collection():
    docs = db["collectionitem"].find({}).sort("created_at", -1)
    return [to_str_id(d) for d in docs]


@app.delete("/collection/{item_id}")
def delete_collection_item(item_id: str):
    res = db["collectionitem"].delete_one({"_id": oid(item_id)})
    if res.deleted_count == 0:
        raise HTTPException(404, "Item not found")
    return {"ok": True}


@app.put("/collection/{item_id}")
def update_collection_item(item_id: str, payload: Dict[str, Any]):
    allowed_fields = {
        "purchase_price", "purchase_date", "condition", "is_graded", "grade_service", "grade_score", "grade_label",
        "quantity", "currency"
    }
    update = {k: v for k, v in payload.items() if k in allowed_fields}
    if not update:
        raise HTTPException(400, "No valid fields to update")
    if "purchase_date" in update and isinstance(update["purchase_date"], str):
        try:
            update["purchase_date"] = datetime.fromisoformat(update["purchase_date"])  # type: ignore
        except Exception:
            raise HTTPException(400, "Invalid purchase_date format, use ISO 8601")
    res = db["collectionitem"].update_one({"_id": oid(item_id)}, {"$set": update})
    if res.matched_count == 0:
        raise HTTPException(404, "Item not found")
    d = db["collectionitem"].find_one({"_id": oid(item_id)})
    return to_str_id(d)

# ----- Transactions -----

class TransactionCreate(Transaction):
    pass


@app.post("/transactions", status_code=201)
def add_transaction(payload: TransactionCreate):
    # Basic validation if collection provided
    if payload.collection_id:
        if not db["collectionitem"].find_one({"_id": oid(payload.collection_id)}):
            raise HTTPException(400, "collection_id not found")
    tid = create_document("transaction", payload)
    d = db["transaction"].find_one({"_id": ObjectId(tid)})
    return to_str_id(d)


@app.get("/transactions")
def list_transactions(limit: int = 100):
    docs = db["transaction"].find({}).sort("date", -1).limit(min(limit, 500))
    return [to_str_id(d) for d in docs]


# ----- Prices -----

class PriceCreate(PriceSnapshot):
    pass


@app.post("/prices/snapshot", status_code=201)
def add_price_snapshot(payload: PriceCreate):
    # Validate catalog exists
    if not db["catalogitem"].find_one({"_id": oid(payload.catalog_id)}):
        raise HTTPException(400, "catalog_id not found")
    pid = create_document("pricesnapshot", payload)
    d = db["pricesnapshot"].find_one({"_id": ObjectId(pid)})
    return to_str_id(d)


@app.get("/prices/latest")
def latest_price(catalog_id: str, currency: str = "EUR"):
    snap = db["pricesnapshot"].find({"catalog_id": catalog_id, "currency": currency}).sort("taken_at", -1).limit(1)
    snap = list(snap)
    if not snap:
        return {"catalog_id": catalog_id, "currency": currency, "price": None}
    return to_str_id(snap[0])


# ----- Portfolio analytics -----

@app.get("/portfolio/summary")
def portfolio_summary(currency: str = "EUR"):
    holdings = list(db["collectionitem"].find({}))
    total_cost_eur = 0.0
    total_value_eur = 0.0
    items_summary = []

    # build price cache: latest by catalog_id in requested currency
    latest_by_catalog: Dict[str, float] = {}
    for h in holdings:
        catalog_id = h.get("catalog_id")
        if not catalog_id:
            continue
        if catalog_id not in latest_by_catalog:
            snap = db["pricesnapshot"].find({"catalog_id": catalog_id, "currency": currency}).sort("taken_at", -1).limit(1)
            snap = list(snap)
            latest_by_catalog[catalog_id] = snap[0]["price"] if snap else 0.0

    # also compute 24h change if available (using two snapshots)
    change24: Dict[str, float] = {}
    for cid in list(latest_by_catalog.keys()):
        snaps = list(db["pricesnapshot"].find({"catalog_id": cid, "currency": currency}).sort("taken_at", -1).limit(2))
        if len(snaps) == 2 and snaps[1]["price"]:
            change = (snaps[0]["price"] - snaps[1]["price"]) / snaps[1]["price"]
            change24[cid] = change
        else:
            change24[cid] = 0.0

    for h in holdings:
        qty = h.get("quantity", 1)
        cost = float(h.get("purchase_price", 0.0)) * qty
        total_cost_eur += cost if h.get("currency", "EUR") == "EUR" else cost / RATES.get(h.get("currency", "EUR"), 1.0)
        current_price = latest_by_catalog.get(h.get("catalog_id"), 0.0)
        current_val = current_price * qty
        total_value_eur += current_val
        items_summary.append({
            "item": to_str_id(h),
            "current_price": current_price,
            "current_value": current_val,
            "unrealized": current_val - cost,
            "change24": change24.get(h.get("catalog_id"), 0.0),
        })

    # Biggest movers by absolute 24h value change
    movers = sorted(items_summary, key=lambda x: abs(x["current_value"] * x["change24"]), reverse=True)[:5]

    data = {
        "currency": currency,
        "total_cost": convert(total_cost_eur, currency),
        "total_value": convert(total_value_eur, currency),
        "unrealized_pnl": convert(total_value_eur - total_cost_eur, currency),
        "items": items_summary,
        "biggest_movers": movers,
    }
    return data


@app.get("/trends/timeseries")
def trends_timeseries(currency: str = "EUR", days: int = 30):
    # Aggregate daily portfolio value using the latest snapshot per catalog per day
    since = datetime.now(timezone.utc).timestamp() - days * 86400
    # Build map: day -> {catalog_id -> price}
    snaps = db["pricesnapshot"].find({"currency": currency})
    day_map: Dict[str, Dict[str, float]] = {}
    for s in snaps:
        ts = s.get("taken_at")
        if isinstance(ts, datetime):
            epoch = ts.replace(tzinfo=timezone.utc).timestamp()
        else:
            # if stored as str
            try:
                epoch = datetime.fromisoformat(str(ts)).timestamp()
            except Exception:
                continue
        if epoch < since:
            continue
        day = datetime.utcfromtimestamp(epoch).strftime("%Y-%m-%d")
        day_map.setdefault(day, {})[s["catalog_id"]] = float(s["price"])

    # For each day, compute portfolio value using quantities
    holdings = list(db["collectionitem"].find({}))
    series = []
    for day in sorted(day_map.keys()):
        prices = day_map[day]
        value = 0.0
        for h in holdings:
            qty = h.get("quantity", 1)
            cid = h.get("catalog_id")
            if not cid:
                continue
            price = prices.get(cid)
            if price is None:
                continue
            value += price * qty
        series.append({"date": day, "value": convert(value, currency)})

    return {"currency": currency, "series": series}


# Simple export endpoint
@app.get("/export")
def export_data():
    col = [to_str_id(x) for x in db["collectionitem"].find({})]
    txs = [to_str_id(x) for x in db["transaction"].find({})]
    cats = [to_str_id(x) for x in db["catalogitem"].find({})]
    return {"catalog": cats, "collection": col, "transactions": txs}


@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": []
    }

    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Configured"
            response["database_name"] = db.name if hasattr(db, 'name') else "✅ Connected"
            response["connection_status"] = "Connected"
            collections = db.list_collection_names()
            response["collections"] = collections[:10]
            response["database"] = "✅ Connected & Working"
        else:
            response["database"] = "⚠️  Available but not initialized"

    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:50]}"

    import os as _os
    response["database_url"] = "✅ Set" if _os.getenv("DATABASE_URL") else "❌ Not Set"
    response["database_name"] = "✅ Set" if _os.getenv("DATABASE_NAME") else "❌ Not Set"

    return response


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
