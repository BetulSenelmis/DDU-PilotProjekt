# Arcade Vault

## Run the game catalog

The game catalog uses RAWG for game metadata, the Steam Store search API for
matching Steam links and Danish prices, and Epic Games Store's public catalog
feed for additional listings, descriptions, links, and Danish prices. The local
Python server keeps the RAWG API key out of browser code and serves both the
site and its API.

1. Get a RAWG API key from [RAWG API](https://rawg.io/apidocs).
2. Copy `.env.example` to `.env` and replace the placeholder with your key:

   ```text
   RAWG_API_KEY=your_actual_key
   ```

3. From this folder, start the server:

   ```powershell
   python server.py
   ```

4. Open <http://127.0.0.1:8000/home.html>.

The `.env` file is excluded by `.gitignore`; do not publish or share the key.
The server uses only Python's standard library, so no package installation is
required.

Games are fetched six at a time. Select "Load more games" to continue through
all available RAWG result pages; there is no overall result limit.
The search icon opens a page with the ten most-added games on RAWG and a search
field for finding games by title.
Game covers and titles open a detail page with RAWG's description, genres,
platforms, developer, publisher, release date, and ratings.
Epic's feed currently supplies only the listings it returns, not its full
catalog or a reliable genre taxonomy. Those listings are shown separately;
exact title matches add an Epic link and price to RAWG game cards, and Epic's
description fills in when RAWG has none. Genre filters and genre metadata
remain based on RAWG, so Epic cannot guarantee that every category has results.
For genre and tag categories, Epic listings are also added to the main results
when RAWG confirms the exact same title and category. Epic listings that cannot
be verified are not presented as genre matches.

RAWG's standard game genres are filtered using genre identifiers, and supported
themes such as Horror, Fantasy, religions, and several mythologies use exact
RAWG tag identifiers. "Southeast Asian Mythology" currently has no dedicated
RAWG tag, so that category remains a text search and may return less precise
matches. Each game card shows the selected category separately from the game's
RAWG genres, so the category used to list it is clear.
If a category with an exact RAWG genre or tag has no results, the catalog tries
a title/text search and labels any results as possible matches rather than
verified genre matches.
Steam and Epic prices are provided only when the stores return matching data
and may change by region or over time.

## Accounts and profiles

Register from `signup.html` or sign in from `login.html`. Accounts, password
hashes, sessions, and favorite games are stored locally in
`arcade-vault.sqlite3`, created automatically when the server starts. Passwords
are stored as PBKDF2-HMAC-SHA256 hashes; the database file is excluded by
`.gitignore`. Keep the database private and back it up securely if you need to
preserve accounts.

After signing in, the site opens the profile page. Heart buttons on RAWG-backed
game cards and detail pages save or remove favorites. Guests are sent through
the profile and sign-in flow, then the selected game is saved automatically.
Epic catalog-only cards do not have a matching RAWG game ID and therefore do
not offer the favorite action.

The brain icon opens personalized recommendations. Save at least four answers
in the profile questionnaire first; recommendations use your favorite genre,
player type, hobbies, least-favorite game and genre, and phobias to rank RAWG
games.
Questionnaire answers are stored in the current browser's local storage for
each username and are not synchronized to other browsers or devices.

This built-in server is intended for local development, not public deployment.
For an internet-facing service, use HTTPS and a production-ready deployment
with secure cookie settings, account recovery, rate limiting, and operational
database backups.
