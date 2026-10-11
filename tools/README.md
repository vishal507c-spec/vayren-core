# Tools — Developer & Validation Tooling

## 1. What is this?

`tools/` contains developer workflows, build drivers, velocity benchmarking, and deterministic CI validators. These are tooling scripts; they are not imported by the runtime application.

## 2. Tools & Validators

| Script | Purpose | When to Run |
|---|---|---|
| `route.py` | Sub-second task router (deterministic task → domain, language, primary files, tests) | At the start of any new task |
| `context_engine.py` | Context compiler & validation level escalation | For task planning and context verification |
| `build_rust.py` | Rust build driver (lean tests, packaging, shortcut sync) | During setup, packaging (`make exe`), or Rust compilation |
| `validate_structure.py` | Verifies domain package directory structure | In `make check` / CI |
| `validate_imports.py` | AST check enforcing `DOMAIN_DEPS` cross-module boundaries | In `make check` / CI |
| `validate_language_ownership.py` | Hard gate enforcing Rust vs Python ownership via `ownership_policy.json` | In `make check` / CI |
| `validate_architecture_gate.py` | Wrong-language & unauthorized dependency change gate | In `make check` / CI |
| `validate_authority.py` | Single-registry, single-authority, contract drift check | In `make check` / CI |
| `validate_routes.py` | Verifies `task_routes.json` against filesystem | In `make check` / CI |
| `validate_repo_graph.py` | Validates `repo_graph.json` consistency against canonical sources | In `make check` / CI |
| `validate_scope.py` | Change impact & test scope calculator | In `make check` / CI |
| `benchmark.py` | Velocity & impact execution harness (`make fast` uses impact mode) | During development loop (`make fast`) |
| `forensics/__main__.py` | Coding-time forensics measurement & timeline reporting | For developer session instrumentation |

## 3. Example

```bash
python tools/route.py --task "order state"
python tools/validate_structure.py
python tools/validate_imports.py
```

## 4. Invariants

- Tooling scripts are never imported by application runtime code.
- Market data is read from configured data directories (e.g. `D:\ZerodhaTradingData`); never fabricate mock market data.
