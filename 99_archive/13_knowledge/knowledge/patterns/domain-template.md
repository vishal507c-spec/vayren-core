# Domain Template

```
domain/
├── __init__.py    # Public API — re-export models, services, events
├── README.md      # Domain documentation
├── models/        # Domain entities
├── services/      # Business logic
├── events/        # Domain events (past tense naming)
├── adapters/      # External integrations (optional)
├── library/       # Concrete implementations (optional)
├── tests/         # All tests
└── conftest.py    # Pytest fixtures
```
