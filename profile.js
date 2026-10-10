const profileGate = document.querySelector("#profile-gate");
const authenticatedProfile = document.querySelector("#authenticated-profile");
const profileFeedback = document.querySelector("#profile-feedback");
const profileForm = document.querySelector("#profile-form");
const passwordForm = document.querySelector("#password-form");
const favoritesGrid = document.querySelector("#favorite-games-grid");
const emptyFavorites = document.querySelector("#favorites-empty");
const queryParams = new URLSearchParams(window.location.search);
const pendingFavoriteId = queryParams.get("favorite");
const pendingFavoriteTitle = queryParams.get("title");
const pendingFavoriteCover = queryParams.get("cover");

function loginHref() {
  const params = new URLSearchParams();
  if (pendingFavoriteId && pendingFavoriteTitle) {
    params.set("favorite", pendingFavoriteId);
    params.set("title", pendingFavoriteTitle);
    if (pendingFavoriteCover) {
      params.set("cover", pendingFavoriteCover);
    }
  }
  const query = params.toString();
  return `login.html${query ? `?${query}` : ""}`;
}

async function loadProfile() {
  try {
    const response = await fetch("/api/me", {
      headers: { Accept: "application/json" },
    });
    if (response.status === 401) {
      document.querySelector("#profile-login-link").href = loginHref();
      document.querySelector("#profile-return-message").hidden =
        !pendingFavoriteId;
      return;
    }
    const user = await response.json();
    if (!response.ok) {
      throw new Error(user.error || `Request failed (${response.status}).`);
    }
    showProfile(user);
    if (pendingFavoriteId && pendingFavoriteTitle) {
      await addPendingFavorite();
    }
  } catch (error) {
    document.querySelector("#profile-return-message").hidden = false;
    document.querySelector("#profile-return-message").textContent =
      `Could not load your profile: ${error.message}`;
    console.error("Could not load the user profile.", error);
  }
}

function showProfile(user) {
  profileGate.hidden = true;
  document.querySelector("#profile-return-message").hidden = true;
  authenticatedProfile.hidden = false;
  document.querySelector("#profile-name").textContent = user.name;
  document.querySelector("#profile-username").textContent = user.username;
  document.querySelector("#profile-email").textContent = user.email;
  profileForm.elements.name.value = user.name;
  profileForm.elements.email.value = user.email;
  renderFavorites(user.favorites);
  if (queryParams.get("questionnaire") === "required") {
    openQuestionnaire();
    questionnaireFeedback.textContent =
      "Please complete at least 4 questionnaire fields to see your recommendations.";
  }
}

function renderFavorites(favorites) {
  emptyFavorites.hidden = favorites.length > 0;
  const cards = favorites.map((game) => {
    const card = document.createElement("article");
    card.className = "game-card";

    if (game.cover) {
      const imageLink = document.createElement("a");
      imageLink.className = "game-card-image-link";
      imageLink.href = `game-detail.html?id=${encodeURIComponent(game.id)}`;
      const image = document.createElement("img");
      image.className = "game-card-image";
      image.src = game.cover;
      image.alt = `${game.title} cover`;
      imageLink.append(image);
      card.append(imageLink);
    }

    const heading = document.createElement("h3");
    heading.className = "game-card-title";
    const titleLink = document.createElement("a");
    titleLink.href = `game-detail.html?id=${encodeURIComponent(game.id)}`;
    titleLink.textContent = game.title;
    heading.append(titleLink);
    card.append(heading);

    const removeButton = document.createElement("button");
    removeButton.className = "favorite-remove";
    removeButton.type = "button";
    removeButton.textContent = "Remove favorite";
    removeButton.addEventListener("click", () => removeFavorite(game.id));
    card.append(removeButton);
    return card;
  });
  favoritesGrid.replaceChildren(...cards);
}

async function addPendingFavorite() {
  const gameId = Number(pendingFavoriteId);
  if (!Number.isSafeInteger(gameId) || gameId < 1 || pendingFavoriteTitle.length > 160) {
    profileFeedback.textContent = "Could not save this game: invalid favorite details.";
    return;
  }
  try {
    const response = await fetch(`/api/favorites/${gameId}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({
        title: pendingFavoriteTitle,
        cover: pendingFavoriteCover || "",
      }),
    });
    const result = await response.json();
    if (!response.ok) {
      throw new Error(result.error || `Request failed (${response.status}).`);
    }
    profileFeedback.textContent = `${pendingFavoriteTitle} was added to your favorites.`;
    await loadProfileWithoutPendingFavorite();
    window.history.replaceState({}, "", "profile.html");
  } catch (error) {
    profileFeedback.textContent = `Could not save favorite: ${error.message}`;
    console.error("Could not save pending favorite.", error);
  }
}

async function loadProfileWithoutPendingFavorite() {
  const response = await fetch("/api/me", {
    headers: { Accept: "application/json" },
  });
  const user = await response.json();
  if (!response.ok) {
    throw new Error(user.error || `Request failed (${response.status}).`);
  }
  renderFavorites(user.favorites);
}

async function removeFavorite(gameId) {
  try {
    const response = await fetch(`/api/favorites/${encodeURIComponent(gameId)}`, {
      method: "DELETE",
      headers: { Accept: "application/json" },
    });
    const result = await response.json();
    if (!response.ok) {
      throw new Error(result.error || `Request failed (${response.status}).`);
    }
    profileFeedback.textContent = "Favorite removed.";
    await loadProfileWithoutPendingFavorite();
  } catch (error) {
    profileFeedback.textContent = `Could not remove favorite: ${error.message}`;
  }
}

profileForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const response = await fetch("/api/profile", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams(new FormData(profileForm)),
    });
    const result = await response.json();
    if (!response.ok) {
      throw new Error(result.error || `Request failed (${response.status}).`);
    }
    showProfile(result.user);
    profileFeedback.textContent = "Profile information saved.";
  } catch (error) {
    profileFeedback.textContent = `Could not save profile: ${error.message}`;
  }
});

passwordForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const response = await fetch("/api/password", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams(new FormData(passwordForm)),
    });
    const result = await response.json();
    if (!response.ok) {
      throw new Error(result.error || `Request failed (${response.status}).`);
    }
    passwordForm.reset();
    passwordForm.closest("details").open = false;
    profileFeedback.textContent = "Password changed.";
  } catch (error) {
    profileFeedback.textContent = `Could not change password: ${error.message}`;
  }
});

document.querySelector("#logout-button").addEventListener("click", async () => {
  try {
    const response = await fetch("/api/auth/logout", { method: "POST" });
    if (!response.ok) {
      throw new Error(`Request failed (${response.status}).`);
    }
    window.location.replace("login.html");
  } catch (error) {
    profileFeedback.textContent = `Could not log out: ${error.message}`;
  }
});

const questionnaireButton = document.querySelector("#questionnaire-button");
const questionnairePanel = document.querySelector("#questionnaire-panel");
const questionnaireFeedback = document.querySelector("#questionnaire-feedback");
const questionnaireSaveButton = document.querySelector("#questionnaire-save");
const questionnaireCloseButton = document.querySelector("#questionnaire-close");

function openQuestionnaire() {
  questionnaireButton.setAttribute("aria-expanded", "true");
  questionnairePanel.hidden = false;
  const storageKey = `arcade-vault-questionnaire:${document.querySelector("#profile-username").textContent}`;
  try {
    const savedAnswers = localStorage.getItem(storageKey);
    if (savedAnswers) {
      const answers = JSON.parse(savedAnswers);
      for (const [name, value] of Object.entries(answers)) {
        if (questionnairePanel.elements[name]) {
          questionnairePanel.elements[name].value = value;
        }
      }
    } else {
      questionnairePanel.elements.name.value =
        document.querySelector("#profile-name").textContent;
    }
  } catch (error) {
    questionnaireFeedback.textContent =
      `Could not load questionnaire answers: ${error.message}`;
    console.error("Could not load questionnaire answers.", error);
  }
  questionnairePanel.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function closeQuestionnaire() {
  questionnaireButton.setAttribute("aria-expanded", "false");
  questionnairePanel.hidden = true;
}

questionnaireButton.addEventListener("click", () => {
  const isExpanded = questionnaireButton.getAttribute("aria-expanded") === "true";
  if (isExpanded) {
    closeQuestionnaire();
  } else {
    openQuestionnaire();
  }
});

questionnaireCloseButton.addEventListener("click", closeQuestionnaire);

function saveQuestionnaire() {
  const answers = Object.fromEntries(new FormData(questionnairePanel).entries());
  const answeredFields = Object.values(answers).filter((value) =>
    value.trim(),
  ).length;
  if (answeredFields < 4) {
    questionnaireFeedback.textContent = "Please fill out at least 4 fields.";
    return false;
  }

  const storageKey = `arcade-vault-questionnaire:${document.querySelector("#profile-username").textContent}`;
  try {
    localStorage.setItem(storageKey, JSON.stringify(answers));
    return true;
  } catch (error) {
    questionnaireFeedback.textContent =
      `Could not save questionnaire answers: ${error.message}`;
    console.error("Could not save questionnaire answers.", error);
    return false;
  }
}

questionnaireSaveButton.addEventListener("click", () => {
  if (saveQuestionnaire()) {
    questionnaireFeedback.textContent = "Questionnaire saved.";
  }
});

questionnairePanel.addEventListener("submit", (event) => {
  event.preventDefault();
  if (saveQuestionnaire()) {
    questionnaireFeedback.textContent = "Questionnaire saved.";
  }
});

loadProfile();
