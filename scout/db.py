"""SQLite: schema, anslutning och config-uttag för scrapers."""

import os
import sqlite3
from contextlib import contextmanager
from urllib.parse import urlencode

DATA_DIR = os.environ.get("DATA_DIR", "/data")
DB_PATH = os.path.join(DATA_DIR, "scout.db")


@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    with db() as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                email       TEXT NOT NULL UNIQUE,
                pw_hash     TEXT NOT NULL,
                interests   TEXT NOT NULL,
                fb_category TEXT NOT NULL DEFAULT 'electronics',
                enabled     INTEGER NOT NULL DEFAULT 1,
                created_at  TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS vinted_searches (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                search_text TEXT NOT NULL,
                price_to    REAL,
                enabled     INTEGER NOT NULL DEFAULT 1,
                UNIQUE (user_id, search_text)
            );
            CREATE TABLE IF NOT EXISTS fb_queries (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                query   TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                UNIQUE (user_id, query)
            );
            CREATE TABLE IF NOT EXISTS fb_locations (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name        TEXT NOT NULL,
                location_id TEXT NOT NULL,
                enabled     INTEGER NOT NULL DEFAULT 1,
                UNIQUE (user_id, location_id)
            );
        """)


# ---------------------------------------------------------------------------
# Config för scrapers (alla aktiva användare)
# ---------------------------------------------------------------------------

def active_users(c):
    return c.execute("SELECT id, email, interests, fb_category FROM users WHERE enabled = 1 ORDER BY id").fetchall()


def get_vinted_users() -> list[dict]:
    users = []
    with db() as c:
        for u in active_users(c):
            searches = []
            for r in c.execute(
                "SELECT search_text, price_to FROM vinted_searches WHERE user_id = ? AND enabled = 1 ORDER BY id",
                (u["id"],),
            ):
                params = {"search_text": r["search_text"]}
                if r["price_to"]:
                    params["price_to"] = r["price_to"]
                searches.append(params)
            if searches:
                users.append({"id": u["id"], "email": u["email"], "interests": u["interests"], "searches": searches})
    return users


def get_fb_users() -> list[dict]:
    users = []
    with db() as c:
        for u in active_users(c):
            locations = c.execute(
                "SELECT location_id FROM fb_locations WHERE user_id = ? AND enabled = 1 ORDER BY id", (u["id"],)
            ).fetchall()
            queries = [r["query"] for r in c.execute(
                "SELECT query FROM fb_queries WHERE user_id = ? AND enabled = 1 ORDER BY id", (u["id"],)
            )]
            if not locations or not queries:
                continue

            category = u["fb_category"].strip().strip("/")
            urls = []
            for loc in locations:
                base = f"https://www.facebook.com/marketplace/{loc['location_id']}/"
                if category:
                    base += f"{category}/"
                for q in queries:
                    params = {"sortBy": "creation_time_descend", "days_since_listed": "1", "query": q}
                    urls.append(f"{base}?{urlencode(params)}")
            users.append({"id": u["id"], "email": u["email"], "interests": u["interests"], "urls": urls})
    return users
