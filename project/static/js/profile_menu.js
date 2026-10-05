// ============================================================
// DOM
// ============================================================

const profileSection =
    document.getElementById("profile-section");

const profileMenuToggle =
    document.getElementById("profile-menu-toggle");

const profileMenu =
    document.getElementById("profile-menu");


// ============================================================
// СОСТОЯНИЕ МЕНЮ
// ============================================================

function setProfileMenuOpen(isOpen) {
    if (!profileMenu || !profileMenuToggle) {
        return;
    }

    profileMenu.hidden = !isOpen;

    profileMenuToggle.setAttribute(
        "aria-expanded",
        String(isOpen)
    );
}


function toggleProfileMenu() {
    if (!profileMenu) {
        return;
    }

    setProfileMenuOpen(profileMenu.hidden);
}


// ============================================================
// КЛИК ПО АВАТАРУ
// ============================================================

if (profileMenuToggle) {
    profileMenuToggle.addEventListener("click", event => {
        event.stopPropagation();

        toggleProfileMenu();
    });
}


// ============================================================
// КЛИК ВНУТРИ МЕНЮ
// ============================================================

if (profileMenu) {
    profileMenu.addEventListener("click", event => {
        event.stopPropagation();

        const item =
            event.target.closest(".profile-menu-item");

        if (!item) {
            return;
        }

        const action = item.dataset.profileAction;

        // Пока страницы Profile / Settings не подключены.
        // Здесь позже можно добавить соответствующие действия.

        if (
            action === "profile" ||
            action === "settings"
        ) {
            setProfileMenuOpen(false);
        }
    });
}


// ============================================================
// ЗАКРЫТИЕ ПРИ КЛИКЕ СНАРУЖИ
// ============================================================

document.addEventListener("click", event => {
    if (
        profileSection &&
        !profileSection.contains(event.target)
    ) {
        setProfileMenuOpen(false);
    }
});


// ============================================================
// ЗАКРЫТИЕ ПО ESC
// ============================================================

document.addEventListener("keydown", event => {
    if (event.key === "Escape") {
        setProfileMenuOpen(false);

        profileMenuToggle?.focus();
    }
});