(() => {
    const historyContent = document.getElementById("json-content");

    if (!historyContent) {
        console.error("ScoreBadges: основная панель не найдена");
        return;
    }

    let fieldScores = {};

    const LABEL_CONFIG = {
        bad: {
            icon: "circle-x",
            className: "field-score-bad",
            title: "Низкая достоверность"
        },

        suspicious: {
            icon: "triangle-alert",
            className: "field-score-suspicious",
            title: "Требует проверки"
        }
    };


    function clearBadges() {
        historyContent
            .querySelectorAll(".field-score-badge")
            .forEach(element => element.remove());
    }


    function createBadge(label) {
        const config = LABEL_CONFIG[label];

        if (!config) {
            return null;
        }

        const badge = document.createElement("span");

        badge.className =
            `field-score-badge ${config.className}`;

        badge.title = config.title;
        badge.setAttribute("aria-label", config.title);

        const icon = document.createElement("i");

        icon.setAttribute(
            "data-lucide",
            config.icon
        );

        icon.setAttribute(
            "aria-hidden",
            "true"
        );

        badge.appendChild(icon);

        return badge;
    }


    function renderBadges() {
        clearBadges();

        const fields =
            historyContent.querySelectorAll(
                ".history-field[data-field-key]"
            );

        fields.forEach(field => {
            const key = field.dataset.fieldKey;

            if (!key) {
                return;
            }

            const score = fieldScores[key];

            if (!score) {
                return;
            }

            const label = score.label;

            // Для good ничего не показываем
            if (
                label !== "bad" &&
                label !== "suspicious"
            ) {
                return;
            }

            const badge = createBadge(label);

            if (!badge) {
                return;
            }

            field.appendChild(badge);
        });


        // Lucide заменяет <i> на SVG
        if (
            window.lucide &&
            typeof window.lucide.createIcons === "function"
        ) {
            window.lucide.createIcons();
        } else {
            console.warn(
                "ScoreBadges: библиотека Lucide не подключена"
            );
        }
    }


    window.ScoreBadges = {

        setCase(scoreData) {

            if (
                scoreData &&
                typeof scoreData === "object" &&
                scoreData.field_quality &&
                typeof scoreData.field_quality === "object"
            ) {
                fieldScores =
                    scoreData.field_quality;
            } else {
                fieldScores = {};
            }

            renderBadges();

            console.log(
                "ScoreBadges загружен:",
                Object.keys(fieldScores).length,
                "полей"
            );
        },

        clear() {
            fieldScores = {};
            clearBadges();
        }
    };
})();