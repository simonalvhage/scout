#!/usr/bin/env python3
"""
Scout — GUI + schemalagd scraping av Vinted och Facebook Marketplace.

Environment variables: se docker-compose.yml
"""

import hashlib
import hmac
import html
import os
import re
import secrets
import time
import logging
from contextlib import asynccontextmanager
from urllib.parse import quote

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

from scout import scheduler
from scout.db import db, init_db

SESSION_SECRET = os.environ["SESSION_SECRET"]
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "true").lower() == "true"
MAX_USERS = int(os.environ.get("MAX_USERS", "50"))
MAX_TERMS = int(os.environ.get("MAX_TERMS", "20"))

DEFAULT_INTERESTS = """Jag gillar:
- ...

Jag är INTE intresserad av: ..."""

DEFAULT_FB_LOCATIONS = [
    ("Sjöbo", "110093335684862"),
    ("Lund", "108679122496232"),
    ("Malmö", "110837332277562"),
    ("Ystad", "115014335181092"),
]

EMAIL_RE = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")

# ---------------------------------------------------------------------------
# Passwords (stdlib scrypt)
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_hex, digest_hex = stored.split("$")
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1)
        return hmac.compare_digest(digest.hex(), digest_hex)
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# App + auth
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    scheduler.start()
    yield


app = FastAPI(title="Scout", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    same_site="lax",
    https_only=COOKIE_SECURE,
    max_age=60 * 60 * 24 * 30,
)


class LoginRequired(Exception):
    pass


@app.exception_handler(LoginRequired)
async def _login_redirect(request: Request, exc: LoginRequired):
    return RedirectResponse("/login", status_code=303)


def current_user(request: Request) -> int:
    user_id = request.session.get("user_id")
    if not user_id:
        raise LoginRequired()
    with db() as c:
        if not c.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone():
            request.session.clear()
            raise LoginRequired()
    return user_id


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------

e = html.escape

CSS = """
body { font-family: -apple-system, sans-serif; max-width: 860px; margin: 0 auto; padding: 16px; color: #1e293b; }
h1 { font-size: 22px; } h2 { font-size: 17px; margin-top: 32px; border-bottom: 1px solid #e2e8f0; padding-bottom: 6px; }
input[type=text], input[type=email], input[type=password], input[type=number], textarea { padding: 6px 8px; border: 1px solid #cbd5e1; border-radius: 4px; font: inherit; box-sizing: border-box; }
textarea { width: 100%; min-height: 220px; }
button { padding: 6px 12px; border: 0; border-radius: 4px; background: #2563eb; color: white; cursor: pointer; font: inherit; }
button.secondary { background: #94a3b8; } button.danger { background: #ef4444; }
table { width: 100%; border-collapse: collapse; } td { padding: 6px 4px; border-bottom: 1px solid #f1f5f9; }
.off { color: #94a3b8; text-decoration: line-through; }
.row { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-top: 8px; }
.stack { display: flex; flex-direction: column; gap: 8px; max-width: 320px; }
.top { display: flex; justify-content: space-between; align-items: center; gap: 8px; flex-wrap: wrap; }
.hint { color: #64748b; font-size: 13px; } .err { color: #ef4444; }
form.inline { display: inline; }
"""


def page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(f"""<!doctype html><html lang="sv"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title><style>{CSS}</style></head><body>{body}</body></html>""")


def list_rows(rows, table: str, label_fn) -> str:
    out = ""
    for r in rows:
        cls = "" if r["enabled"] else "off"
        toggle = "Pausa" if r["enabled"] else "Aktivera"
        out += f"""<tr>
            <td class="{cls}">{label_fn(r)}</td>
            <td style="text-align:right; white-space:nowrap;">
                <form class="inline" method="post" action="/{table}/{r['id']}/toggle"><button class="secondary">{toggle}</button></form>
                <form class="inline" method="post" action="/{table}/{r['id']}/delete"><button class="danger">Ta bort</button></form>
            </td></tr>"""
    return f"<table>{out}</table>" if out else '<p class="hint">Inga ännu.</p>'


def back(error: str = ""):
    return RedirectResponse(f"/?error={quote(error)}" if error else "/", status_code=303)


def register_error(msg: str):
    return RedirectResponse(f"/register?error={quote(msg)}", status_code=303)


# ---------------------------------------------------------------------------
# Register / login
# ---------------------------------------------------------------------------

@app.get("/register", response_class=HTMLResponse)
def register_form(error: str = ""):
    msg = f'<p class="err">{e(error)}</p>' if error else ""
    return page("Skapa konto", f"""
        <h1>Scout — skapa konto</h1>{msg}
        <p class="hint">Rapporter med fynd mejlas till din e-postadress.</p>
        <form method="post" action="/register" class="stack">
            <input type="email" name="email" placeholder="E-post" required>
            <input type="password" name="password" placeholder="Lösenord (minst 8 tecken)" minlength="8" required>
            <button>Skapa konto</button>
        </form>
        <p><a href="/login">Har du redan ett konto? Logga in</a></p>""")


@app.post("/register")
def register(request: Request, email: str = Form(...), password: str = Form(...)):
    email = email.strip().lower()
    if not EMAIL_RE.match(email):
        return register_error("Ogiltig e-postadress")
    if len(password) < 8:
        return register_error("Lösenordet måste vara minst 8 tecken")

    with db() as c:
        if c.execute("SELECT COUNT(*) FROM users").fetchone()[0] >= MAX_USERS:
            return register_error("Max antal konton uppnått")
        if c.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
            return register_error("E-postadressen finns redan")
        cur = c.execute(
            "INSERT INTO users (email, pw_hash, interests) VALUES (?, ?, ?)",
            (email, hash_password(password), DEFAULT_INTERESTS),
        )
        user_id = cur.lastrowid
        c.executemany(
            "INSERT INTO fb_locations (user_id, name, location_id) VALUES (?, ?, ?)",
            [(user_id, n, lid) for n, lid in DEFAULT_FB_LOCATIONS],
        )

    request.session.clear()
    request.session["user_id"] = user_id
    return RedirectResponse("/", status_code=303)


@app.get("/login", response_class=HTMLResponse)
def login_form(error: str = ""):
    msg = '<p class="err">Fel e-post eller lösenord</p>' if error else ""
    return page("Logga in", f"""
        <h1>Scout — logga in</h1>{msg}
        <form method="post" action="/login" class="stack">
            <input type="email" name="email" placeholder="E-post" autofocus required>
            <input type="password" name="password" placeholder="Lösenord" required>
            <button>Logga in</button>
        </form>
        <p><a href="/register">Skapa konto</a></p>""")


@app.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...)):
    with db() as c:
        row = c.execute(
            "SELECT id, pw_hash FROM users WHERE email = ?", (email.strip().lower(),)
        ).fetchone()
    if row and verify_password(password, row["pw_hash"]):
        request.session.clear()
        request.session["user_id"] = row["id"]
        return RedirectResponse("/", status_code=303)
    time.sleep(1)  # enkel broms mot brute force
    return RedirectResponse("/login?error=1", status_code=303)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.post("/account/delete")
def delete_account(request: Request, password: str = Form(...), user_id: int = Depends(current_user)):
    with db() as c:
        row = c.execute("SELECT pw_hash FROM users WHERE id = ?", (user_id,)).fetchone()
        if not verify_password(password, row["pw_hash"]):
            return back("Fel lösenord, kontot raderades inte")
        c.execute("DELETE FROM users WHERE id = ?", (user_id,))
    request.session.clear()
    return RedirectResponse("/register", status_code=303)


# ---------------------------------------------------------------------------
# Main page
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def index(error: str = "", user_id: int = Depends(current_user)):
    with db() as c:
        u = c.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        vinted = c.execute("SELECT * FROM vinted_searches WHERE user_id = ? ORDER BY search_text", (user_id,)).fetchall()
        fbq = c.execute("SELECT * FROM fb_queries WHERE user_id = ? ORDER BY query", (user_id,)).fetchall()
        fbl = c.execute("SELECT * FROM fb_locations WHERE user_id = ? ORDER BY name", (user_id,)).fetchall()

    msg = f'<p class="err">{e(error)}</p>' if error else ""
    vinted_html = list_rows(
        vinted, "vinted_searches",
        lambda r: e(r["search_text"]) + (f' <span class="hint">(max {r["price_to"]:g} kr)</span>' if r["price_to"] else ""),
    )
    fbq_html = list_rows(fbq, "fb_queries", lambda r: e(r["query"]))
    fbl_html = list_rows(fbl, "fb_locations", lambda r: f'{e(r["name"])} <span class="hint">{e(r["location_id"])}</span>')
    paused = "" if u["enabled"] else '<p class="err">Bevakningen är pausad, inga mejl skickas.</p>'
    toggle_label = "Pausa all bevakning" if u["enabled"] else "Starta bevakning"

    return page("Scout", f"""
    <div class="top"><h1>Scout</h1>
        <div class="row">
            <span class="hint">{e(u['email'])}</span>
            <form method="post" action="/logout"><button class="secondary">Logga ut</button></form>
        </div></div>
    {msg}{paused}
    <p class="hint">{e(scheduler.status_text())}</p>
    <form method="post" action="/account/toggle"><button class="secondary">{toggle_label}</button></form>

    <h2>Intressen (skickas till AI:n)</h2>
    <p class="hint">Gäller både Vinted och Marketplace. Beskriv vad du gillar och vad du inte vill se.</p>
    <form method="post" action="/interests">
        <textarea name="value">{e(u['interests'])}</textarea>
        <div class="row"><button>Spara</button></div>
    </form>

    <h2>Vinted — sökord</h2>
    {vinted_html}
    <form method="post" action="/vinted_searches" class="row">
        <input type="text" name="search_text" placeholder="t.ex. ubiquiti" required maxlength="100">
        <input type="number" name="price_to" placeholder="Maxpris (valfritt)" min="0" step="1">
        <button>Lägg till</button>
    </form>

    <h2>Marketplace — sökord</h2>
    <p class="hint">Varje sökord körs mot varje aktiv plats. Inga sökord = ingen Marketplace-bevakning.</p>
    {fbq_html}
    <form method="post" action="/fb_queries" class="row">
        <input type="text" name="query" placeholder="t.ex. ram" required maxlength="100">
        <button>Lägg till</button>
    </form>

    <h2>Marketplace — platser</h2>
    <p class="hint">Plats-ID är siffrorna i URL:en, facebook.com/marketplace/<b>110093335684862</b>/…</p>
    {fbl_html}
    <form method="post" action="/fb_locations" class="row">
        <input type="text" name="name" placeholder="Namn" required maxlength="50">
        <input type="text" name="location_id" placeholder="Plats-ID" pattern="[0-9]+" required maxlength="30">
        <button>Lägg till</button>
    </form>

    <h2>Marketplace — kategori</h2>
    <form method="post" action="/fb_category" class="row">
        <input type="text" name="value" value="{e(u['fb_category'])}" maxlength="50">
        <button>Spara</button>
    </form>
    <p class="hint">Sista delen av URL:en, t.ex. electronics. Tom = alla kategorier.</p>

    <h2>Radera konto</h2>
    <form method="post" action="/account/delete" class="row">
        <input type="password" name="password" placeholder="Bekräfta med lösenord" required>
        <button class="danger">Radera mitt konto</button>
    </form>
    """)


# ---------------------------------------------------------------------------
# Mutations (always scoped to the logged-in user)
# ---------------------------------------------------------------------------

TABLES = {"vinted_searches", "fb_queries", "fb_locations"}


def over_limit(c, table: str, user_id: int) -> bool:
    return c.execute(f"SELECT COUNT(*) FROM {table} WHERE user_id = ?", (user_id,)).fetchone()[0] >= MAX_TERMS


@app.post("/account/toggle")
def toggle_account(user_id: int = Depends(current_user)):
    with db() as c:
        c.execute("UPDATE users SET enabled = 1 - enabled WHERE id = ?", (user_id,))
    return back()


@app.post("/interests")
def set_interests(value: str = Form(""), user_id: int = Depends(current_user)):
    with db() as c:
        c.execute("UPDATE users SET interests = ? WHERE id = ?", (value.strip()[:5000], user_id))
    return back()


@app.post("/fb_category")
def set_fb_category(value: str = Form(""), user_id: int = Depends(current_user)):
    value = value.strip().strip("/")
    if value and not re.fullmatch(r"[a-z0-9\-]+", value):
        return back("Ogiltig kategori")
    with db() as c:
        c.execute("UPDATE users SET fb_category = ? WHERE id = ?", (value, user_id))
    return back()


@app.post("/vinted_searches")
def add_vinted(search_text: str = Form(...), price_to: str = Form(""), user_id: int = Depends(current_user)):
    try:
        price = float(price_to) if price_to.strip() else None
    except ValueError:
        return back("Ogiltigt maxpris")
    with db() as c:
        if over_limit(c, "vinted_searches", user_id):
            return back(f"Max {MAX_TERMS} sökord")
        c.execute(
            "INSERT OR IGNORE INTO vinted_searches (user_id, search_text, price_to) VALUES (?, ?, ?)",
            (user_id, search_text.strip()[:100], price),
        )
    return back()


@app.post("/fb_queries")
def add_fb_query(query: str = Form(...), user_id: int = Depends(current_user)):
    with db() as c:
        if over_limit(c, "fb_queries", user_id):
            return back(f"Max {MAX_TERMS} sökord")
        c.execute(
            "INSERT OR IGNORE INTO fb_queries (user_id, query) VALUES (?, ?)",
            (user_id, query.strip()[:100]),
        )
    return back()


@app.post("/fb_locations")
def add_fb_location(name: str = Form(...), location_id: str = Form(...), user_id: int = Depends(current_user)):
    if not location_id.strip().isdigit():
        return back("Plats-ID måste vara siffror")
    with db() as c:
        if over_limit(c, "fb_locations", user_id):
            return back(f"Max {MAX_TERMS} platser")
        c.execute(
            "INSERT OR IGNORE INTO fb_locations (user_id, name, location_id) VALUES (?, ?, ?)",
            (user_id, name.strip()[:50], location_id.strip()),
        )
    return back()


@app.post("/{table}/{row_id}/toggle")
def toggle(table: str, row_id: int, user_id: int = Depends(current_user)):
    if table not in TABLES:
        raise HTTPException(404)
    with db() as c:
        c.execute(f"UPDATE {table} SET enabled = 1 - enabled WHERE id = ? AND user_id = ?", (row_id, user_id))
    return back()


@app.post("/{table}/{row_id}/delete")
def delete(table: str, row_id: int, user_id: int = Depends(current_user)):
    if table not in TABLES:
        raise HTTPException(404)
    with db() as c:
        c.execute(f"DELETE FROM {table} WHERE id = ? AND user_id = ?", (row_id, user_id))
    return back()


@app.get("/health")
def health():
    return {"status": "ok"}
