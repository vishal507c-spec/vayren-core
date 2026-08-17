# Project Rules — Ghar Ke Pakke Rules

## 1. Ye kya hai?

Ye repository ke **permanent rules** hain. Inhe kabhi nahi toda jata — na aap, na AI, na future developers.

## 2. Rules — Ek Ek Karke

### Rule 1: Pehle Brain Padho

Code chhune se pehle `90_brain/` ki saari docs padhni **zaroori** hai.

```
Code likhne se pehle:
  90_brain/architecture.md
  → event_catalog.md
  → module_contracts.md
  → coding_standards.md
  → naming_conventions.md
  → project_rules.md
  → ai_memory.md
```

### Rule 2: Event-Driven Only

Har module sirf **EventBus** se baat karta hai.

```
Module A              Module B
    │                     │
    └──► EventBus ◄───────┘
         (Post Office)
```

Seedha call — **mana hai**.

### Rule 3: One Module = One Responsibility

- Ek module sirf ek kaam karta hai
- Koi God Class nahi
- Naya kaam aaye → naya module banao

### Rule 4: No Circular Dependencies

Dependency hamesha ek hi taraf:

```
00_app → 01_core → 02_data → 03_market → 04_chart
```

Upar se neeche. Aage-piche — mana.

### Rule 5: Numbering = Startup Flow

Number priority nahi hai, **order** hai — kaun pehle start hota hai.

```
00_app → 01_core → 02_data → 03_market → 04_chart
```

Aage: `05_strategy → 06_backtest → 07_risk → 08_execution → 09_portfolio → 10_scanner → 11_indicator → 12_drawing → 13_replay → 14_workspace → 15_plugin` (usi order mein; `02_data` aa chuka hai).

### Rule 6: Naya Feature = Naya Module

Existing module kabhi expand nahi hota. Purana module apna kaam karta rahega.

### Rule 7: No Placeholder Code

- No placeholder code
- No mock business logic
- No sample trading logic

Jo feature nahi bana, wo code mein **hai hi nahi**.

### Rule 8: Charting Mein Trading Logic Nahin

- Market sirf data store/store karta hai
- Chart sirf data dikhata hai
- Indicators, signals, positions — charting ka kaam nahi

### Rule 9: Layers Kabhi Mix Nahi Hote

| Cheez | Kahan nahi |
|---|---|
| SQL | UI mein ❌ |
| Drawing | Data loader mein ❌ |
| EventBus logic | Widgets ke andar ❌ |
| Business logic | UI classes mein ❌ |

### Rule 10: Har Public Class Ka Ek Clear Kaam

Har public class ke paas:

- Ek hi responsibility
- Docstring jo bataye "ye kya karta hai"

### Rule 11: Architecture Badlega Toh Log

- `90_brain/development_log.md` mein entry
- `90_brain/architecture.md` update

## 3. Phase 1 Scope — Filhaal Bas Ye

```
App shuru
    ↓
SQLite se candles lo
    ↓
Candlestick chart dikhao
```

Aur kuch nahi. No indicators, no strategies, no brokers, no replay — jab tak roadmap bole.

## 4. Story

Socho ek ghar hai.

Ghar ke rules fixed hain — "kitchen mein shoes nahi", "raat 10 baje lights off".

Naye log aayenge, purane log jayenge — rules wahi rahenge. Isliye ghar kabhi bigadta nahi.

Ye file wahi hai — ghar ke rules ki deewar.

## 5. Ye Kya Nahi Karega

Ye file code nahi chalti — ye **check** karti hai ki code sahi chal raha hai ya nahi. AI agent ise padhkar rules follow karta hai.

## 6. Future

Naya phase aaye toh naye rules add hote hain — old rules replace nahi hote.

> Rule todo toh ghar ka order toota. Order todo toh platform bigda.
