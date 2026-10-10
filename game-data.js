const gameGrid = document.querySelector("#game-grid");
const gameStatus = document.querySelector("#game-status");
const gameTemplate = document.querySelector("#game-card-template");
const loadMoreButton = document.querySelector("#load-more-games");
const categoryChips = [...document.querySelectorAll(".category-chip")];
const searchForm = document.querySelector("#game-search-form");
const searchInput = document.querySelector("#game-search-input");
const isSearchPage = document.body.dataset.page === "search";
const isDetailPage = document.body.dataset.page === "detail";
const isRecommendationsPage = document.body.dataset.page === "recommendations";
let activeRequest;
let activeCategory = "";
let currentPage = 1;
let loadedCount = 0;
let hasMore = false;

function createSkeleton() {
  const skeleton = document.createElement("div");
  skeleton.className = "game-card-skeleton";
  skeleton.setAttribute("aria-hidden", "true");
  skeleton.innerHTML =
    '<div class="skeleton-cover"></div><div class="skeleton-line skeleton-title"></div><div class="skeleton-line"></div><div class="skeleton-line skeleton-short"></div>';
  return skeleton;
}

function showSkeletons(count, append = false) {
  const skeletons = Array.from({ length: count }, createSkeleton);
  if (append) {
    gameGrid.append(...skeletons);
  } else {
    gameGrid.replaceChildren(...skeletons);
  }
}

function formatSteamPrice(price) {
  if (!price || !/^[A-Z]{3}$/.test(price.currency)) {
    return "";
  }

  try {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency: price.currency,
    }).format(price.final / 100);
  } catch (error) {
    console.error("Unable to format Steam price.", error);
    return "";
  }
}

function renderGame(game, category, rank) {
  const card = gameTemplate.content.firstElementChild.cloneNode(true);
  const image = card.querySelector("[data-game-image]");
  const rankLabel = card.querySelector("[data-game-rank]");
  const rating = card.querySelector("[data-game-rating]");
  const genres = card.querySelector("[data-game-genres]");
  const store = card.querySelector("[data-game-store]");
  const favoriteButton = card.querySelector("[data-requires-login]");

  const detailUrl = `game-detail.html?id=${encodeURIComponent(game.id)}`;
  favoriteButton.dataset.gameId = String(game.id);
  favoriteButton.dataset.gameTitle = game.title;
  favoriteButton.dataset.gameCover = game.cover || "";
  const imageLink = card.querySelector("[data-game-image-link]");
  const titleLink = card.querySelector("[data-game-title-link]");
  imageLink.href = detailUrl;
  imageLink.setAttribute("aria-label", `Read more about ${game.title}`);
  titleLink.href = detailUrl;
  titleLink.textContent = game.title;
  card.querySelector("[data-game-category]").textContent = category
    ? game.category_match === "suggestion"
      ? `Possible match for: ${category}`
      : isSearchPage
      ? `Search: ${category}`
      : `Listed under: ${category}`
  : "Top 10 popular on RAWG";
  if (rankLabel && rank) {
    rankLabel.textContent = `${rank}.`;
  }
  card.querySelector("[data-game-description]").textContent =
    game.description || "No description is available.";

  if (game.cover) {
    image.src = game.cover;
    image.alt = `${game.title} cover`;
    image.addEventListener("error", () => image.remove(), { once: true });
  } else {
    imageLink.remove();
  }

  rating.textContent =
    typeof game.rating === "number" ? `RAWG rating: ${game.rating.toFixed(1)} / 5` : "";

  for (const genre of game.genres) {
    const tag = document.createElement("span");
    tag.className = "game-genre-tag";
    tag.textContent = genre;
    genres.append(tag);
  }

  if (game.steam?.url) {
    const link = document.createElement("a");
    link.className = "game-store-link";
    link.href = game.steam.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";

    const formattedPrice = formatSteamPrice(game.steam.price);
    link.textContent = formattedPrice ? `Steam · ${formattedPrice}` : "View on Steam";
    store.append(link);
  }
  if (game.epic?.url) {
    const link = document.createElement("a");
    link.className = "game-store-link";
    link.href = game.epic.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = game.epic.price
      ? `Epic Games Store · ${game.epic.price}`
      : "View on Epic Games Store";
    if (store.childElementCount > 0) {
      store.append(document.createTextNode(" · "));
    }
    store.append(link);
  }
  if (store.childElementCount === 0) {
    store.textContent = "Not listed on Steam or Epic Games Store";
  }

  return card;
}

document.addEventListener("click", async (event) => {
  const favoriteButton = event.target.closest("[data-requires-login]");
  if (!favoriteButton) {
    return;
  }
  event.preventDefault();
  if (!favoriteButton.dataset.gameId || favoriteButton.dataset.busy === "true") {
    return;
  }
  favoriteButton.dataset.busy = "true";
  const gameId = favoriteButton.dataset.gameId;
  try {
    const isFavorite = favoriteButton.getAttribute("aria-pressed") === "true";
    const response = await fetch(`/api/favorites/${encodeURIComponent(gameId)}`, {
      method: isFavorite ? "DELETE" : "POST",
      headers: isFavorite ? { Accept: "application/json" } : {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: isFavorite
        ? undefined
        : JSON.stringify({
            title: favoriteButton.dataset.gameTitle,
            cover: favoriteButton.dataset.gameCover || "",
          }),
    });
    const result = await response.json();
    if (response.status === 401) {
      const params = new URLSearchParams({
        favorite: gameId,
        title: favoriteButton.dataset.gameTitle || "",
      });
      if (favoriteButton.dataset.gameCover) {
        params.set("cover", favoriteButton.dataset.gameCover);
      }
      window.location.href = `profile.html?${params}`;
      return;
    }
    if (!response.ok) {
      throw new Error(result.error || `Request failed (${response.status}).`);
    }
    favoriteButton.setAttribute("aria-pressed", String(result.is_favorite));
    favoriteButton.setAttribute(
      "aria-label",
      result.is_favorite ? "Remove game from favorites" : "Save game to favorites",
    );
  } catch (error) {
    console.error("Could not update favorite.", error);
  } finally {
    favoriteButton.dataset.busy = "false";
  }
});

async function syncFavoriteButtons(
  buttons = [...document.querySelectorAll("[data-requires-login][data-game-id]")],
) {
  if (!buttons.length) {
    return;
  }
  try {
    const response = await fetch("/api/me", {
      headers: { Accept: "application/json" },
    });
    if (response.status === 401) {
      return;
    }
    const user = await response.json();
    if (!response.ok) {
      throw new Error(user.error || `Request failed (${response.status}).`);
    }
    const favoriteIds = new Set(user.favorites.map((game) => String(game.id)));
    for (const button of buttons) {
      const isFavorite = favoriteIds.has(button.dataset.gameId);
      button.setAttribute("aria-pressed", String(isFavorite));
      button.setAttribute(
        "aria-label",
        isFavorite ? "Remove game from favorites" : "Save game to favorites",
      );
    }
  } catch (error) {
    console.error("Could not load favorite state.", error);
  }
}

async function loadGames(category, page = 1, append = false, pageSize = 6) {
  activeRequest?.abort();
  const request = new AbortController();
  activeRequest = request;
  activeCategory = category;
  gameGrid.setAttribute("aria-busy", "true");
  loadMoreButton.disabled = true;
  if (append) {
    showSkeletons(6, true);
    gameStatus.textContent = `Loading more ${category} games…`;
  } else {
    currentPage = 1;
    loadedCount = 0;
    hasMore = false;
    loadMoreButton.hidden = true;
    showSkeletons(6);
    gameStatus.textContent = "Loading games…";
  }

  try {
    const params = new URLSearchParams({
      category,
      page: String(page),
      limit: String(pageSize),
    });
    const response = await fetch(`/api/games?${params}`, {
      signal: request.signal,
      headers: { Accept: "application/json" },
    });
    const result = await response.json();

    if (!response.ok) {
      throw new Error(result.error || `Request failed (${response.status}).`);
    }

    if (request.signal.aborted) {
      return;
    }

    const cards = result.games.map((game, index) =>
      renderGame(game, category, (page - 1) * pageSize + index + 1),
    );
    if (append) {
      gameGrid.querySelectorAll(".game-card-skeleton").forEach((item) => item.remove());
      gameGrid.append(...cards);
    } else {
      gameGrid.replaceChildren(...cards);
    }
    syncFavoriteButtons();
    gameGrid.setAttribute("aria-busy", "false");
    currentPage = page;
    loadedCount += result.games.length;
    hasMore = result.has_more;
    loadMoreButton.hidden = !hasMore || (isSearchPage && !category);

    if (loadedCount === 0) {
      gameStatus.textContent = category
        ? `No games found for “${category}”.`
        : "No top-rated games were found.";
    } else if (result.category_fallback) {
      gameStatus.textContent =
        `No exact RAWG genre or tag matches for “${category}”; showing ${loadedCount} text-search suggestions.`;
      if (result.warnings.length > 0) {
        gameStatus.textContent +=
          " Some RAWG descriptions or store prices could not be loaded.";
        console.warn("Some game details could not be loaded:", result.warnings);
      }
    } else if (result.warnings.length > 0) {
      gameStatus.textContent =
        `Showing ${loadedCount} games. Some RAWG descriptions or Steam prices could not be loaded.`;
      console.warn("Some game details could not be loaded:", result.warnings);
    } else if (isSearchPage && !category) {
      gameStatus.textContent = `Showing the top ${loadedCount} popular games on RAWG.`;
    } else {
      gameStatus.textContent = hasMore
        ? `Showing ${loadedCount} games. More are available.`
        : `Showing all ${loadedCount} available games.`;
    }
    if (result.epic_games_added > 0) {
      gameStatus.textContent +=
        ` Includes ${result.epic_games_added} Epic Games Store game${result.epic_games_added === 1 ? "" : "s"} with a verified RAWG genre or tag.`;
    }
  } catch (error) {
    if (error.name === "AbortError") {
      return;
    }
    if (append) {
      gameGrid.querySelectorAll(".game-card-skeleton").forEach((item) => item.remove());
    } else {
      gameGrid.replaceChildren();
    }
    if (!hasMore) {
      loadMoreButton.hidden = true;
    }
    gameStatus.textContent = append
      ? `Could not load more games: ${error.message}`
      : `Could not load games: ${error.message}`;
    console.error("Could not load game data.", error);
  } finally {
    if (activeRequest === request) {
      gameGrid.setAttribute("aria-busy", "false");
      loadMoreButton.disabled = false;
      activeRequest = undefined;
    }
  }
}

for (const chip of categoryChips) {
  chip.addEventListener("click", () => {
    for (const item of categoryChips) {
      const selected = item === chip;
      item.classList.toggle("is-selected", selected);
      item.setAttribute("aria-pressed", String(selected));
    }
    loadGames(chip.textContent.trim());
  });
}

if (searchForm && searchInput) {
  searchForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const searchTerm = searchInput.value.trim();
    if (!searchTerm) {
      gameStatus.textContent = "Enter a game title to search.";
      searchInput.focus();
      return;
    }
    document.querySelector("#search-page-title").textContent = "SEARCH RESULTS";
    loadGames(searchTerm, 1, false, 10);
    loadEpicGames(searchTerm);
  });
}

if (loadMoreButton) {
  loadMoreButton.addEventListener("click", () => {
    if (hasMore && activeCategory) {
      loadGames(activeCategory, currentPage + 1, true, isSearchPage ? 10 : 6);
    }
  });
}

if (isDetailPage) {
  loadGameDetails();
} else if (isSearchPage) {
  loadGames("", 1, false, 10);
  loadEpicGames();
} else {
  loadHomeGreeting();
  const selectedCategory = document.querySelector(".category-chip.is-selected");
  if (selectedCategory) {
    loadGames(selectedCategory.textContent.trim());
  }
}

async function loadHomeGreeting() {
  const nameElement = document.querySelector("[data-user-name]");
  if (!nameElement) {
    return;
  }

  try {
    const response = await fetch("/api/me", {
      headers: { Accept: "application/json" },
    });
    if (response.status === 401) {
      nameElement.hidden = true;
      return;
    }

    const user = await response.json();
    if (!response.ok) {
      throw new Error(user.error || `Request failed (${response.status}).`);
    }
    nameElement.textContent = `, “${user.username}”`;
    nameElement.hidden = false;
  } catch (error) {
    nameElement.hidden = true;
    console.error("Could not load the signed-in username.", error);
  }
}

const playerTypeGenres = {
  achievers: ["strategy", "simulation"],
  explorers: ["adventure", "rpg"],
  socializers: ["multiplayer", "casual"],
  competitors: ["action", "sport", "racing"],
};

function preferenceWords(value) {
  return value
    .toLocaleLowerCase()
    .split(/[^\p{L}\p{N}]+/u)
    .filter((word) => word.length > 2);
}

function matchesGenre(game, preference) {
  const normalizedPreference = preference.toLocaleLowerCase().trim();
  return game.genres.some((genre) => {
    const normalizedGenre = genre.toLocaleLowerCase();
    return (
      normalizedGenre === normalizedPreference ||
      normalizedGenre.includes(normalizedPreference) ||
      normalizedPreference.includes(normalizedGenre)
    );
  });
}

function scoreRecommendation(game, answers, playerGenres) {
  const searchableText =
    `${game.title} ${game.description} ${game.genres.join(" ")}`.toLocaleLowerCase();
  let score = typeof game.rating === "number" ? game.rating : 0;

  if (answers.favorite_game_genre && matchesGenre(game, answers.favorite_game_genre)) {
    score += 12;
  }
  if (playerGenres.some((genre) => matchesGenre(game, genre))) {
    score += 5;
  }

  const interestWords = [
    ...preferenceWords(answers.hobbies || ""),
    ...preferenceWords(answers.favorite_game || ""),
  ];
  for (const word of new Set(interestWords)) {
    if (searchableText.includes(word)) {
      score += 2;
    }
  }

  return score;
}

async function loadRecommendations() {
  gameGrid.setAttribute("aria-busy", "true");
  gameStatus.textContent = "Building recommendations from your questionnaire…";
  try {
    const profileResponse = await fetch("/api/me", {
      headers: { Accept: "application/json" },
    });
    const user = await profileResponse.json();
    if (!profileResponse.ok) {
      if (profileResponse.status === 401) {
        window.location.href = "profile.html";
        return;
      }
      throw new Error(user.error || `Request failed (${profileResponse.status}).`);
    }

    const storageKey = `arcade-vault-questionnaire:${user.username}`;
    const storedAnswers = localStorage.getItem(storageKey);
    const answers = storedAnswers ? JSON.parse(storedAnswers) : {};
    if (!answers || typeof answers !== "object" || Array.isArray(answers)) {
      throw new Error("Your saved questionnaire answers are invalid.");
    }
    const answeredFields = Object.values(answers).filter(
      (value) => typeof value === "string" && value.trim(),
    ).length;
    if (answeredFields < 4) {
      window.location.href = "profile.html?questionnaire=required";
      return;
    }

    const playerType = typeof answers.player_type === "string"
      ? answers.player_type.toLocaleLowerCase()
      : "";
    const playerGenres = playerTypeGenres[playerType] || [];
    const categories = [
      answers.favorite_game_genre,
      playerGenres[0],
      answers.hobbies,
      ...playerGenres.slice(1),
    ].filter((value) => typeof value === "string" && value.trim());
    const uniqueCategories = [
      ...new Set(categories.map((value) => value.trim())),
    ].slice(0, 3);
    if (!uniqueCategories.length) {
      uniqueCategories.push("");
    }

    const results = await Promise.all(
      uniqueCategories.map(async (category) => {
        const params = new URLSearchParams({ category, page: "1", limit: "10" });
        const response = await fetch(`/api/games?${params}`, {
          headers: { Accept: "application/json" },
        });
        const result = await response.json();
        if (!response.ok) {
          throw new Error(result.error || `Request failed (${response.status}).`);
        }
        return result.games;
      }),
    );
    const gamesById = new Map();
    for (const games of results) {
      for (const game of games) {
        gamesById.set(String(game.id), game);
      }
    }

    const leastFavoriteGenre =
      typeof answers.least_favorite_game_genre === "string"
        ? answers.least_favorite_game_genre.trim()
        : "";
    const leastFavoriteGame =
      typeof answers.least_favorite_game === "string"
        ? answers.least_favorite_game.trim().toLocaleLowerCase()
        : "";
    const phobiaWords = preferenceWords(
      typeof answers.phobias === "string" ? answers.phobias : "",
    );
    const recommendations = [...gamesById.values()]
      .filter((game) => !leastFavoriteGenre || !matchesGenre(game, leastFavoriteGenre))
      .filter(
        (game) =>
          !leastFavoriteGame ||
          game.title.trim().toLocaleLowerCase() !== leastFavoriteGame,
      )
      .filter((game) => {
        const searchableText =
          `${game.title} ${game.genres.join(" ")}`.toLocaleLowerCase();
        return !phobiaWords.some((word) => searchableText.includes(word));
      })
      .map((game) => ({
        game,
        score: scoreRecommendation(game, answers, playerGenres),
      }))
      .sort((left, right) => right.score - left.score)
      .slice(0, 10);

    if (!recommendations.length) {
      gameGrid.replaceChildren();
      gameStatus.textContent =
        "No matching games were found. Try changing your favorite genre or phobias in your questionnaire.";
      return;
    }

    gameGrid.replaceChildren(
      ...recommendations.map(({ game }, index) => {
        const card = renderGame(game, "", index + 1);
        card.querySelector("[data-game-category]").textContent =
          "Recommended for you";
        return card;
      }),
    );
    gameStatus.textContent =
      `Here are ${recommendations.length} games selected from your favorite genre, player type, and interests.`;
    syncFavoriteButtons();
  } catch (error) {
    gameGrid.replaceChildren();
    gameStatus.textContent = `Could not load recommendations: ${error.message}`;
    console.error("Could not load game recommendations.", error);
  } finally {
    gameGrid.setAttribute("aria-busy", "false");
  }
}

if (isRecommendationsPage) {
  loadRecommendations();
}

async function loadEpicGames(searchTerm = "") {
  const epicGrid = document.querySelector("#epic-game-grid");
  const epicStatus = document.querySelector("#epic-game-status");
  const epicTemplate = document.querySelector("#epic-game-card-template");
  if (!epicGrid || !epicStatus || !epicTemplate) {
    return;
  }
  epicGrid.setAttribute("aria-busy", "true");
  epicStatus.textContent = "Loading Epic Games Store games…";

  try {
    const params = new URLSearchParams();
    if (searchTerm) {
      params.set("search", searchTerm);
    }
    const response = await fetch(`/api/epic-games?${params}`, {
      headers: { Accept: "application/json" },
    });
    const result = await response.json();
    if (!response.ok) {
      throw new Error(result.error || `Request failed (${response.status}).`);
    }

    const cards = result.games
      .filter((game) => game.url)
      .map((game) => {
        const card = epicTemplate.content.firstElementChild.cloneNode(true);
        const image = card.querySelector("[data-epic-image]");
        const imageLink = card.querySelector("[data-epic-image-link]");
        const titleLink = card.querySelector("[data-epic-title-link]");
        imageLink.href = game.url;
        titleLink.href = game.url;
        titleLink.textContent = game.title;
        const favoriteButton = card.querySelector("[data-requires-login]");
        favoriteButton.remove();
        image.alt = `${game.title} cover`;
        if (game.cover) {
          image.src = game.cover;
        } else {
          imageLink.remove();
        }
        card.querySelector("[data-epic-description]").textContent =
          game.description || "No description is available from Epic Games Store.";
        card.querySelector("[data-epic-price]").textContent =
          game.price || "Price unavailable";
        return card;
      });
    epicGrid.replaceChildren(...cards);
    epicGrid.setAttribute("aria-busy", "false");
    epicStatus.textContent = cards.length
      ? searchTerm
        ? `Showing ${cards.length} Epic Games Store matches.`
        : `Showing ${cards.length} games from Epic Games Store's catalog feed.`
      : searchTerm
        ? `No Epic Games Store matches found for “${searchTerm}”.`
        : "No games are available from Epic Games Store right now.";
  } catch (error) {
    epicGrid.replaceChildren();
    epicGrid.setAttribute("aria-busy", "false");
    epicStatus.textContent = `Could not load Epic Games Store games: ${error.message}`;
    console.error("Could not load Epic Games Store games.", error);
  }
}

async function loadGameDetails() {
  const detailStatus = document.querySelector("#detail-status");
  const detailContent = document.querySelector("#game-detail-content");
  const params = new URLSearchParams(window.location.search);
  const gameId = params.get("id");
  if (!gameId || !/^[1-9]\d*$/.test(gameId)) {
    detailStatus.textContent = "This game link is missing a valid game ID.";
    return;
  }

  try {
    const response = await fetch(`/api/games/${encodeURIComponent(gameId)}`, {
      headers: { Accept: "application/json" },
    });
    const result = await response.json();
    if (!response.ok) {
      throw new Error(result.error || `Request failed (${response.status}).`);
    }

    document.title = `${result.title} | Arcade Vault`;
    document.querySelector("#detail-title").textContent = result.title;
    const favoriteButton = document.querySelector("#detail-favorite");
    favoriteButton.dataset.gameId = String(result.id);
    favoriteButton.dataset.gameTitle = result.title;
    favoriteButton.dataset.gameCover = result.cover || "";
    syncFavoriteButtons([favoriteButton]);
    const cover = document.querySelector("#detail-cover");
    if (result.cover) {
      cover.src = result.cover;
      cover.alt = `${result.title} cover`;
    } else {
      cover.remove();
    }

    const description = document.querySelector("#detail-description");
    description.textContent = result.description || "No description is available.";
    setDetailValue("detail-genres", result.genres);
    setDetailValue("detail-platforms", result.platforms);
    setDetailValue("detail-developers", result.developers);
    setDetailValue("detail-publishers", result.publishers);
    setDetailValue("detail-release-date", result.released);
    setDetailValue("detail-esrb-rating", result.esrb_rating);
    setDetailValue("detail-metacritic", result.metacritic);
    setDetailValue(
      "detail-rating",
      typeof result.rating === "number" ? `${result.rating.toFixed(1)} / 5` : "",
    );

    const website = document.querySelector("#detail-website");
    if (result.website) {
      try {
        const websiteUrl = new URL(result.website);
        if (websiteUrl.protocol === "http:" || websiteUrl.protocol === "https:") {
          website.href = websiteUrl.href;
          website.hidden = false;
        }
      } catch (error) {
        console.warn("RAWG returned an invalid game website URL.", error);
      }
    }
    const epicStore = document.querySelector("#detail-epic-store");
    if (result.epic?.url) {
      epicStore.href = result.epic.url;
      epicStore.textContent = result.epic.price
        ? `View on Epic Games Store · ${result.epic.price}`
        : "View on Epic Games Store";
      epicStore.hidden = false;
    }
    detailContent.hidden = false;
    detailStatus.textContent = result.warnings?.join(" ") || "";
  } catch (error) {
    detailStatus.textContent = `Could not load game details: ${error.message}`;
    console.error("Could not load game details.", error);
  }
}

function setDetailValue(id, value) {
  const element = document.querySelector(`#${id}`);
  if (Array.isArray(value)) {
    element.textContent = value.length ? value.join(", ") : "Not available";
  } else {
    element.textContent = value || "Not available";
  }
}
