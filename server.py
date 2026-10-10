from __future__ import annotations

import json
import hashlib
import hmac
import mimetypes
import os
import re
import secrets
import sqlite3
import threading
import time
import unicodedata
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, unquote, urlencode, urlsplit
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
HOST = "127.0.0.1"
PORT = 8000
HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", "8000"))
GAME_PAGE_SIZE = 6
MAX_GAME_PAGE_SIZE = 10
CACHE_SECONDS = 300
REQUEST_TIMEOUT_SECONDS = 12
AUTH_DB_PATH = ROOT / "arcade-vault.sqlite3"
PASSWORD_ITERATIONS = 310_000
SESSION_SECONDS = 12 * 60 * 60
REMEMBERED_SESSION_SECONDS = 30 * 24 * 60 * 60
MAX_REQUEST_BODY = 64 * 1024
SESSION_COOKIE = "arcade_vault_session"
EPIC_GAMES_URL = (
    "https://store-site-backend-static.ak.epicgames.com/freeGamesPromotions"
    "?locale=da-DK&country=DK&allowCountries=DK"
)

RAWG_GENRES = {
    "action": "action",
    "adventure": "adventure",
    "strategy": "strategy",
    "puzzle": "puzzle",
    "rpg": "role-playing-games-rpg",
    "simulation": "simulation",
    "sport": "sports",
    "racing": "racing",
    "casual": "casual",
    "educational": "educational",
    "multiplayer": "massively-multiplayer",
}

RAWG_TAGS = {
    "christian": "1195",
    "horror": "16",
    "romance": "217",
    "survival": "1",
    "comedy": "123",
    "drama": "209",
    "mecha": "4809",
    "music": "136",
    "mystery": "117",
    "psychological": "285",
    "sci-fi": "32",
    "slice of life": "924",
    "supernatural": "279",
    "thriller": "183",
    "fantasy": "64",
    "islam": "16815",
    "judaism": "39381",
    "buddhism": "3817",
    "hinduism": "19147",
    "greek mythology": "38489",
    "norse mythology": "7737",
    "egyptian mythology": "36578",
    "japanese mythology": "48265",
    "indian mythology": "60124",
    "chinese mythology": "90202",
}


class UpstreamAPIError(Exception):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def load_env_file() -> None:
    env_path = ROOT / ".env"
    if not env_path.is_file():
        return

    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip().strip("\"'")
        if name and name not in os.environ:
            os.environ[name] = value


def normalize_title(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.casefold())
    without_marks = "".join(char for char in normalized if not unicodedata.combining(char))
    return "".join(char for char in without_marks if char.isalnum())


def rawg_query(
    category: str, page: int = 1, page_size: int = GAME_PAGE_SIZE
) -> dict[str, str]:
    normalized_category = category.strip().casefold()
    query: dict[str, str] = {
        "ordering": "-added" if not normalized_category else "-rating",
        "page_size": str(page_size),
        "page": str(page),
    }
    genre_slug = RAWG_GENRES.get(normalized_category)
    if genre_slug:
        query["genres"] = genre_slug
    elif tag_id := RAWG_TAGS.get(normalized_category):
        query["tags"] = tag_id
    elif normalized_category:
        query["search"] = category.strip()
    return query


class JsonCache:
    def __init__(self) -> None:
        self._items: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get_or_fetch(self, url: str) -> Any:
        now = time.monotonic()
        with self._lock:
            cached = self._items.get(url)
            if cached and cached[0] > now:
                return cached[1]

        value = fetch_json(url)
        with self._lock:
            self._items[url] = (time.monotonic() + CACHE_SECONDS, value)
        return value


cache = JsonCache()


@contextmanager
def auth_connection() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(AUTH_DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_auth_database() -> None:
    with auth_connection() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                username TEXT NOT NULL,
                username_key TEXT NOT NULL UNIQUE,
                email TEXT NOT NULL,
                email_key TEXT NOT NULL UNIQUE,
                password_salt BLOB NOT NULL,
                password_hash BLOB NOT NULL,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS sessions_user_id_idx ON sessions(user_id);
            CREATE TABLE IF NOT EXISTS favorites (
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                game_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                cover TEXT NOT NULL DEFAULT '',
                created_at INTEGER NOT NULL,
                PRIMARY KEY (user_id, game_id)
            );
            """
        )


def hash_password(password: str, salt: bytes | None = None) -> tuple[bytes, bytes]:
    password_salt = salt or secrets.token_bytes(16)
    password_hash = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        password_salt,
        PASSWORD_ITERATIONS,
    )
    return password_salt, password_hash


def verify_password(password: str, salt: bytes, expected_hash: bytes) -> bool:
    _, actual_hash = hash_password(password, salt)
    return hmac.compare_digest(actual_hash, expected_hash)


def create_session(user_id: int, remember: bool) -> tuple[str, int]:
    token = secrets.token_urlsafe(32)
    expires_at = int(time.time()) + (
        REMEMBERED_SESSION_SECONDS if remember else SESSION_SECONDS
    )
    with auth_connection() as connection:
        connection.execute(
            "DELETE FROM sessions WHERE expires_at <= ?",
            (int(time.time()),),
        )
        connection.execute(
            "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
            (hashlib.sha256(token.encode("ascii")).hexdigest(), user_id, expires_at),
        )
    return token, expires_at


def get_session_user(token: str | None) -> sqlite3.Row | None:
    if not token:
        return None
    token_hash = hashlib.sha256(token.encode("ascii", errors="ignore")).hexdigest()
    with auth_connection() as connection:
        return connection.execute(
            """
            SELECT users.id, users.name, users.username, users.email
            FROM sessions
            JOIN users ON users.id = sessions.user_id
            WHERE sessions.token_hash = ? AND sessions.expires_at > ?
            """,
            (token_hash, int(time.time())),
        ).fetchone()


def safe_profile(user: sqlite3.Row) -> dict[str, Any]:
    with auth_connection() as connection:
        favorites = connection.execute(
            """
            SELECT game_id, title, cover
            FROM favorites
            WHERE user_id = ?
            ORDER BY created_at DESC, title COLLATE NOCASE
            """,
            (user["id"],),
        ).fetchall()
    return {
        "id": user["id"],
        "name": user["name"],
        "username": user["username"],
        "email": user["email"],
        "favorites": [
            {"id": item["game_id"], "title": item["title"], "cover": item["cover"]}
            for item in favorites
        ],
    }


def fetch_json(url: str) -> Any:
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "ArcadeVault/1.0",
        },
    )
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError) as error:
        if isinstance(error, HTTPError):
            detail = f"HTTP {error.code}"
            status_code = error.code
        elif isinstance(error, URLError):
            detail = str(error.reason)
            status_code = None
        else:
            detail = str(error)
            status_code = None
        raise UpstreamAPIError(detail, status_code=status_code) from error


def rawg_url(path: str, params: dict[str, str] | None = None) -> str:
    api_key = os.environ.get("RAWG_API_KEY", "").strip()
    if not api_key or api_key.lower() in {"your_rawg_api_key", "replace_me"}:
        raise ValueError(
            "RAWG_API_KEY is missing. Add your RAWG API key to the .env file."
        )

    query = {"key": api_key}
    if params:
        query.update(params)
    return f"https://api.rawg.io/api/{path}?{urlencode(query)}"


def get_rawg_description(game_id: int) -> str:
    details = cache.get_or_fetch(rawg_url(f"games/{game_id}"))
    if not isinstance(details, dict):
        return ""
    description = details.get("description_raw")
    return description.strip() if isinstance(description, str) else ""


def get_game_details(game_id: int) -> dict[str, Any]:
    details = cache.get_or_fetch(rawg_url(f"games/{game_id}"))
    if not isinstance(details, dict):
        raise UpstreamAPIError("RAWG returned an unexpected game response.")

    def names(items: Any) -> list[str]:
        if not isinstance(items, list):
            return []
        return [
            item["name"]
            for item in items
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        ]

    platforms = details.get("platforms", [])
    platform_names = [
        platform["platform"]["name"]
        for platform in platforms
        if isinstance(platform, dict)
        and isinstance(platform.get("platform"), dict)
        and isinstance(platform["platform"].get("name"), str)
    ] if isinstance(platforms, list) else []
    esrb_rating = details.get("esrb_rating")

    result = {
        "id": game_id,
        "title": str(details.get("name", "Untitled game")),
        "cover": details.get("background_image") or "",
        "description": details.get("description_raw") or "",
        "rating": details.get("rating"),
        "metacritic": details.get("metacritic"),
        "released": details.get("released") or "",
        "genres": names(details.get("genres")),
        "developers": names(details.get("developers")),
        "publishers": names(details.get("publishers")),
        "platforms": platform_names,
        "website": details.get("website") or "",
        "esrb_rating": esrb_rating.get("name", "") if isinstance(esrb_rating, dict) else "",
    }
    try:
        epic_match = next(
            (
                game
                for game in get_epic_games()
                if normalize_title(game["title"]) == normalize_title(result["title"])
            ),
            None,
        )
    except UpstreamAPIError as error:
        result["warnings"] = [f"Epic Games Store details unavailable: {error}"]
        epic_match = None
    if epic_match:
        result["epic"] = {"url": epic_match["url"], "price": epic_match["price"]}
        if not result["description"]:
            result["description"] = epic_match["description"]
    return result


def get_epic_games() -> list[dict[str, Any]]:
    response = cache.get_or_fetch(EPIC_GAMES_URL)
    try:
        elements = response["data"]["Catalog"]["searchStore"]["elements"]
    except (KeyError, TypeError) as error:
        raise UpstreamAPIError("Epic returned an unexpected games response.") from error
    if not isinstance(elements, list):
        raise UpstreamAPIError("Epic returned an unexpected games response.")

    games: list[dict[str, Any]] = []
    seen_titles: set[str] = set()
    for item in elements:
        if not isinstance(item, dict) or not isinstance(item.get("title"), str):
            continue
        if item.get("offerType") not in {None, "BASE_GAME"}:
            continue
        normalized_title = normalize_title(item["title"])
        if not normalized_title or normalized_title in seen_titles:
            continue
        seen_titles.add(normalized_title)
        images = item.get("keyImages", [])
        cover = ""
        if isinstance(images, list):
            image = next(
                (
                    image
                    for image in images
                    if isinstance(image, dict)
                    and image.get("type") in {"OfferImageWide", "DieselStoreFrontWide", "Thumbnail"}
                    and isinstance(image.get("url"), str)
                ),
                None,
            )
            if image:
                cover = image["url"]

        product_slug = item.get("productSlug")
        page_slug = (
            product_slug.split("/", 1)[0]
            if isinstance(product_slug, str) and product_slug
            else ""
        )
        if not page_slug:
            catalog_ns = item.get("catalogNs")
            mappings = catalog_ns.get("mappings", []) if isinstance(catalog_ns, dict) else []
            if isinstance(mappings, list):
                page_slug = next(
                    (
                        mapping["pageSlug"]
                        for mapping in mappings
                        if isinstance(mapping, dict)
                        and isinstance(mapping.get("pageSlug"), str)
                    ),
                    "",
                )

        price_data = item.get("price")
        total_price = price_data.get("totalPrice", {}) if isinstance(price_data, dict) else {}
        formatted_price = (
            total_price.get("fmtPrice", {}).get("discountPrice", "")
            if isinstance(total_price, dict)
            and isinstance(total_price.get("fmtPrice"), dict)
            else ""
        )
        custom_attributes = item.get("customAttributes", [])
        attributes = {
            attribute["key"]: attribute["value"]
            for attribute in custom_attributes
            if isinstance(attribute, dict)
            and isinstance(attribute.get("key"), str)
            and isinstance(attribute.get("value"), str)
        } if isinstance(custom_attributes, list) else {}

        games.append(
            {
                "id": item.get("id", ""),
                "title": item["title"],
                "description": item.get("description") or "",
                "cover": cover,
                "url": f"https://store.epicgames.com/da/p/{page_slug}" if page_slug else "",
                "price": formatted_price,
                "developer": attributes.get("developerName", ""),
                "publisher": attributes.get("publisherName", ""),
            }
        )
    return games


def find_rawg_game_match(title: str, category: str) -> dict[str, Any] | None:
    query = rawg_query(category)
    if "genres" not in query and "tags" not in query:
        return None
    response = cache.get_or_fetch(
        rawg_url("games", {"search": title, "page_size": "5"})
    )
    if not isinstance(response, dict) or not isinstance(response.get("results"), list):
        raise UpstreamAPIError("RAWG returned an unexpected game search response.")

    expected_title = normalize_title(title)
    match = next(
        (
            item
            for item in response["results"]
            if isinstance(item, dict)
            and isinstance(item.get("name"), str)
            and normalize_title(item["name"]) == expected_title
            and isinstance(item.get("id"), int)
        ),
        None,
    )
    if match is None:
        return None

    if "genres" in query:
        genre_items = match.get("genres", [])
        if not isinstance(genre_items, list) or not any(
            isinstance(genre, dict) and genre.get("slug") == query["genres"]
            for genre in genre_items
        ):
            return None
    else:
        tag_items = match.get("tags", [])
        if not isinstance(tag_items, list) or not any(
            isinstance(tag, dict) and str(tag.get("id")) == query["tags"]
            for tag in tag_items
        ):
            return None

    genres = match.get("genres", [])
    return {
        "id": match["id"],
        "title": match["name"],
        "cover": match.get("background_image") or "",
        "rating": match.get("rating"),
        "genres": [
            genre["name"]
            for genre in genres
            if isinstance(genre, dict) and isinstance(genre.get("name"), str)
        ] if isinstance(genres, list) else [],
    }


def find_steam_match(title: str) -> dict[str, Any] | None:
    params = urlencode({"term": title, "l": "en", "cc": "DK"})
    url = f"https://store.steampowered.com/api/storesearch/?{params}"
    response = cache.get_or_fetch(url)
    items = response.get("items", []) if isinstance(response, dict) else []
    expected_title = normalize_title(title)

    for item in items:
        if not isinstance(item, dict) or item.get("type") != "app":
            continue
        if normalize_title(str(item.get("name", ""))) != expected_title:
            continue

        app_id = item.get("id")
        if not isinstance(app_id, int):
            continue
        price = item.get("price")
        steam: dict[str, Any] = {
            "url": f"https://store.steampowered.com/app/{app_id}/",
            "price": None,
        }
        if isinstance(price, dict) and isinstance(price.get("final"), int):
            steam["price"] = {
                "final": price["final"],
                "initial": price.get("initial", price["final"]),
                "currency": str(price.get("currency", "DKK")),
                "discountPercent": price.get("discount_percent", 0),
            }
        return steam
    return None


def get_games(
    category: str, page: int = 1, page_size: int = GAME_PAGE_SIZE
) -> dict[str, Any]:
    query = rawg_query(category, page, page_size)
    listing = cache.get_or_fetch(rawg_url("games", query))
    if not isinstance(listing, dict) or not isinstance(listing.get("results"), list):
        raise UpstreamAPIError("RAWG returned an unexpected games response.")

    has_exact_category_filter = "genres" in query or "tags" in query
    category_fallback = bool(category.strip()) and not has_exact_category_filter
    if has_exact_category_filter and not listing["results"]:
        fallback_query = {
            "ordering": "-rating",
            "page_size": str(page_size),
            "page": str(page),
            "search": category.strip(),
        }
        fallback_listing = cache.get_or_fetch(rawg_url("games", fallback_query))
        if not isinstance(fallback_listing, dict) or not isinstance(
            fallback_listing.get("results"), list
        ):
            raise UpstreamAPIError("RAWG returned an unexpected search response.")
        listing = fallback_listing
        category_fallback = bool(listing["results"])

    games: list[dict[str, Any]] = []
    rawg_game_ids: set[int] = set()
    for item in listing["results"][:page_size]:
        if not isinstance(item, dict) or not isinstance(item.get("id"), int):
            continue
        rawg_game_ids.add(item["id"])
        genres = item.get("genres", [])
        game_genres = [
            genre["name"]
            for genre in genres
            if isinstance(genre, dict) and isinstance(genre.get("name"), str)
        ] if isinstance(genres, list) else []
        games.append(
            {
                "id": item["id"],
                "title": str(item.get("name", "Untitled game")),
                "cover": item.get("background_image") or "",
                "description": "",
                "rating": item.get("rating"),
                "genres": game_genres,
                "steam": None,
                "category_match": "suggestion" if category_fallback else "exact",
            }
        )

    warnings: list[str] = []
    epic_by_title: dict[str, dict[str, Any]] = {}
    try:
        epic_catalog = get_epic_games()
        epic_by_title = {
            normalize_title(game["title"]): game
            for game in epic_catalog
            if normalize_title(game["title"])
        }
    except UpstreamAPIError as error:
        epic_catalog = []
        warnings.append(f"Epic Games Store details unavailable: {error}")

    epic_games_added = 0
    if page == 1 and has_exact_category_filter and epic_catalog:
        with ThreadPoolExecutor(max_workers=4) as executor:
            rawg_match_jobs = {
                executor.submit(
                    find_rawg_game_match, epic_game["title"], category
                ): epic_game
                for epic_game in epic_catalog
            }
            for future in as_completed(rawg_match_jobs):
                epic_game = rawg_match_jobs[future]
                try:
                    rawg_match = future.result()
                except (UpstreamAPIError, ValueError) as error:
                    warnings.append(
                        f"Could not verify Epic game {epic_game['title']} in RAWG: {error}"
                    )
                    continue
                if not rawg_match or rawg_match["id"] in rawg_game_ids:
                    continue
                rawg_game_ids.add(rawg_match["id"])
                games.append(
                    {
                        **rawg_match,
                        "description": epic_game["description"],
                        "steam": None,
                        "epic": {
                            "url": epic_game["url"],
                            "price": epic_game["price"],
                        },
                        "category_match": "exact",
                    }
                )
                epic_games_added += 1

    with ThreadPoolExecutor(max_workers=4) as executor:
        detail_jobs = {
            executor.submit(get_rawg_description, game["id"]): game
            for game in games
        }
        steam_jobs = {
            executor.submit(find_steam_match, game["title"]): game
            for game in games
        }
        for future in as_completed(detail_jobs):
            game = detail_jobs[future]
            try:
                game["description"] = future.result()
            except (UpstreamAPIError, ValueError) as error:
                warnings.append(f"RAWG description unavailable for {game['title']}: {error}")
        for future in as_completed(steam_jobs):
            game = steam_jobs[future]
            try:
                game["steam"] = future.result()
            except UpstreamAPIError as error:
                warnings.append(f"Steam price unavailable for {game['title']}: {error}")
    for game in games:
        epic_game = epic_by_title.get(normalize_title(game["title"]))
        if epic_game:
            game["epic"] = {
                "url": epic_game["url"],
                "price": epic_game["price"],
            }
            if not game["description"]:
                game["description"] = epic_game["description"]

    return {
        "games": games,
        "warnings": warnings,
        "has_more": bool(listing.get("next")),
        "category_fallback": category_fallback,
        "epic_games_added": epic_games_added,
    }


def rawg_error_message(error: UpstreamAPIError) -> str:
    if error.status_code == 401:
        return (
            "RAWG rejected the API key (401 Unauthorized). Check that .env "
            "contains only your valid RAWG key, then restart the server."
        )
    if error.status_code == 403:
        return (
            "RAWG denied API access (403 Forbidden). Check that API access is "
            "enabled for your RAWG account."
        )
    return f"Could not load games from RAWG: {error}"


class ArcadeVaultHandler(BaseHTTPRequestHandler):
    server_version = "ArcadeVault/1.0"

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        if path in {"/api/auth/register", "/api/auth/login"}:
            if not self.verify_same_origin():
                self.send_json(403, {"error": "Cross-origin requests are not allowed."})
                return
            self.handle_auth(path)
            return
        if path in {"/api/auth/logout", "/api/profile", "/api/password"}:
            if not self.verify_same_origin():
                self.send_json(403, {"error": "Cross-origin requests are not allowed."})
                return
        if path == "/api/auth/logout":
            self.handle_logout()
        elif path == "/api/profile":
            self.handle_profile_update()
        elif path == "/api/password":
            self.handle_password_update()
        elif path.startswith("/api/favorites/"):
            if not self.verify_same_origin():
                self.send_json(403, {"error": "Cross-origin requests are not allowed."})
                return
            self.handle_favorite_add(path.removeprefix("/api/favorites/"))
        else:
            self.send_json(404, {"error": "Endpoint not found."})

    def do_DELETE(self) -> None:
        path = urlsplit(self.path).path
        if not path.startswith("/api/favorites/") or not self.verify_same_origin():
            self.send_json(403, {"error": "Request is not allowed."})
            return
        self.handle_favorite_delete(path.removeprefix("/api/favorites/"))

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        if parsed.path == "/api/me":
            user = self.current_user()
            if user is None:
                self.send_json(401, {"error": "Please sign in to view your profile."})
            else:
                self.send_json(200, safe_profile(user))
            return
        if parsed.path in {"/login.html", "/signup.html"} and self.current_user():
            self.redirect("/profile.html")
            return
        if parsed.path == "/logout":
            self.handle_logout("/login.html")
            return
        if parsed.path == "/api/epic-games":
            self.handle_epic_games(parse_qs(parsed.query))
            return
        if parsed.path == "/api/games":
            self.handle_games(parse_qs(parsed.query))
            return
        if parsed.path.startswith("/api/games/"):
            self.handle_game_details(parsed.path.removeprefix("/api/games/"))
            return
        self.serve_static(parsed.path)

    def current_user(self) -> sqlite3.Row | None:
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except Exception:
            return None
        session_cookie = cookie.get(SESSION_COOKIE)
        return get_session_user(session_cookie.value if session_cookie else None)

    def verify_same_origin(self) -> bool:
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            return False
        origin = self.headers.get("Origin")
        if not origin:
            return True
        return origin.rstrip("/") in {
            f"http://{self.headers.get('Host', '')}",
            f"https://{self.headers.get('Host', '')}",
        }

    def read_form(self) -> dict[str, str] | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_json(400, {"error": "Invalid request body."})
            return None
        if length < 0 or length > MAX_REQUEST_BODY:
            self.send_json(413, {"error": "Request body is too large."})
            return None
        if self.headers.get_content_type() != "application/x-www-form-urlencoded":
            self.send_json(415, {"error": "Expected a form submission."})
            return None
        try:
            body = self.rfile.read(length).decode("utf-8")
            return {
                key: values[0]
                for key, values in parse_qs(body, keep_blank_values=True).items()
            }
        except UnicodeDecodeError:
            self.send_json(400, {"error": "Form data must be UTF-8."})
            return None

    def read_json(self) -> dict[str, Any] | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_json(400, {"error": "Invalid request body."})
            return None
        if length < 0 or length > MAX_REQUEST_BODY:
            self.send_json(413, {"error": "Request body is too large."})
            return None
        if self.headers.get_content_type() != "application/json":
            self.send_json(415, {"error": "Expected a JSON request."})
            return None
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.send_json(400, {"error": "Invalid JSON request body."})
            return None
        if not isinstance(body, dict):
            self.send_json(400, {"error": "JSON body must be an object."})
            return None
        return body

    def handle_auth(self, path: str) -> None:
        form = self.read_form()
        if form is None:
            return
        password = form.get("password", "")
        if path == "/api/auth/register":
            name = form.get("name", "").strip()
            username = form.get("username", "").strip()
            email = form.get("email", "").strip()
            if not 1 <= len(name) <= 60:
                self.send_json(400, {"error": "Name must be 1 to 60 characters."})
                return
            if not re.fullmatch(r"[A-Za-z0-9_]{3,32}", username):
                self.send_json(
                    400,
                    {"error": "Username must be 3 to 32 letters, numbers, or underscores."},
                )
                return
            if (
                len(email) > 254
                or email.count("@") != 1
                or "." not in email.rsplit("@", 1)[-1]
            ):
                self.send_json(400, {"error": "Enter a valid email address."})
                return
            if not 12 <= len(password) <= 128:
                self.send_json(
                    400,
                    {"error": "Password must be between 12 and 128 characters."},
                )
                return
            salt, password_hash = hash_password(password)
            try:
                with auth_connection() as connection:
                    cursor = connection.execute(
                        """
                        INSERT INTO users
                            (name, username, username_key, email, email_key,
                             password_salt, password_hash, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            name,
                            username,
                            username.casefold(),
                            email,
                            email.casefold(),
                            salt,
                            password_hash,
                            int(time.time()),
                        ),
                    )
                    user_id = cursor.lastrowid
            except sqlite3.IntegrityError:
                self.send_json(409, {"error": "That username or email is already registered."})
                return
        else:
            login = form.get("username", "").strip()
            if not login or not password:
                self.send_json(400, {"error": "Enter your username/email and password."})
                return
            with auth_connection() as connection:
                user = connection.execute(
                    """
                    SELECT id, password_salt, password_hash
                    FROM users
                    WHERE username_key = ? OR email_key = ?
                    """,
                    (login.casefold(), login.casefold()),
                ).fetchone()
            if user is None or not verify_password(
                password,
                user["password_salt"] if user else b"\0" * 16,
                user["password_hash"] if user else b"\0" * 32,
            ):
                self.send_json(401, {"error": "Incorrect username/email or password."})
                return
            user_id = user["id"]

        remember = form.get("remember") == "on"
        token, expires_at = create_session(user_id, remember)
        self.send_json(
            200,
            {"ok": True},
            set_cookie=self.session_cookie(token, remember, expires_at),
        )

    def session_cookie(self, token: str, remember: bool, expires_at: int) -> str:
        cookie = (
            f"{SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax"
        )
        if remember:
            cookie += f"; Max-Age={max(0, expires_at - int(time.time()))}"
        return cookie

    def handle_logout(self, redirect_target: str | None = None) -> None:
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except Exception:
            cookie = SimpleCookie()
        session_cookie = cookie.get(SESSION_COOKIE)
        if session_cookie:
            token_hash = hashlib.sha256(
                session_cookie.value.encode("ascii", errors="ignore")
            ).hexdigest()
            with auth_connection() as connection:
                connection.execute(
                    "DELETE FROM sessions WHERE token_hash = ?",
                    (token_hash,),
                )
        expired_cookie = f"{SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"
        if redirect_target:
            self.send_response(303)
            self.send_header("Location", redirect_target)
            self.send_header("Set-Cookie", expired_cookie)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_json(200, {"ok": True}, set_cookie=expired_cookie)

    def handle_profile_update(self) -> None:
        user = self.current_user()
        if user is None:
            self.send_json(401, {"error": "Please sign in again."})
            return
        form = self.read_form()
        if form is None:
            return
        name = form.get("name", "").strip()
        email = form.get("email", "").strip()
        if not 1 <= len(name) <= 60:
            self.send_json(400, {"error": "Name must be 1 to 60 characters."})
            return
        if (
            len(email) > 254
            or email.count("@") != 1
            or "." not in email.rsplit("@", 1)[-1]
        ):
            self.send_json(400, {"error": "Enter a valid email address."})
            return
        try:
            with auth_connection() as connection:
                connection.execute(
                    """
                    UPDATE users SET name = ?, email = ?, email_key = ?
                    WHERE id = ?
                    """,
                    (name, email, email.casefold(), user["id"]),
                )
        except sqlite3.IntegrityError:
            self.send_json(409, {"error": "That email address is already registered."})
            return
        refreshed_user = get_session_user(self.session_token())
        self.send_json(200, {"ok": True, "user": safe_profile(refreshed_user)})

    def session_token(self) -> str | None:
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except Exception:
            return None
        session_cookie = cookie.get(SESSION_COOKIE)
        return session_cookie.value if session_cookie else None

    def handle_password_update(self) -> None:
        user = self.current_user()
        if user is None:
            self.send_json(401, {"error": "Please sign in again."})
            return
        form = self.read_form()
        if form is None:
            return
        current_password = form.get("current_password", "")
        new_password = form.get("new_password", "")
        with auth_connection() as connection:
            credential = connection.execute(
                "SELECT password_salt, password_hash FROM users WHERE id = ?",
                (user["id"],),
            ).fetchone()
        if credential is None or not verify_password(
            current_password, credential["password_salt"], credential["password_hash"]
        ):
            self.send_json(401, {"error": "Current password is incorrect."})
            return
        if not 12 <= len(new_password) <= 128:
            self.send_json(
                400,
                {"error": "New password must be between 12 and 128 characters."},
            )
            return
        salt, password_hash = hash_password(new_password)
        token_hash = hashlib.sha256(
            (self.session_token() or "").encode("ascii", errors="ignore")
        ).hexdigest()
        with auth_connection() as connection:
            connection.execute(
                "UPDATE users SET password_salt = ?, password_hash = ? WHERE id = ?",
                (salt, password_hash, user["id"]),
            )
            connection.execute(
                "DELETE FROM sessions WHERE user_id = ? AND token_hash != ?",
                (user["id"], token_hash),
            )
        self.send_json(200, {"ok": True})

    def handle_favorite_add(self, game_id: str) -> None:
        user = self.current_user()
        if user is None:
            self.send_json(401, {"error": "Please sign in to save favorites."})
            return
        try:
            parsed_game_id = int(game_id)
        except ValueError:
            self.send_json(400, {"error": "Game ID must be a positive integer."})
            return
        if not game_id.isascii() or not game_id.isdecimal() or parsed_game_id < 1:
            self.send_json(400, {"error": "Game ID must be a positive integer."})
            return
        body = self.read_json()
        if body is None:
            return
        title = body.get("title")
        cover = body.get("cover", "")
        if not isinstance(title, str) or not title.strip() or len(title) > 160:
            self.send_json(400, {"error": "A game title of 1 to 160 characters is required."})
            return
        if not isinstance(cover, str) or len(cover) > 2048:
            self.send_json(400, {"error": "Invalid game cover URL."})
            return
        if cover and urlsplit(cover).scheme != "https":
            self.send_json(400, {"error": "Game cover must use HTTPS."})
            return
        with auth_connection() as connection:
            connection.execute(
                """
                INSERT INTO favorites (user_id, game_id, title, cover, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id, game_id) DO UPDATE SET
                    title = excluded.title, cover = excluded.cover
                """,
                (
                    user["id"],
                    parsed_game_id,
                    title.strip(),
                    cover,
                    int(time.time()),
                ),
            )
        self.send_json(200, {"ok": True, "is_favorite": True})

    def handle_favorite_delete(self, game_id: str) -> None:
        user = self.current_user()
        if user is None:
            self.send_json(401, {"error": "Please sign in to manage favorites."})
            return
        try:
            parsed_game_id = int(game_id)
        except ValueError:
            self.send_json(400, {"error": "Game ID must be a positive integer."})
            return
        if not game_id.isascii() or not game_id.isdecimal() or parsed_game_id < 1:
            self.send_json(400, {"error": "Game ID must be a positive integer."})
            return
        with auth_connection() as connection:
            connection.execute(
                "DELETE FROM favorites WHERE user_id = ? AND game_id = ?",
                (user["id"], parsed_game_id),
            )
        self.send_json(200, {"ok": True, "is_favorite": False})

    def redirect(self, location: str) -> None:
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def handle_epic_games(self, query: dict[str, list[str]]) -> None:
        search_term = query.get("search", [""])[0].strip()
        if len(search_term) > 80:
            self.send_json(400, {"error": "Search must be 80 characters or fewer."})
            return
        try:
            games = get_epic_games()
            if search_term:
                normalized_term = normalize_title(search_term)
                games = [
                    game
                    for game in games
                    if normalized_term in normalize_title(game["title"])
                ]
            self.send_json(200, {"games": games})
        except UpstreamAPIError as error:
            self.send_json(502, {"error": f"Could not load games from Epic: {error}"})

    def handle_game_details(self, game_id: str) -> None:
        try:
            parsed_game_id = int(game_id)
        except ValueError:
            self.send_json(400, {"error": "Game ID must be a positive integer."})
            return
        if not game_id.isascii() or not game_id.isdecimal() or parsed_game_id < 1:
            self.send_json(400, {"error": "Game ID must be a positive integer."})
            return
        try:
            self.send_json(200, get_game_details(parsed_game_id))
        except ValueError as error:
            self.send_json(503, {"error": str(error)})
        except UpstreamAPIError as error:
            self.send_json(502, {"error": rawg_error_message(error)})

    def handle_games(self, query: dict[str, list[str]]) -> None:
        category = query.get("category", [""])[0].strip()
        if len(category) > 80:
            self.send_json(400, {"error": "Category must be 80 characters or fewer."})
            return
        try:
            page = int(query.get("page", ["1"])[0])
        except ValueError:
            self.send_json(400, {"error": "Page must be a positive integer."})
            return
        if page < 1:
            self.send_json(400, {"error": "Page must be a positive integer."})
            return
        try:
            page_size = int(query.get("limit", [str(GAME_PAGE_SIZE)])[0])
        except ValueError:
            self.send_json(400, {"error": "Limit must be an integer from 1 to 10."})
            return
        if not 1 <= page_size <= MAX_GAME_PAGE_SIZE:
            self.send_json(400, {"error": "Limit must be an integer from 1 to 10."})
            return
        try:
            self.send_json(200, get_games(category, page, page_size))
        except ValueError as error:
            self.send_json(503, {"error": str(error)})
        except UpstreamAPIError as error:
            self.send_json(502, {"error": rawg_error_message(error)})

    def serve_static(self, request_path: str) -> None:
        relative_path = unquote(request_path).lstrip("/") or "home.html"
        relative_parts = Path(relative_path).parts
        if any(part.startswith(".") for part in relative_parts):
            self.send_json(404, {"error": "File not found."})
            return
        candidate = (ROOT / relative_path).resolve()
        if candidate != ROOT and ROOT not in candidate.parents:
            self.send_json(403, {"error": "Access denied."})
            return
        if candidate.suffix == ".py" or not candidate.is_file():
            self.send_json(404, {"error": "File not found."})
            return
        try:
            content = candidate.read_bytes()
        except OSError:
            self.send_json(500, {"error": "Could not read requested file."})
            return

        content_type, _ = mimetypes.guess_type(candidate.name)
        self.send_response(200)
        response_type = content_type or "application/octet-stream"
        if response_type.startswith("text/") or response_type in {
            "application/javascript",
            "application/json",
        }:
            response_type += "; charset=utf-8"
        self.send_header("Content-Type", response_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(content)

    def send_json(
        self, status: int, body: dict[str, Any], set_cookie: str | None = None
    ) -> None:
        content = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if set_cookie:
            self.send_header("Set-Cookie", set_cookie)
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format_string: str, *args: Any) -> None:
        print(f"{self.log_date_time_string()} - {format_string % args}")


def main() -> None:
    load_env_file()
    initialize_auth_database()
    server = ThreadingHTTPServer((HOST, PORT), ArcadeVaultHandler)
    print(f"Arcade Vault is running at http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Arcade Vault.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
