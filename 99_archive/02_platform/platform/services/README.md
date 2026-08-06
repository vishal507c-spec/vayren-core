# platform/services/ — Platform Services

## What Is Inside

| Service | Responsibility |
|---|---|
| `EventBus` | Central pub/sub message bus — departments communicate through events, never direct calls |
| `ConfigLoader` | Loads configuration from YAML files, environment variables, and CLI args |
| `LifecycleManager` | Manages system startup, shutdown, and state transitions |
| `Container` | Dependency injection container — wires services together |
