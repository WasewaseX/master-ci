"""Stockroom console (fixture: click consumed, boto3 never imported)."""

import click


@click.command()
def console():
    """Print the stockroom banner."""
    click.echo("stockroom console ready")
