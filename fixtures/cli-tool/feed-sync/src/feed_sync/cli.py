"""feed-sync command line."""

import os
import sys

import click
import requests
import sqlalchemy
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from .models import Base, Entry


def _engine():
    return create_engine(os.environ.get("DATABASE_URL", "sqlite:///feed.db"))


@click.group()
def cli():
    """Fetch a feed, store entries, publish a digest."""


@cli.command()
def fetch():
    """Pull the feed and store new entries."""
    feed_url = os.environ.get("FEED_URL")
    if not feed_url:
        raise SystemExit("FEED_URL is not set")

    response = requests.get(feed_url, timeout=30)
    response.raise_for_status()
    payload = response.json()

    engine = _engine()
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        for item in payload.get("items", []):
            session.add(Entry(title=item["title"], link=item["link"]))
        session.commit()

    click.echo(f"stored {len(payload.get('items', []))} entries")


@cli.command()
def digest():
    """Build a digest from stored entries."""
    engine = _engine()
    with Session(engine) as session:
        rows = session.execute(select(Entry)).scalars().all()

    lines = [f"- {row.title} ({row.link})" for row in rows]
    text = "\n".join(lines)
    with open("digest.md", "w", encoding="utf-8") as handle:
        handle.write(text)
    click.echo(f"wrote digest.md with {len(rows)} entries")


@cli.command()
def publish():
    """Send the digest to the configured webhook."""
    webhook = os.environ.get("WEBHOOK_URL")
    if not webhook:
        raise SystemExit("WEBHOOK_URL is not set")

    with open("digest.md", "r", encoding="utf-8") as handle:
        body = handle.read()

    response = requests.post(webhook, json={"content": body}, timeout=30)
    response.raise_for_status()
    click.echo("digest published")


@cli.command()
def status():
    """Show how many entries are stored."""
    engine = _engine()
    with Session(engine) as session:
        count = session.execute(select(sqlalchemy.func.count(Entry.id))).scalar()
    click.echo(f"{count} entries stored")


if __name__ == "__main__":
    sys.exit(cli())
