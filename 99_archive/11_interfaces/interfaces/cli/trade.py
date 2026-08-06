"""Trade CLI commands."""

import click


@click.group()
def trade():
    """Trade commands."""
    pass


@trade.command()
@click.option("--symbol", required=True)
@click.option("--side", type=click.Choice(["buy", "sell"]), required=True)
@click.option("--quantity", type=int, required=True)
def place(symbol: str, side: str, quantity: int) -> None:
    """Place a trade order."""
    click.echo(f"Placing {side} order for {quantity} shares of {symbol}")


@trade.command()
@click.option("--symbol", required=True)
def status(symbol: str) -> None:
    """Check position status."""
    click.echo(f"Position for {symbol}: 0 shares")
