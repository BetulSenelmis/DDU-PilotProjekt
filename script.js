for (const toggleButton of document.querySelectorAll(".toggle-password")) {
  const passwordInput = toggleButton.parentElement.querySelector('input[type="password"]');
  if (!passwordInput) {
    continue;
  }
  toggleButton.addEventListener("click", () => {
    const isPassword = passwordInput.type === "password";
    passwordInput.type = isPassword ? "text" : "password";
    toggleButton.setAttribute("aria-pressed", String(isPassword));
    toggleButton.setAttribute("aria-label", isPassword ? "Hide password" : "Show password");
  });
}
