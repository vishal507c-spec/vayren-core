# Scripts — Chhote Helper Tools

## 1. Ye kya hai?

`scripts/` — development ke chhote tools. Ye app ka hissa nahi hain, sirf kaam mein aate hain.

## 2. Tools

| Script | Kaam | Kab chalao |
|---|---|---|
| `seed_sample_db.py` | `data/vayren.db` banata hai (random-walk OHLCV) | Pehli baar, jab asli DB nahi hai |
| `run_tests.py` | Suite-partitioned pytest driver — har partition apne fresh interpreter mein (Qt/GC crashes se bachne ke liye); `research/tests` samet saare 7 domains | `make test` ke bajaye stable full run ke liye; CI bhi yahi chalata hai |
| `validate_structure.py` | 7 domains ka layout check (`app`/`core`/`data`/`market`/`chart`/`strategy`/`backtest`) | `make check` mein |
| `validate_imports.py` | Cross-module dependency rules check (AST se; `if TYPE_CHECKING:` imports runtime coupling nahi maane jate) | `make check` mein |

## 3. Example

```bash
python scripts/seed_sample_db.py                 # default: SPY, 1200 bars
python scripts/seed_sample_db.py --symbol QQQ --bars 500 --output data/qqq.db

python scripts/validate_structure.py
python scripts/validate_imports.py
```

## 4. Ye Kya Nahi Karega

- Ye koi service nahi hai — app inhe kabhi import nahi karta
- `90_brain` validation se bahar hai; `99_archive` legacy snapshot hai — ruff/validators/tests se bahar
- Sample DB sirf khilona hai — asli data `D:\ZerodhaTradingData` par aayega

## 5. Future

Naya module aayega → validators mein naya domain add hoga, scripts wahi rahenge.
