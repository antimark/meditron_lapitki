const themeButton =
    document.getElementById("theme-toggle");


function updateThemeButton() {

    if (!themeButton) {
        return;
    }

    const isDark =
        document.documentElement.dataset.theme === "dark";


    if (isDark) {

        themeButton.innerHTML =
            '<i data-lucide="sun"></i>';

        themeButton.setAttribute(
            "aria-label",
            "Включить светлую тему"
        );

        themeButton.setAttribute(
            "title",
            "Светлая тема"
        );

    } else {

        themeButton.innerHTML =
            '<i data-lucide="moon"></i>';

        themeButton.setAttribute(
            "aria-label",
            "Включить тёмную тему"
        );

        themeButton.setAttribute(
            "title",
            "Тёмная тема"
        );

    }


    if (window.lucide) {
        lucide.createIcons();
    }
}


if (themeButton) {

    themeButton.addEventListener("click", () => {

        const isDark =
            document.documentElement.dataset.theme === "dark";

        if (isDark) {

            document.documentElement.dataset.theme = "light";

            localStorage.setItem(
                "theme",
                "light"
            );

        } else {

            document.documentElement.dataset.theme = "dark";

            localStorage.setItem(
                "theme",
                "dark"
            );

        }

        updateThemeButton();

    });

}


updateThemeButton();