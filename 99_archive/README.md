# 99_archive — Purane Modules Ka Museum

## 1. Ye kya hai?

`99_archive/` — woh saare modules jinko platform ab use **nahi** karta. Purane architecture ka pura record.

```
99_archive/
    ├── 01_foundation/    (purana lib)
    ├── 02_platform/      (purana platform)
    ├── 03_market/        (purana market)
    ├── 04_signals … 13_knowledge   (baaki sab)
    └── ...
```

## 2. Ye kyu banaya?

Refactor ke waqt **delete karna** waste hota — history, ideas, lessons sab code mein hote hain. Isliye `git mv` se archive kiya, taaki:

- History safe (git me `git mv` = rename)
- Ideas reference ke liye available
- Active repo sirf minimum rakhe

## 3. Rules — Pakke

| Rule | Matlab |
|---|---|
| Kabhi import nahi | `sys.path` par nahi, build packages mein nahi |
| Kabhi extend nahi | Purane module mein naya kaam — mana |
| Sirf reference | Idea lena ho toh padh sakte ho |
| Remove sirf approval se | Archive se delete karne se pehle poochho |

## 4. Purane modules kyun retire hue?

| Purana module | Kyu retire |
|---|---|
| signals, strategies, risk | Trading logic — Phase 1 ka kaam nahi |
| execution, portfolio, broker | Execution domain — future modules |
| research, analytics | AI/ML — future scope |
| interfaces (API/web/mobile) | Desktop app hai, server nahi |
| infrastructure (docker/k8s) | CI/CD — abhi zaroorat nahi |

Unke kaam future mein naye chapters mein ayenge:

```
04_indicator → 05_drawing → … → 12_broker → 13_workspace → 14_plugin
```

## 5. Example — Kya reuse hua, kya nahi

| Cheez | Kya hua |
|---|---|
| `EventBus` | **Nikal liya** → `01_core/core/event_bus/` |
| Logger | **Nikal liya** → `01_core/core/logger/` |
| `Registry` | **Nikal liya** → `01_core/core/registry/` |
| `Bar` | **Nikal liya** → `02_market/market/models/bar.py` |
| Baaki sab | Archive mein |

## 6. Story

Socho purane ghar ka store room.

Kuch cheezein (EventBus, Bar) naye ghar mein use ho rahi hain. Baaki (purana furniture) store room mein.

Store room khol sakte ho — purani cheez dekhne ke liye. Naye furniture wahan se mat lao, naya hi banao.

## 7. Future

Jab naya module banega aur purane se kuch sahi lage — reference lena, code copy karna theek hai. Lekin import nahi.

> Archive = museum. Dekho, seekho, but use mat karo.
