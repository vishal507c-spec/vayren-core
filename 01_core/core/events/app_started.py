"""AppStarted event — published once the application is bootstrapped."""

from dataclasses import dataclass

from core.events.event import Event


@dataclass(frozen=True)
class AppStarted(Event):
    """The application has been fully bootstrapped and is starting."""
