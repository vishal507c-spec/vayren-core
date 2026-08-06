# platform/ — Platform

## What Is a Platform?

A **platform** is the underlying system that other software runs on. It provides the common services that every department needs:

- **Engine:** Controls startup, shutdown, and runtime mode (backtest/paper/live)
- **Event Bus:** Connects departments through publish/subscribe messaging
- **Clock:** Provides time, which runs differently in backtest (fast/simulated) vs live (real-time)
- **Configuration:** Loads and provides access to settings
- **Dependency Injection:** Manages how services are created and wired together

---

## Purpose

`platform/` is the **operating system** of the trading system. It provides the runtime environment that every department depends on, while remaining independent of any specific department's logic.

---

## Why This Folder Exists

Without `platform/`:
- Every department would need to manage its own lifecycle
- There would be no single way to control the system (start/stop/pause)
- The runtime mode (backtest vs live) would be handled differently everywhere
- Event communication between departments would be ad-hoc and inconsistent

`platform/` centralizes all of this so that departments can focus on their domain logic.

---

## Visual Diagram

```
                    platform/
                        
    ┌─────────────────┼─────────────────┐
    │                 │                 │
    ▼                 ▼                 ▼
 Engine          Event Bus           Clock
 ┌───────┐      ┌───────────┐    ┌──────────┐
 │ Mode  │      │ publish() │    │ .now     │
 │ Start │      │ subscribe│    │ set_time │
 │ Stop  │      │ handlers │    │ advance  │
 └───────┘      └───────────┘    └──────────┘
    │                 │                 │
    └─────────────────┼─────────────────┘
                      │
                      ▼
            ConfigLoader        Container (DI)
            ┌─────────────┐      ┌──────────────┐
            │ load()      │      │ register()   │
            │ save()      │      │ get()        │
            └─────────────┘      └──────────────┘
```

---

## What Platform Provides to Every Department

| Service | What It Provides | Used By |
|---|---|---|
| `Engine` | Runtime lifecycle (start/stop), mode (backtest/paper/live) | All departments |
| `EventBus` | Publish/subscribe messaging | All departments (cross-department communication) |
| `Clock` | Current time (real or simulated) | All departments (time-based decisions) |
| `ConfigLoader` | Settings from configuration files | All departments (configuration) |
| `Container` | Dependency injection for services | All departments (wiring) |

---

## Runtime Modes

| Mode | Clock | Execution | Data Source | Use Case |
|---|---|---|---|---|
| `BACKTEST` | Simulated (fast) | SimulatedBroker | Historical data | Testing strategies |
| `PAPER` | Real | SimulatedBroker | Live data | Validating without real money |
| `LIVE` | Real | Real broker | Live data | Real trading |

---

## Quick Example

```python
from platform.models.engine import Engine
from platform.models.mode import Mode
from platform.services.event_bus import EventBus

# Create the engine in backtest mode
engine = Engine(mode=Mode.BACKTEST)
engine.start()

# Create the event bus
bus = EventBus()

# Subscribe to an event
def handle_bar_received(event):
    print(f"New bar: {event.bar.symbol}")

bus.subscribe(BarReceived, handle_bar_received)

# Somewhere else in the system:
bus.publish(BarReceived(bar=some_bar))
```

---

## Continue to the Next Lesson

→ `apps/` — How humans interact with the system
