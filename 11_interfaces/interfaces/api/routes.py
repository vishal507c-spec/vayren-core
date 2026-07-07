from fastapi import APIRouter

router = APIRouter(prefix="/api/v1")


@router.get("/bars/{symbol}")
async def get_bars(symbol: str):
    return {"symbol": symbol, "bars": []}


@router.get("/portfolio")
async def get_portfolio():
    return {"portfolio": {}}


@router.get("/positions")
async def get_positions():
    return {"positions": []}


@router.post("/orders")
async def place_order(symbol: str, side: str, quantity: int):
    return {"status": "submitted", "symbol": symbol, "side": side, "quantity": quantity}
