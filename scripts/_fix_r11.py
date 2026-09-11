"""One-shot: replace hardcoded D:\\VAYREN_STRATEGIES literals in bootstrap.py."""

from pathlib import Path

TARGET = Path("00_app/app/bootstrap/bootstrap.py")
src = TARGET.read_text(encoding="utf-8")
# The literal as it appears in source: r"D:\VAYREN_STRATEGIES"
LITERAL = 'r"D:\\VAYREN_STRATEGIES"'
print("literal occurrences:", src.count(LITERAL))

# All uses inside Bootstrap become self._strategy_dir.
replaced = src.replace(LITERAL, "self._strategy_dir")
TARGET.write_text(replaced, encoding="utf-8")
print("after replace, remaining:", replaced.count(LITERAL))
print("self._strategy_dir uses:", replaced.count("self._strategy_dir"))
