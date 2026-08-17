# Chart â€” Painter

## 1. Ye kya hai?

`04_chart/chart/` â€” candlestick chart banane wala module. Data aata hai yahan, screen par chart dikhta hai.

## 2. Andar Kya Hai â€” Layers

```
engine     (data â†’ chart model)
    â†“
models     (ChartModel â€” pure data)
    â†“
renderer   (painter â€” grid, candles, volume)
    â†“
widgets    (viewport â€” zoom, pan, resize)
    â†“
windows    (host â€” window kholta hai)
```

| Layer | Kaam |
|---|---|
| `models` | `ChartModel` â€” `symbol` + `bars` (ascending) |
| `engine` | `ChartEngine` â€” `DataLoaded` sunta hai â†’ sort â†’ `ChartReady` |
| `renderer` | `CandleRenderer` â€” stateless QPainter: grid, price labels, wicks, bodies, volume |
| `widgets` | `CandleChartWidget` â€” wheel=zoom, drag=pan, resize=repaint |
| `windows` | `ChartWindow` â€” widget host, title, `WindowRendered` |
| `events` | `ChartReady`, `WindowRendered` |

## 3. Story

Socho ek painter hai.

- Post office se envelope aaya: *"DataLoaded â€” SPY ke candles"* (engine)
- Painter ne candles ko line mein lagaya â€” sort kiya (engine)
- Model taiyar hua: `ChartModel` â€” *"ab mujhe paint karne do"* â†’ `ChartReady`
- Window kholi, widget ko model diya
- Widget canvas par paint karta hai: wick, body, volume, grid
- Window boli: *"Ho gaya!"* â†’ `WindowRendered`

## 4. Interaction â€” Zoom, Pan, Resize

| Action | Kya hota hai |
|---|---|
| Mouse wheel | Zoom â€” cursor jahan hai, wahan anchor |
| Left-drag | Pan â€” window ko ghaseeto |
| Window resize | Chart khud adjust â€” repaint |

Widget sirf viewport hai â€” data load nahi karta, SQL nahi jaanta, bus nahi chhunta.

## 5. Example â€” Rendering Flow

```
ChartWindow.on_chart_ready(event)
    â†“
widget.set_model(event.model)
    â†“
paintEvent
    â†“
visible bars â†’ price range â†’ CandleRenderer.paint(...)
```

## 6. Ye Kya Nahi Karega

| âŒ Nahi karega | Kyu |
|---|---|
| Indicators | `11_indicator` ka future kaam |
| Drawing tools | `12_drawing` ka kaam |
| Trading logic | Chart sirf dikhata hai |
| EventBus subscribe | Wiring sirf bootstrap mein |
| SQL | Data `03_market` se event mein aata hai |

## 7. Future

`11_indicator` aayega â†’ indicator lines chart ke upar paint hongi â€” `renderer` mein naya function, widget viewport wahi rahega.

> Painter ka kaam: data lo, model banao, paint karo.

