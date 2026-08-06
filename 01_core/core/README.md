# Core — Post Office

## 1. Ye kya hai?

`01_core/core/` — platform ki **neev**. Sab kuch isi par khada hai. Ye kisi par depend nahi karta.

## 2. Andar Kya Hai

| Package | Kaam |
|---|---|
| `event_bus` | Post Office — sab messages yahin se guzarte hain |
| `events` | `Event` base class + `AppStarted` |
| `logger` | Logging setup (`configure_logging`, `get_logger`) |
| `registry` | Services ki list (`register`, `get`, ...) |

## 3. Story — Event Bus Ka Post Office

Socho ek gaon ka post office.

- Market ko chart ko kuch batana hai
- Wo seedha chart ke ghar nahi jata
- Wo post office jata hai, envelope deta hai
- Post office envelope par naam padhta hai (`DataLoaded`)
- Us naam ke jo subscribe hai — usko envelope pahunch jata hai

```
Market ──envelope──► EventBus ──envelope──► Chart
```

Seedha jana — mana. Post office se jana — sahi.

## 4. Diagram — EventBus Ka Kaam

```
subscribe(type, handler)   → "mujhe DataLoaded bhejo"
publish(event)             → "ye envelope bhejo"
unsubscribe(type, handler) → "ab mat bhejo"
clear()                    → "saare envelopes band"
```

Rules:

```
Envelope par naam likha hai (type)
Exact type match hota hai — subclass nahi
Handler fail ho → log likho, aage ka kaam chalta rahe
```

## 5. Example — Bus Kaise Use Hota Hai

```python
bus = EventBus()
bus.subscribe(DataLoaded, engine.on_data_loaded)   # bootstrap mein
bus.publish(DataLoaded(symbol="SPY", bars=bars))   # market mein
```

- Subscribe sirf `00_app/app/bootstrap/bootstrap.py` mein
- Publish koi bhi service kar sakti hai
- Widgets dono nahi karte

## 6. Ye Kya Nahi Karega

| ❌ Nahi karega | Kyu |
|---|---|
| Trading logic | Neev hai, kaam nahi |
| SQL | Post office message hi banata hai |
| Async/replay/queues | Synchronous, simple, predictable |
| Business logic | Sirf messages |

## 7. Future

Naya module aayega → naye events `core.events` mein nahi, **us module ke** `events/` mein.

> Post Office yaad rakho: sab messages yahan se guzarte hain.
