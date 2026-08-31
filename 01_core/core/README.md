# Core — Post Office (01_core)

`01_core/core/` — platform ki **neev**. Kisi par depend nahi. Bus + contracts + registry + system + AI yahan.

## Packages
| Package | Kaam |
|---|---|
| `event_bus` | `EventBus` — sync publish/subscribe, exact-type, fail-safe |
| `events` | `Event` base + `AppStarted` |
| `logger` | `configure_logging` / `get_logger` |
| `registry` | `Registry[T]` + `ComponentRegistry`/`CapabilityRegistry` |
| `contracts` | Component/Capability/Contract/Manifest dataclasses |
| `system` | `SystemModel`, graphs, change-impact (`analyze_change`) |
| `ai` | Intent→Plan→Validator→Simulation→Sandbox (deterministic, AI optional) |

## EventBus
```python
bus.subscribe(DataLoaded, engine.on_data_loaded)  # sirf bootstrap mein
bus.publish(DataLoaded(symbol="SPY", bars=bars))   # koi bhi service
```
Rules: exact-type match, handler fail → log, app chalta rahe. Widgets bus nahi chhunte.

## Kya Nahi Karega
Trading logic / SQL / async / business logic — sirf messages, contracts, registry.
