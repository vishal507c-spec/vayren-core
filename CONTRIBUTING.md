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

2. CODE  karo (AGENTS.md ke rules follow karo)

3. RUN   make check
   → "Sab kuch sahi hai?"

4. UPDATE  90_brain/ai_memory.md
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

Code change complete → `90_brain/ai_memory.md` (current state only; history auto-archives).
Naya module → `90_brain/architecture.md` + `module_contracts.md`. Rules → `AGENTS.md` owns workflow
(commit style wahin hai — yahan duplicate nahi).

## 6. Commit Style

Conventional commits, ek commit = ek kaam (`feat:`/`fix:`/`docs:`/`refactor:`/`test:`/`chore:`).
Detail + examples: `AGENTS.md` (yahi canonical hai).

## 7. Accha Contribution Kya Hota Hai?

Ek commit = ek kaam, tests saath mein, public API badla → README update,
`make check` pass before commit, Brain update. Detail: `AGENTS.md`.

## 8. Ek Line Mein

> Kaam karo → `make check` chalao → Brain update karo → commit karo. Bas.
