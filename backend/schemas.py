"""
Database Schemas for One Piece TCG Portfolio Platform

Each Pydantic model maps to a MongoDB collection (lowercase class name).
"""
from typing import Optional, List, Literal
from pydantic import BaseModel, Field
from datetime import datetime

# Users (optional for future multi-user). For now we will default to a single demo user.
class User(BaseModel):
    name: str = Field(..., description="Full name")
    email: str = Field(..., description="Email address")
    currency: str = Field("EUR", description="Primary currency code")

# Master catalog entries (cards or sealed products). In real life this would be pre-populated.
class CatalogItem(BaseModel):
    
    category: Literal["card_raw", "card_graded", "sealed"] = Field(..., description="Type of item")
    name: str = Field(..., description="Card or product name")
    set_name: Optional[str] = Field(None, description="Set or product line")
    number: Optional[str] = Field(None, description="Card number / product code")
    variant: Optional[str] = Field(None, description="Foil, parallel, alt art, etc.")
    image_url: Optional[str] = Field(None, description="Cover or card image")
    
    # External refs (CardMarket, CardTrader, eBay, etc.)
    external_ids: Optional[dict] = Field(default_factory=dict, description="External marketplace IDs and URLs")

# A holding in the user's portfolio (one line can represent N quantity of the same item)
class CollectionItem(BaseModel):
    user_id: Optional[str] = Field(None, description="Owner id")
    catalog_id: Optional[str] = Field(None, description="Reference to catalog item _id as string")
    category: Literal["card_raw", "card_graded", "sealed"]
    name: str
    set_name: Optional[str] = None
    number: Optional[str] = None
    variant: Optional[str] = None

    # Condition & grading
    condition: Optional[Literal["Mint","Near Mint","Excellent","Good","Played","Poor"]] = Field(None, description="Card condition")
    is_graded: bool = Field(False, description="Whether the card is graded")
    grade_service: Optional[str] = Field(None, description="PSA, BGS, CGC, etc.")
    grade_score: Optional[float] = Field(None, description="Numerical grade score, e.g., 10, 9.5")
    grade_label: Optional[str] = Field(None, description="Optional grade label string")

    quantity: int = Field(1, ge=1)

    # Acquisition
    purchase_price: float = Field(0, ge=0)
    currency: str = Field("EUR")
    purchase_date: Optional[datetime] = None
    source: Optional[str] = None

# Transactions for realized P&L (buys/sells)
class Transaction(BaseModel):
    user_id: Optional[str] = None
    collection_id: Optional[str] = Field(None, description="Link to CollectionItem")
    type: Literal["buy", "sell"]
    quantity: int = Field(1, ge=1)
    price_total: float = Field(..., ge=0, description="Total price for the trade in the given currency")
    currency: str = Field("EUR")
    date: datetime = Field(default_factory=datetime.utcnow)
    notes: Optional[str] = None

# Price snapshot for a catalog item (used to compute unrealized P&L and trends)
class PriceSnapshot(BaseModel):
    catalog_id: str
    currency: str = Field("EUR")
    price: float = Field(..., ge=0)
    source: str = Field("mock", description="Data source identifier")
    taken_at: datetime = Field(default_factory=datetime.utcnow)
