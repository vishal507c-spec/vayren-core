"""Allowed primitives for VAYREN Strategy Language."""

# For highlighter and validator
KEYWORDS = {"strategy", "input", "if", "and", "or", "not", "True", "False"}

FUNCTIONS = {
    "strategy": {"args": 1, "desc": "strategy(name)"},
    "input": {"args": (1, 2), "desc": "input(default, label)"},
    "RSI": {"args": 1, "desc": "RSI(period)"},
    "ATR": {"args": 1, "desc": "ATR(period)"},
    "SMA": {"args": 1, "desc": "SMA(period)"},
    "EMA": {"args": 1, "desc": "EMA(period)"},
    "range": {"args": 1, "desc": "range(period) channel range"},
    "buy": {"args": 0, "desc": "buy() open long"},
    "sell": {"args": 0, "desc": "sell() open short"},
    "close_position": {"args": 0, "desc": "close_position()"},
    "stop_loss": {"args": 1, "desc": "stop_loss(price)"},
    "take_profit": {"args": 1, "desc": "take_profit(price)"},
    "time_exit": {"args": 1, "desc": 'time_exit("HH:MM")'},
    "exit_time": {"args": 1, "desc": 'exit_time("15:15") alias'},
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
]  # noqa: E501
