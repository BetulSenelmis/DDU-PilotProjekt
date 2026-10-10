async function readAuthResponse(response) {
  const contentType = response.headers.get("Content-Type") || "";
  if (!contentType.toLowerCase().includes("application/json")) {
    const body = await response.text();
    if (body.trimStart().toLowerCase().startsWith("<!doctype html")) {
      throw new Error(
        "The server returned a web page instead of an account API response. Stop the old server and restart this project with .venv\\Scripts\\python.exe server.py, then try again.",
      );
    }
    throw new Error(
      `The account server returned an unexpected response (HTTP ${response.status}). Restart the project server and try again.`,
    );
  }

  try {
    return await response.json();
  } catch {
    throw new Error(
      "The account server returned invalid JSON. Restart the project server and try again.",
    );
  }
}

for (const form of document.querySelectorAll("[data-auth-form]")) {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const message = form.querySelector("[data-auth-message]");
    const submitButton = form.querySelector('[type="submit"]');
    const action = form.dataset.authForm;
    submitButton.disabled = true;
    message.textContent = "";

    try {
      const response = await fetch(`/api/auth/${action}`, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams(new FormData(form)),
      });
      const result = await readAuthResponse(response);
      if (!response.ok) {
        throw new Error(result.error || `Request failed (${response.status}).`);
      }

      const query = new URLSearchParams();
      const params = new URLSearchParams(window.location.search);
      for (const key of ["favorite", "title", "cover"]) {
        const value = params.get(key);
        if (value) {
          query.set(key, value);
        }
      }
      const queryString = query.toString();
      window.location.href = `profile.html${queryString ? `?${queryString}` : ""}`;
    } catch (error) {
      message.textContent = error.message;
    } finally {
      submitButton.disabled = false;
    }
  });
}
