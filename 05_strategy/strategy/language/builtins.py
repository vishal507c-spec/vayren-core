"""Allowed primitives for VAYREN Strategy Language."""

# For highlighter and validator
KEYWORDS = {"strategy", "input", "if", "and", "or", "not", "True", "False"}

FUNCTIONS = {
    "strategy": {"args": 1, "desc": "strategy(name)"},
    "input": {"args": (1, 2), "desc": "input(default, label)"},
    "RSI": {"args": (1, 2), "desc": "RSI(period) or RSI(close, period)"},
    "ATR": {"args": (1, 2), "desc": "ATR(period) or ATR(close, period)"},
    "SMA": {"args": (1, 2), "desc": "SMA(period) or SMA(close, period)"},
    "EMA": {"args": (1, 2), "desc": "EMA(period) or EMA(close, period)"},
    "range": {"args": 1, "desc": "range(period) channel range"},
    "buy": {"args": 0, "desc": "buy() open long"},
    "sell": {"args": 0, "desc": "sell() open short"},
    "close_position": {"args": 0, "desc": "close_position()"},
    "stop_loss": {"args": 1, "desc": "stop_loss(price)"},
    "take_profit": {"args": 1, "desc": "take_profit(price)"},
    "time_exit": {"args": 1, "desc": 'time_exit("HH:MM")'},
    "exit_time": {"args": 1, "desc": 'exit_time("15:15") alias'},
    "is_new_day": {"args": 0, "desc": "is_new_day() true on first bar of new session"},
    "prev_day_close": {"args": 0, "desc": "prev_day_close() previous session close"},
    "after_time": {"args": 1, "desc": 'after_time("HH:MM") true when bar time >= HH:MM'},
    "plot": {"args": (2, 6), "desc": 'plot(value, title, style="line", ...)'},
    "line": {"args": (4, 6), "desc": 'line(x1,y1,x2,y2, ...)'},
    "label": {"args": (3, 6), "desc": 'label(x,y,text, ...)'},
    "marker": {"args": (3, 6), "desc": 'marker(x,y,text, ...)'},
}

VARIABLES = {"close", "open", "high", "low", "volume", "bar", "time"}

# For highlighter token types
HIGHLIGHT_KEYWORDS = ["strategy", "input", "if", "and", "or", "not"]
HIGHLIGHT_FUNCTIONS = [
    "RSI",
    "ATR",
    "SMA",
    "EMA",
    "range",
    "buy",
    "sell",
    "close_position",
    "stop_loss",
    "take_profit",
    "time_exit",
    "exit_time",
    "is_new_day",
    "prev_day_close",
    "after_time",
    "plot",
    "line",
    "label",
    "marker",
]  # noqa: E501
