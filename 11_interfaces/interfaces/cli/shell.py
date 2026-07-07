"""Interactive CLI shell for Vayren Core."""

import sys


def main() -> None:
    print("Vayren Core Shell")
    print("Type 'help' for commands")
    while True:
        try:
            line = input("vayren> ").strip()
            if not line:
                continue
            if line == "exit":
                break
            elif line == "help":
                print("Commands: help, status, backtest, exit")
            elif line == "status":
                print("Engine status: idle")
            else:
                print(f"Unknown command: {line}")
        except (EOFError, KeyboardInterrupt):
            break
    print("Goodbye")


if __name__ == "__main__":
    main()
