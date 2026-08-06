# Coding Standards — Kitchen Ke Rules

## 1. Ye kya hai?

Code likhne ke rules. Inhe follow karo toh code khud khud padhne layak hota hai.

## 2. Tools

| Tool | Kaam | Rule |
|---|---|---|
| Python | 3.11+ | Har function mein type hints |
| `ruff format` | Formatter | Line length 100, double quotes |
| `ruff check` | Linter | `pyproject.toml` ka ruleset |
| `pyright` | Type checker | 0 errors ho hi jaati |
| `make check` | Gate | Sab kuch ek saath — pass hone se pehle merge nahi |

## 3. Structure

```
NN_chapter/chapter/           ← package, `__init__.py` public API
    models/                   ← data cheezein
    events/                   ← messages
    ...functional layers...   ← database/, repository/, loader/, engine/, renderer/, widgets/, windows/
    tests/                    ← test_<module>.py
```

- Chapter folder mein `__init__.py` nahi hota
- Tests apne module ke saath: `NN_name/name/tests/test_x.py`

## 4. Classes

| Rule | Matlab |
|---|---|
| One responsibility | Docstring mein 2 verbs dikhe → class split karo |
| Constructor injection | Dependencies andar banane ki bajay bahar se aati hain |
| Frozen dataclass | Models aur events ke liye |

## 5. UI Rules

```
Widgets:
  model lete hain
  EventBus nahi chhunte ❌
  SQL nahi jaante ❌
  data load nahi karte ❌
Painting:
  sirf renderer mein
  event handlers mein kabhi nahi ❌
Windows:
  terminal event publish kar sakte hain
  subscribe sirf bootstrap mein
```

## 6. Events

- Frozen dataclass, `Event` base se
- Payload sirf data: strings, numbers, tuples of models
- Kabhi connection, widget, callable nahi
- Module ko kaam karwana hai → event bhejo, bas

## 7. Error Handling

| Jagah | Kya hota hai |
|---|---|
| Bus | Handler fail → log, aage ka kaam chalta rahe |
| Services | Log karo, return karo — UI mein raise nahi |
| Database | Contract toota → raise (file nahi, table nahi) |
| Loader | Raise ko log mein badal deta hai |

## 8. Mana Hai — Black List

```
from x import *                                    ❌
from ..market import ... (relative cross-module)   ❌
from market.database import ... (dusre module ka internal) ❌
TODO/FIXME / dead code / mock logic / sample trading logic ❌
```

## 9. Story

Socho ek restaurant ki kitchen.

Har chef ka ek kaam — ek sirf sabzi kaat-ta hai, ek sirf gas chalata hai.

Kitchen mein rules likhe hain: "hath dhoke aao", "apni jagah ka saaman wahi rakho".

Koi chef rules toda → khana kharab. Koi chef rules follow kiya → khana ekdum same taste ka.

Ye file wahi rules hai.

## 10. Example — Accha Code

```python
@dataclass(frozen=True)
class ChartModel:
    symbol: str
    bars: tuple[Bar, ...]
```

Ek kaam, ek class, ek file. Type hint ✓, frozen ✓, no logic ✓.

## 11. Future

Naya phase aayega → naye standards usi style mein add honge.

> Code waisa likho jaise koi aur tumhare code ko bina tumhare poora samjhe.
