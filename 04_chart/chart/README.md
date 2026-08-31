# Chart — Painter (04_chart)

`04_chart/chart/` — candlestick chart: engine→model→renderer→widgets→windows.

## Layers
| Layer | Kaam |
|---|---|
| `models` | `ChartModel(symbol, bars, timeframe, exchange)` + `CrosshairValue` |
| `engine` | `ChartEngine` — `DataLoaded` → sort → `ChartReady` |
| `renderer` | `CandleRenderer`, `TimeAxisRenderer`, `CrosshairRenderer`, `OverlayRenderer` — stateless QPainter |
| `widgets` | `CandleChartWidget` (zoom/pan/crosshair, pixmap cache), `WatchlistWidget`/`SymbolListWidget`, `ChartToolsToolbar` |
| `windows` | `ChartWindow` — splitter host (`tools|watchlist|download|chart`) |
| `theme` | `APP_PALETTE` + `APP_STYLE` (dark terminal, teal `#26a69a`) |
| `events` | `ChartReady`, `WindowRendered` |

## Interaction
Wheel = zoom (cursor anchor), drag = 2D pan (time+price), resize = repaint, price strip drag = vertical scale, double-click/reset = latest 150 bars.

## Kya Nahi Karega
Indicators/drawing/trading logic (`11_indicator`/`12_drawing`/`05_strategy`), SQL, bus subscribe — sirf `set_model(model)`.
