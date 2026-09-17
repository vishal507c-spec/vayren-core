"""Top navigation: Qt-shell sections reachable, active state honest.

PORTFOLIO is a visible nav item owned by the native Rust+Slint view
(constitution §3): clicking it shows the in-window Slint viewport and never
mounts the legacy Qt `portfolio_workspace.py` surface.
"""

from chart.widgets.candle_chart_widget import CandleChartWidget
from chart.widgets.timeframe_toolbar import TimeframeToolbar
from chart.widgets.tools_toolbar import ChartToolsToolbar
from chart.widgets.watchlist_widget import WatchlistWidget
from chart.windows.chart_window import ChartWindow
from core.event_bus.event_bus import EventBus
from PySide6.QtWidgets import QWidget

from app.services.slint_live_host import SlintLiveHost
from app.services.slint_portfolio_host import SlintPortfolioHost
from app.ui.live_workspace import LiveWorkspace
from app.ui.portfolio_workspace import PortfolioWorkspace
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


def test_portfolio_nav_routes_to_slint_shell(qt_app) -> None:
    """PORTFOLIO is visible but fires a signal (Qt mounts nothing itself)."""
    assert qt_app is not None
    nav = TopNavBar()
    assert nav._buttons["PORTFOLIO"].isEnabled() is True
    fired: list[str] = []
    nav.portfolio_clicked.connect(lambda: fired.append("PORTFOLIO"))
    nav._buttons["PORTFOLIO"].click()
    assert fired == ["PORTFOLIO"]


def test_qt_shell_mounts_no_portfolio_surface(qt_app) -> None:
    """The Qt window stack must contain no PortfolioWorkspace (Slint only)."""
    assert qt_app is not None
    window = ChartWindow(
        CandleChartWidget(),
        WatchlistWidget(),
        TimeframeToolbar(),
        ChartToolsToolbar(),
        EventBus(),
        nav=TopNavBar(),
        lab_workspace=QWidget(),
    )
    assert not hasattr(window, "show_portfolio")
    assert window.findChildren(PortfolioWorkspace) == []


def test_portfolio_route_shows_slint_host(qt_app) -> None:
    """PORTFOLIO nav shows the Slint viewport inside the same window."""
    assert qt_app is not None
    nav = TopNavBar()
    host = SlintPortfolioHost(state_provider=lambda: {})
    window = ChartWindow(
        CandleChartWidget(),
        WatchlistWidget(),
        TimeframeToolbar(),
        ChartToolsToolbar(),
        EventBus(),
        nav=nav,
        lab_workspace=QWidget(),
        slint_portfolio_host=host,
    )
    assert hasattr(window, "show_slint_portfolio")
    assert window.findChildren(PortfolioWorkspace) == []
    nav.portfolio_clicked.connect(window.show_slint_portfolio)
    nav._buttons["PORTFOLIO"].click()
    assert window._stack is not None
    assert window._stack.currentWidget() is host
    assert nav.active == "PORTFOLIO"


def test_research_live_and_portfolio_are_enabled(qt_app) -> None:
    assert qt_app is not None
    nav = TopNavBar()
    assert nav._buttons["RESEARCH"].isEnabled() is True
    assert nav._buttons["LIVE"].isEnabled() is True
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
    nav.set_active("LIVE")
    assert nav.active == "LIVE"
    nav.set_active("RESEARCH")
    assert nav.active == "RESEARCH"
    nav.set_active("BOGUS")
    assert nav.active == "RESEARCH"


def test_live_nav_routes_to_slint_shell(qt_app) -> None:
    """LIVE is visible but fires a signal (Qt mounts nothing itself)."""
    assert qt_app is not None
    nav = TopNavBar()
    assert nav._buttons["LIVE"].isEnabled() is True
    fired: list[str] = []
    nav.live_clicked.connect(lambda: fired.append("LIVE"))
    nav._buttons["LIVE"].click()
    assert fired == ["LIVE"]


def test_qt_shell_mounts_no_live_workspace(qt_app) -> None:
    """The Qt window stack must contain no LiveWorkspace (Slint only)."""
    assert qt_app is not None
    window = ChartWindow(
        CandleChartWidget(),
        WatchlistWidget(),
        TimeframeToolbar(),
        ChartToolsToolbar(),
        EventBus(),
        nav=TopNavBar(),
        lab_workspace=QWidget(),
    )
    assert window.findChildren(LiveWorkspace) == []


def test_live_route_shows_slint_host(qt_app) -> None:
    """LIVE nav shows the Slint viewport inside the same window."""
    assert qt_app is not None
    nav = TopNavBar()
    host = SlintLiveHost(state_provider=lambda: {})
    window = ChartWindow(
        CandleChartWidget(),
        WatchlistWidget(),
        TimeframeToolbar(),
        ChartToolsToolbar(),
        EventBus(),
        nav=nav,
        lab_workspace=QWidget(),
        slint_live_host=host,
    )
    assert hasattr(window, "show_slint_live")
    assert window.findChildren(LiveWorkspace) == []
    nav.live_clicked.connect(window.show_slint_live)
    nav._buttons["LIVE"].click()
    assert window._stack is not None
    assert window._stack.currentWidget() is host
    assert nav.active == "LIVE"


def test_system_nav_routes_to_slint_shell(qt_app) -> None:
    """SYSTEM is visible but fires a signal (Qt mounts nothing itself)."""
    assert qt_app is not None
    nav = TopNavBar()
    assert nav._buttons["SYSTEM"].isEnabled() is True
    fired: list[str] = []
    nav.system_clicked.connect(lambda: fired.append("SYSTEM"))
    nav._buttons["SYSTEM"].click()
    assert fired == ["SYSTEM"]


def test_qt_shell_mounts_no_brokers_workspace(qt_app) -> None:
    """The Qt window stack must contain no BrokersWorkspace (Slint only)."""
    assert qt_app is not None
    from app.ui.brokers_workspace import BrokersWorkspace

    window = ChartWindow(
        CandleChartWidget(),
        WatchlistWidget(),
        TimeframeToolbar(),
        ChartToolsToolbar(),
        EventBus(),
        nav=TopNavBar(),
        lab_workspace=QWidget(),
    )
    assert window.findChildren(BrokersWorkspace) == []


def test_system_route_shows_slint_host(qt_app) -> None:
    """SYSTEM nav shows the Slint viewport inside the same window."""
    assert qt_app is not None
    from app.services.slint_system_host import SlintSystemHost
    from app.ui.brokers_workspace import BrokersWorkspace

    nav = TopNavBar()
    host = SlintSystemHost(state_provider=lambda: {})
    window = ChartWindow(
        CandleChartWidget(),
        WatchlistWidget(),
        TimeframeToolbar(),
        ChartToolsToolbar(),
        EventBus(),
        nav=nav,
        lab_workspace=QWidget(),
        slint_system_host=host,
    )
    assert hasattr(window, "show_slint_system")
    assert window.findChildren(BrokersWorkspace) == []
    nav.system_clicked.connect(window.show_slint_system)
    nav._buttons["SYSTEM"].click()
    assert window._stack is not None
    assert window._stack.currentWidget() is host
    assert nav.active == "SYSTEM"


def test_market_route_shows_slint_host(qt_app) -> None:
    """MARKET nav shows the native Slint viewport inside the same window.

    The Qt market splitter must NOT remain production-mounted once a host is
    attached (constitution §3): `show_market` selects the viewport widget.
    """
    assert qt_app is not None
    from app.services.slint_market_host import SlintMarketHost

    nav = TopNavBar()
    host = SlintMarketHost(state_provider=lambda: {})
    window = ChartWindow(
        CandleChartWidget(),
        WatchlistWidget(),
        TimeframeToolbar(),
        ChartToolsToolbar(),
        EventBus(),
        nav=nav,
        lab_workspace=QWidget(),
    )
    splitter = window._splitter
    window.set_slint_market_host(host)
    nav.market_clicked.connect(window.show_market)
    nav._buttons["MARKET"].click()
    assert window._stack is not None
    assert window._stack.currentWidget() is host
    assert window._stack.indexOf(splitter) == -1
    assert nav.active == "MARKET"


def test_market_without_host_keeps_qt_splitter(qt_app) -> None:
    """No injected host: MARKET keeps the legacy Qt splitter mounted."""
    assert qt_app is not None
    nav = TopNavBar()
    window = ChartWindow(
        CandleChartWidget(),
        WatchlistWidget(),
        TimeframeToolbar(),
        ChartToolsToolbar(),
        EventBus(),
        nav=nav,
        lab_workspace=QWidget(),
    )
    window.show_market()
    assert window._stack is not None
    assert window._stack.currentWidget() is window._splitter
