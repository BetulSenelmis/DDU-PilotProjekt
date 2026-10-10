import json
import os
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from http.server import ThreadingHTTPServer
from threading import Thread
from http.cookies import SimpleCookie
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from unittest.mock import patch

import server


class GameApiTests(unittest.TestCase):
    def test_maps_supported_rawg_genres(self):
        self.assertEqual(server.rawg_query("RPG")["genres"], "role-playing-games-rpg")
        self.assertEqual(server.rawg_query("Sport")["genres"], "sports")
        self.assertEqual(server.rawg_query("RPG", page=3)["page"], "3")
        self.assertEqual(server.rawg_query("", page_size=10)["page_size"], "10")
        self.assertEqual(server.rawg_query("")["ordering"], "-added")

    def test_filters_known_themes_with_rawg_tag_ids(self):
        self.assertEqual(server.rawg_query("hOrRoR")["tags"], "16")
        self.assertEqual(server.rawg_query("Greek Mythology")["tags"], "38489")
        self.assertEqual(server.rawg_query("Southeast Asian Mythology")["search"], "Southeast Asian Mythology")
        self.assertEqual(server.rawg_query("horror")["ordering"], "-rating")

    def test_normalizes_titles_for_exact_steam_matching(self):
        self.assertEqual(server.normalize_title("Pokémon: Let's Go!"), "pokemonletsgo")

    def test_requires_rawg_api_key(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "RAWG_API_KEY is missing"):
                server.rawg_url("games")

    def test_explains_rawg_rejected_api_key(self):
        error = server.UpstreamAPIError("HTTP 401", status_code=401)
        self.assertIn("RAWG rejected the API key", server.rawg_error_message(error))
        self.assertIn("restart the server", server.rawg_error_message(error))

    def test_combines_rawg_metadata_and_exact_steam_match(self):
        def fake_fetch(url):
            if "/games/1?" in url:
                return {"description_raw": "A sample game description."}
            if "storesearch" in url:
                return {
                    "items": [
                        {
                            "type": "app",
                            "name": "Sample Game",
                            "id": 123,
                            "price": {
                                "final": 1200,
                                "initial": 1500,
                                "currency": "DKK",
                                "discount_percent": 20,
                            },
                        }
                    ]
                }
            return {
                "results": [
                    {
                        "id": 1,
                        "name": "Sample Game",
                        "background_image": "https://example.test/cover.jpg",
                        "rating": 4.2,
                        "genres": [{"name": "Adventure"}],
                    }
                ],
                "next": "https://api.rawg.io/api/games?page=2",
            }

        with (
            patch.dict(os.environ, {"RAWG_API_KEY": "test-key"}),
            patch.object(server.cache, "get_or_fetch", side_effect=fake_fetch),
            patch.object(server, "get_epic_games", return_value=[]),
        ):
            result = server.get_games("Adventure", page=2)

        self.assertEqual(len(result["games"]), 1)
        game = result["games"][0]
        self.assertEqual(game["description"], "A sample game description.")
        self.assertEqual(game["genres"], ["Adventure"])
        self.assertEqual(game["steam"]["price"]["currency"], "DKK")
        self.assertEqual(result["warnings"], [])
        self.assertTrue(result["has_more"])
        self.assertFalse(result["category_fallback"])

    def test_falls_back_to_labeled_search_suggestions_for_empty_genre(self):
        exact_listing = {"results": [], "next": None}
        suggestion_listing = {
            "results": [{"id": 2, "name": "Action Hero", "genres": []}],
            "next": None,
        }
        with (
            patch.dict(os.environ, {"RAWG_API_KEY": "test-key"}),
            patch.object(
                server.cache,
                "get_or_fetch",
                side_effect=[exact_listing, suggestion_listing],
            ) as fetch,
            patch.object(server, "get_rawg_description", return_value=""),
            patch.object(server, "find_steam_match", return_value=None),
            patch.object(server, "get_epic_games", return_value=[]),
        ):
            result = server.get_games("Action")

        self.assertEqual(len(result["games"]), 1)
        self.assertEqual(result["games"][0]["category_match"], "suggestion")
        self.assertTrue(result["category_fallback"])
        self.assertIn("search=Action", fetch.call_args_list[1].args[0])

    def test_returns_ten_games_when_requested(self):
        results = [
            {"id": game_id, "name": f"Game {game_id}", "genres": []}
            for game_id in range(1, 11)
        ]
        with (
            patch.dict(os.environ, {"RAWG_API_KEY": "test-key"}),
            patch.object(
                server.cache,
                "get_or_fetch",
                return_value={"results": results, "next": None},
            ) as fetch,
            patch.object(server, "get_rawg_description", return_value=""),
            patch.object(server, "find_steam_match", return_value=None),
            patch.object(server, "get_epic_games", return_value=[]),
        ):
            result = server.get_games("", page_size=10)

        self.assertEqual(len(result["games"]), 10)
        self.assertIn("page_size=10", fetch.call_args.args[0])
        self.assertFalse(result["has_more"])

    def test_uses_matching_epic_description_and_store_link(self):
        listing = {
            "results": [
                {"id": 1, "name": "Sample Game", "genres": [{"name": "Action"}]}
            ],
            "next": None,
        }
        epic_game = {
            "title": "Sample Game",
            "description": "Epic store description.",
            "url": "https://store.epicgames.com/da/p/sample-game",
            "price": "49,00 kr.",
        }
        with (
            patch.dict(os.environ, {"RAWG_API_KEY": "test-key"}),
            patch.object(server.cache, "get_or_fetch", return_value=listing),
            patch.object(server, "get_rawg_description", return_value=""),
            patch.object(server, "find_steam_match", return_value=None),
            patch.object(server, "get_epic_games", return_value=[epic_game]),
        ):
            result = server.get_games("Action")

        self.assertEqual(result["games"][0]["description"], "Epic store description.")
        self.assertEqual(
            result["games"][0]["epic"],
            {
                "url": "https://store.epicgames.com/da/p/sample-game",
                "price": "49,00 kr.",
            },
        )

    def test_adds_epic_game_to_genre_results_only_with_rawg_match(self):
        listing = {
            "results": [
                {"id": 1, "name": "Existing Action Game", "genres": [{"name": "Action"}]}
            ],
            "next": None,
        }
        epic_game = {
            "title": "Epic Action Game",
            "description": "Epic description.",
            "url": "https://store.epicgames.com/da/p/epic-action-game",
            "price": "Free",
        }
        rawg_match = {
            "id": 2,
            "title": "Epic Action Game",
            "cover": "https://example.test/epic-cover.jpg",
            "rating": 4.0,
            "genres": ["Action"],
        }
        with (
            patch.dict(os.environ, {"RAWG_API_KEY": "test-key"}),
            patch.object(server.cache, "get_or_fetch", return_value=listing),
            patch.object(server, "get_epic_games", return_value=[epic_game]),
            patch.object(server, "find_rawg_game_match", return_value=rawg_match),
            patch.object(server, "get_rawg_description", return_value=""),
            patch.object(server, "find_steam_match", return_value=None),
        ):
            result = server.get_games("Action")

        self.assertEqual([game["id"] for game in result["games"]], [1, 2])
        self.assertEqual(result["games"][1]["category_match"], "exact")
        self.assertEqual(result["games"][1]["description"], "Epic description.")
        self.assertEqual(result["games"][1]["epic"]["price"], "Free")
        self.assertEqual(result["epic_games_added"], 1)

    def test_loads_game_detail_fields_from_rawg(self):
        rawg_details = {
            "name": "Sample Game",
            "background_image": "https://example.test/cover.jpg",
            "description_raw": "Long game description.",
            "rating": 4.2,
            "metacritic": 87,
            "released": "2024-05-01",
            "genres": [{"name": "Adventure"}],
            "developers": [{"name": "Studio"}],
            "publishers": [{"name": "Publisher"}],
            "platforms": [{"platform": {"name": "PC"}}],
            "website": "https://example.test/",
            "esrb_rating": {"name": "Teen"},
        }
        with (
            patch.dict(os.environ, {"RAWG_API_KEY": "test-key"}),
            patch.object(server.cache, "get_or_fetch", return_value=rawg_details),
            patch.object(server, "get_epic_games", return_value=[]),
        ):
            details = server.get_game_details(123)

        self.assertEqual(details["id"], 123)
        self.assertEqual(details["title"], "Sample Game")
        self.assertEqual(details["description"], "Long game description.")
        self.assertEqual(details["genres"], ["Adventure"])
        self.assertEqual(details["platforms"], ["PC"])
        self.assertEqual(details["developers"], ["Studio"])
        self.assertEqual(details["publishers"], ["Publisher"])
        self.assertEqual(details["metacritic"], 87)
        self.assertEqual(details["esrb_rating"], "Teen")

    def test_maps_epic_games_store_catalog_response(self):
        feed = {
            "data": {
                "Catalog": {
                    "searchStore": {
                        "elements": [
                            {
                                "id": "epic-id",
                                "title": "Sample Game",
                                "description": "A store description.",
                                "keyImages": [
                                    {"type": "OfferImageWide", "url": "https://example.test/cover.jpg"}
                                ],
                                "productSlug": "sample-game/home",
                                "price": {
                                    "totalPrice": {
                                        "fmtPrice": {"discountPrice": "49,00 kr."}
                                    }
                                },
                                "customAttributes": [
                                    {"key": "developerName", "value": "Studio"},
                                    {"key": "publisherName", "value": "Publisher"},
                                ],
                            }
                        ]
                    }
                }
            }
        }
        with patch.object(server.cache, "get_or_fetch", return_value=feed):
            games = server.get_epic_games()

        self.assertEqual(len(games), 1)
        self.assertEqual(games[0]["title"], "Sample Game")
        self.assertEqual(games[0]["description"], "A store description.")
        self.assertEqual(games[0]["url"], "https://store.epicgames.com/da/p/sample-game")
        self.assertEqual(games[0]["price"], "49,00 kr.")
        self.assertEqual(games[0]["developer"], "Studio")

    def test_game_detail_api_route_returns_game_details(self):
        http_server = ThreadingHTTPServer(("127.0.0.1", 0), server.ArcadeVaultHandler)
        thread = Thread(target=http_server.serve_forever)
        thread.start()
        try:
            with (
                patch.object(
                    server,
                    "get_game_details",
                    return_value={"id": 123, "title": "Sample Game"},
                ),
                urlopen(f"http://127.0.0.1:{http_server.server_port}/api/games/123") as response,
            ):
                result = json.loads(response.read().decode("utf-8"))
            self.assertEqual(response.status, 200)
            self.assertEqual(result, {"id": 123, "title": "Sample Game"})
        finally:
            http_server.shutdown()
            http_server.server_close()
            thread.join()


class AuthApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database_directory = tempfile.TemporaryDirectory()
        cls.database_path = os.path.join(cls.database_directory.name, "auth-test.sqlite3")
        cls.database_patch = patch.object(server, "AUTH_DB_PATH", server.Path(cls.database_path))
        cls.database_patch.start()
        server.initialize_auth_database()
        cls.http_server = ThreadingHTTPServer(("127.0.0.1", 0), server.ArcadeVaultHandler)
        cls.thread = Thread(target=cls.http_server.serve_forever)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.http_server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.http_server.shutdown()
        cls.http_server.server_close()
        cls.thread.join()
        cls.database_patch.stop()
        cls.database_directory.cleanup()

    def request(self, path, method="GET", fields=None, json_body=None, cookie=None):
        headers = {}
        body = None
        if fields is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            body = urlencode(fields).encode("utf-8")
        elif json_body is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(json_body).encode("utf-8")
        if cookie:
            headers["Cookie"] = cookie
        request = Request(
            f"{self.base_url}{path}",
            data=body,
            headers=headers,
            method=method,
        )
        try:
            response = urlopen(request)
        except HTTPError as error:
            response = error
        with response:
            payload = json.loads(response.read().decode("utf-8"))
            return response.status, payload, response.headers

    def register(self, username="player_test"):
        status, body, headers = self.request(
            "/api/auth/register",
            method="POST",
            fields={
                "name": "Test Player",
                "username": username,
                "email": f"{username}@example.test",
                "password": "secure-passphrase-123",
            },
        )
        cookie = SimpleCookie()
        cookie.load(headers.get("Set-Cookie", ""))
        return status, body, cookie[server.SESSION_COOKIE].OutputString()

    def test_registration_stores_password_hash_and_creates_session(self):
        username = f"player_{time.time_ns()}"
        status, body, cookie = self.register(username)
        self.assertEqual(status, 200)
        self.assertEqual(body, {"ok": True})

        with closing(sqlite3.connect(self.database_path)) as connection:
            stored_hash = connection.execute(
                "SELECT password_hash FROM users WHERE username = ?",
                (username,),
            ).fetchone()[0]
        self.assertNotEqual(stored_hash, b"secure-passphrase-123")

        status, profile, _ = self.request("/api/me", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(profile["username"], username)
        self.assertEqual(profile["favorites"], [])

    def test_login_rejects_wrong_password(self):
        username = f"player_{time.time_ns()}"
        self.register(username)
        status, body, _ = self.request(
            "/api/auth/login",
            method="POST",
            fields={"username": username, "password": "wrong-password"},
        )
        self.assertEqual(status, 401)
        self.assertIn("Incorrect", body["error"])

    def test_favorites_profile_updates_password_and_logout(self):
        username = f"player_{time.time_ns()}"
        status, _, cookie = self.register(username)
        self.assertEqual(status, 200)

        status, result, _ = self.request(
            "/api/favorites/123",
            method="POST",
            json_body={
                "title": "Sample Game",
                "cover": "https://example.test/cover.jpg",
            },
            cookie=cookie,
        )
        self.assertEqual(status, 200)
        self.assertTrue(result["is_favorite"])

        status, profile, _ = self.request("/api/me", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(profile["favorites"][0]["id"], 123)
        self.assertEqual(profile["favorites"][0]["title"], "Sample Game")

        status, result, _ = self.request(
            "/api/profile",
            method="POST",
            fields={"name": "Updated Player", "email": f"{username}@example.test"},
            cookie=cookie,
        )
        self.assertEqual(status, 200)
        self.assertEqual(result["user"]["name"], "Updated Player")

        status, _, _ = self.request(
            "/api/password",
            method="POST",
            fields={
                "current_password": "secure-passphrase-123",
                "new_password": "another-secure-passphrase-456",
            },
            cookie=cookie,
        )
        self.assertEqual(status, 200)

        status, _, _ = self.request(
            "/api/favorites/123",
            method="DELETE",
            cookie=cookie,
        )
        self.assertEqual(status, 200)
        status, profile, _ = self.request("/api/me", cookie=cookie)
        self.assertEqual(profile["favorites"], [])

        status, _, _ = self.request(
            "/api/auth/logout",
            method="POST",
            cookie=cookie,
        )
        self.assertEqual(status, 200)
        status, _, _ = self.request("/api/me", cookie=cookie)
        self.assertEqual(status, 401)


if __name__ == "__main__":
    unittest.main()
