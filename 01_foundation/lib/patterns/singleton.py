from typing import Any


class Singleton:
    """Mixin for singleton classes.

    Usage:
        class MyService(Singleton):
            pass

        s1 = MyService.instance()
        s2 = MyService.instance()
        assert s1 is s2
    """

    _instances: dict[type, Any] = {}

    @classmethod
    def instance(cls) -> Any:
        if cls not in cls._instances:
            cls._instances[cls] = cls()
        return cls._instances[cls]
