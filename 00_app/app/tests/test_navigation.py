"""Top navigation: all six sections reachable, active state honest."""

from app.ui.top_nav_bar import TopNavBar


def test_all_sections_present_and_market_default(qt_app) -> None:
    assert qt_app is not None
    nav = TopNavBar()
    assert set(nav._buttons) == {
        "MARKET",
        "STRATEGY LAB",
        "RESEARCH",
        "PORTFOLIO",
        "LIVE",
        "SYSTEM",
    }
    assert nav.active == "MARKET"


def test_research_and_portfolio_are_enabled(qt_app) -> None:
    assert qt_app is not None
    nav = TopNavBar()
    assert nav._buttons["RESEARCH"].isEnabled() is True
    assert nav._buttons["PORTFOLIO"].isEnabled() is True


def test_section_signals_fire(qt_app) -> None:
    assert qt_app is not None
    nav = TopNavBar()
    fired: list[str] = []
    nav.research_clicked.connect(lambda: fired.append("RESEARCH"))
    nav.portfolio_clicked.connect(lambda: fired.append("PORTFOLIO"))
    nav.live_clicked.connect(lambda: fired.append("LIVE"))
    nav._buttons["RESEARCH"].click()
    nav._buttons["PORTFOLIO"].click()
    nav._buttons["LIVE"].click()
    assert fired == ["RESEARCH", "PORTFOLIO", "LIVE"]


def test_set_active_tracks_selection(qt_app) -> None:
    assert qt_app is not None
    nav = TopNavBar()
    nav.set_active("PORTFOLIO")
    assert nav.active == "PORTFOLIO"
    nav.set_active("RESEARCH")
    assert nav.active == "RESEARCH"
    nav.set_active("BOGUS")
    assert nav.active == "RESEARCH"
