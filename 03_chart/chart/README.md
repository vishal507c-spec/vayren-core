# Chart — Painter

## 1. Ye kya hai?

`03_chart/chart/` — candlestick chart banane wala module. Data aata hai yahan, screen par chart dikhta hai.

## 2. Andar Kya Hai — Layers

```
engine     (data → chart model)
    ↓
models     (ChartModel — pure data)
    ↓
renderer   (painter — grid, candles, volume)
    ↓
widgets    (viewport — zoom, pan, resize)
    ↓
windows    (host — window kholta hai)
```

| Layer | Kaam |
|---|---|
| `models` | `ChartModel` — `symbol` + `bars` (ascending) |
| `engine` | `ChartEngine` — `DataLoaded` sunta hai → sort → `ChartReady` |
| `renderer` | `CandleRenderer` — stateless QPainter: grid, price labels, wicks, bodies, volume |
| `widgets` | `CandleChartWidget` — wheel=zoom, drag=pan, resize=repaint |
| `windows` | `ChartWindow` — widget host, title, `WindowRendered` |
| `events` | `ChartReady`, `WindowRendered` |

## 3. Story

Socho ek painter hai.

- Post office se envelope aaya: *"DataLoaded — SPY ke candles"* (engine)
- Painter ne candles ko line mein lagaya — sort kiya (engine)
- Model taiyar hua: `ChartModel` — *"ab mujhe paint karne do"* → `ChartReady`
- Window kholi, widget ko model diya
- Widget canvas par paint karta hai: wick, body, volume, grid
- Window boli: *"Ho gaya!"* → `WindowRendered`

## 4. Interaction — Zoom, Pan, Resize

| Action | Kya hota hai |
|---|---|
| Mouse wheel | Zoom — cursor jahan hai, wahan anchor |
| Left-drag | Pan — window ko ghaseeto |
| Window resize | Chart khud adjust — repaint |

Widget sirf viewport hai — data load nahi karta, SQL nahi jaanta, bus nahi chhunta.

## 5. Example — Rendering Flow

```
ChartWindow.on_chart_ready(event)
    ↓
widget.set_model(event.model)
    ↓
paintEvent
    ↓
visible bars → price range → CandleRenderer.paint(...)
```

## 6. Ye Kya Nahi Karega

| ❌ Nahi karega | Kyu |
|---|---|
| Indicators | `04_indicator` ka future kaam |
| Drawing tools | `05_drawing` ka kaam |
| Trading logic | Chart sirf dikhata hai |
| EventBus subscribe | Wiring sirf bootstrap mein |
| SQL | Data `02_market` se event mein aata hai |

## 7. Future

`04_indicator` aayega → indicator lines chart ke upar paint hongi — `renderer` mein naya function, widget viewport wahi rahega.

> Painter ka kaam: data lo, model banao, paint karo.
