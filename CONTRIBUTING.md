# CONTRIBUTING — Kaise Kaam Karein

## 1. Ye kya hai?

Ye document batata hai ki **is repository mein kaam kaise karna hai** — chahe aap khud ho, AI ho, ya future mein koi aur developer aaye.

## 2. Kaun kaun kaam karega?

- **Aap (owner)** — vision, decisions, final approval
- **AI** — code, tests, review, documentation

| Role | Kaam |
|---|---|
| Aap | "Kya banana hai" bolna, approve karna |
| AI | "Kaise banana hai" karna, verify karna |

## 3. Har Session Ka Flow

```
1. READ  90_brain/ai_memory.md
   → "Kya chal raha tha? Aage kya?"

2. READ  90_brain/development_log.md
   → "Is area ke baare mein kya decide hua?"

3. CODE  karo (AGENTS.md ke rules follow karo)

4. RUN   make check
   → "Sab kuch sahi hai?"

5. UPDATE  90_brain/development_log.md + ai_memory.md
   → "Kya kiya? Aage kya?"
```

## 4. Code Kaise Add Karein

Har naye kaam ka order pakka hai:

```
1. MODEL   → Ye cheez KYA hai?        (models/file.py)
2. EVENT   → Ye kaunsa message bhejta hai?   (events/file.py)
3. SERVICE → Ye KYA kaam karta hai?   (services/file.py)
4. TEST    → Kya sahi chalta hai?     (tests/test_file.py)
```

### Rules

- Har naye Python file ka ek test file hona chahiye
- Har public function mein type hints honi chahiye
- Har model frozen dataclass hona chahiye (immutable)
- Har event ka naam past tense hona chahiye (`DataLoaded`, `ChartReady`)

## 5. Gyan Kahaan Likhein

| Kya hua | Kahaan likho |
|---|---|
| Architecture decision | `90_brain/development_log.md` |
| Code change complete | `90_brain/development_log.md` |
| Current state update | `90_brain/ai_memory.md` |
| Naya module aaya | `90_brain/roadmap.md` + `90_brain/module_contracts.md` |

> Purana `13_knowledge/` ab `99_archive/` mein hai — wahan kuch mat likho.

## 6. Commit Style

Conventional commits — har commit ek hi kaam:

| Prefix | Matlab |
|---|---|
| `feat:` | Naya feature |
| `fix:` | Bug fix |
| `docs:` | Sirf documentation |
| `refactor:` | Kaam waisa hi, code alag |
| `test:` | Tests add/fix |
| `chore:` | Tooling, dependencies |

Examples:

```
feat: add candle zoom for the chart widget
fix: correct bar timestamp ordering in the repository
docs: rewrite 03_chart README in teaching style
test: add repository limit tests
```

## 7. Accha Contribution Kya Hota Hai?

| ✅ Accha | ❌ Kharab |
|---|---|
| Ek commit = ek kaam | Ek commit mein bahut kuch |
| Tests saath mein | Tests nahi |
| Public API badla → README update | Docs nahi |
| `make check` pass before commit | Validation skip |
| Brain docs update kiye | Brain update nahi kiya |

## 8. Ek Line Mein

> Kaam karo → `make check` chalao → Brain update karo → commit karo. Bas.
