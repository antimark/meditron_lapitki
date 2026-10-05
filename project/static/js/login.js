const authModeToggle = document.getElementById("auth-mode-toggle");
const authMode = document.getElementById("auth-mode");
const authTitle = document.getElementById("auth-title");
const authDescription = document.getElementById("auth-description");
const authSubmit = document.getElementById("auth-submit");
const secretGroup = document.getElementById("registration-secret-group");
const secretInput = document.getElementById("registration-secret");
const passwordInput = document.getElementById("password");

function applyAuthMode(registering) {
    authMode.value = registering ? "register" : "login";
    authTitle.textContent = registering ? "Регистрация" : "Вход";
    authDescription.textContent = registering
        ? "Создайте пользователя для работы с историями болезни."
        : "Войдите в систему для работы с историями болезни.";
    authSubmit.textContent = registering ? "Зарегистрироваться" : "Войти";
    secretGroup.classList.toggle("is-hidden", !registering);
    secretInput.required = registering;
    passwordInput.autocomplete = registering ? "new-password" : "current-password";
}

if (authModeToggle) {
    authModeToggle.addEventListener("change", () => applyAuthMode(authModeToggle.checked));
    applyAuthMode(authModeToggle.checked);
}
