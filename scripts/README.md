# Scripts — Chhote Helper Tools

## 1. Ye kya hai?

`scripts/` — development ke chhote tools. Ye app ka hissa nahi hain, sirf kaam mein aate hain.

## 2. Tools

| Script | Kaam | Kab chalao |
|---|---|---|
| `seed_sample_db.py` | `data/vayren.db` banata hai (random-walk OHLCV) | Pehli baar, jab asli DB nahi hai |
| `validate_structure.py` | 4 modules ka layout check | `make check` mein |
| `validate_imports.py` | Cross-module dependency rules check (AST se) | `make check` mein |

## 3. Example

```bash
python scripts/seed_sample_db.py                 # default: SPY, 1200 bars
python scripts/seed_sample_db.py --symbol QQQ --bars 500 --output data/qqq.db

python scripts/validate_structure.py
python scripts/validate_imports.py
```

## 4. Ye Kya Nahi Karega

- Ye koi service nahi hai — app inhe kabhi import nahi karta
- `99_archive` aur `90_brain` validation se bahar hain
- Sample DB sirf khilona hai — asli data `data/vayren.db` par aayega

## 5. Future

Naya module aayega → validators mein naya domain add hoga, scripts wahi rahenge.
