from typing import Generic, Optional, TypeVar

T = TypeVar("T")


class Nullable(Generic[T]):
    """A value that may be explicitly null/missing.

    Useful for field-level null tracking distinct from Optional.
    """

    _NULL = object()

    def __init__(self, value: T | None = None) -> None:
        self._value: T | object = value if value is not None else self._NULL

    @classmethod
    def null(cls) -> "Nullable[T]":
        return cls()

    @classmethod
    def of(cls, value: T) -> "Nullable[T]":
        return cls(value)

    @property
    def is_null(self) -> bool:
        return self._value is self._NULL

    @property
    def is_present(self) -> bool:
        return self._value is not self._NULL

    def get(self) -> T:
        if self.is_null:
            msg = "Value is null"
            raise ValueError(msg)
        return self._value  # type: ignore[return-value]

    def get_or(self, default: T) -> T:
        return self._value if self._value is not self._NULL else default  # type: ignore[return-value]

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Nullable):
            return NotImplemented
        if self.is_null and other.is_null:
            return True
        return self._value == other._value

    def __str__(self) -> str:
        return "null" if self.is_null else str(self._value)

    def __repr__(self) -> str:
        return f"Nullable({self})"
