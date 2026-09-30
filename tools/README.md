# Scripts — Chhote Helper Tools

## 1. Ye kya hai?

`scripts/` — development ke chhote tools. Ye app ka hissa nahi hain, sirf kaam mein aate hain.

## 2. Tools

| Script | Kaam | Kab chalao |
|---|---|---|
| `run_tests.py` | Suite-partitioned pytest driver — har partition apne fresh interpreter mein (suite isolation); live test files wale domains | `make test` ke bajaye stable full run ke liye; CI bhi yahi chalata hai |
| `validate_structure.py` | 9 domains ka layout check (`app`/`core`/`data`/`market`/`strategy`/`backtest`/`risk`/`execution`/`broker`) | `make check` mein |
| `validate_imports.py` | Cross-module dependency rules check (AST se; `if TYPE_CHECKING:` imports runtime coupling nahi maane jate) | `make check` mein |
| `benchmark.py` | AEOS velocity harness (`gate`/`begin`/`record`/`impact`/`replay`/`scoreboard`, output regenerated on use) | Validation cost measure karne, impact plan, scoreboard regen ke liye |
| `validate_architecture_gate.py` | Wrong-language change gate: Rust-owned Python additions bina retention ke FAIL; declared third-party imports ke bahar kuch nahi | Har commit se pehle, pre-commit hook aur `make check` mein (`validate-architecture`) |
| `speed/__main__.py` | Phase-18 speed instrumentation (`mark`/`record`/`compile-context`/`recommend`/`dashboard`) — engineering-loop timing, task journal, context compiler | Har benchmarked task par (negligible overhead: ek JSONL line) |

## 3. Example

```bash
python scripts/validate_structure.py
python scripts/validate_imports.py
```

## 4. Ye Kya Nahi Karega

- Ye koi service nahi hai — app inhe kabhi import nahi karta
- `90_brain` validation se bahar hai
- Asli market data `D:\ZerodhaTradingData` par aayega — repo mein kabhi fabricate mat karo

## 5. Future

Naya module aayega → validators mein naya domain add hoga, scripts wahi rahenge.
