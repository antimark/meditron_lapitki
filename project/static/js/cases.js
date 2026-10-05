// ============================================================
// DOM
// ============================================================

const jsonContent = document.getElementById("json-content");
const currentFile = document.getElementById("current-file");

const mdContent = document.getElementById("md-content");
const currentMd = document.getElementById("current-md");

const contentArea = document.getElementById("content-area");
const mdToggle = document.getElementById("md-toggle");

const casesList = document.querySelector(".cases-list");

const processButton = document.getElementById("process-button");
const mdFileInput = document.getElementById("md-file-input");
const selectedFile = document.getElementById("selected-file");
const anonymizeToggle = document.getElementById("anonymize-toggle");
const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";


// ============================================================
// СОСТОЯНИЕ
// ============================================================

// Нужен, чтобы старый fetch не перезаписал данные,
// если пользователь быстро открыл другой кейс.
let caseRequestController = null;


// ============================================================
// ПАНЕЛЬ ОРИГИНАЛЬНОГО MD
// ============================================================

function updateMdToggleState(isCollapsed) {
    if (!mdToggle) {
        return;
    }

    const label = isCollapsed
        ? "Показать оригинал"
        : "Свернуть оригинал";

    mdToggle.setAttribute("aria-label", label);
    mdToggle.setAttribute("title", label);
}


function setMdPanelCollapsed(isCollapsed) {
    if (!contentArea) {
        return;
    }

    contentArea.classList.toggle(
        "md-collapsed",
        isCollapsed
    );

    updateMdToggleState(isCollapsed);
}


function openMdPanel() {
    if (
        !contentArea ||
        !contentArea.classList.contains("md-collapsed")
    ) {
        return false;
    }

    setMdPanelCollapsed(false);

    return true;
}


if (contentArea && mdToggle) {
    mdToggle.addEventListener("click", () => {
        const isCollapsed =
            contentArea.classList.contains("md-collapsed");

        setMdPanelCollapsed(!isCollapsed);
    });
}


/*
 * Даём другим скриптам возможность
 * открыть панель оригинала.
 */
window.MdPanel = {
    open: openMdPanel,

    isCollapsed() {
        return Boolean(
            contentArea?.classList.contains("md-collapsed")
        );
    }
};


// ============================================================
// РЕНДЕР ИСТОРИИ БОЛЕЗНИ
// ============================================================

function createHistoryField(field) {
    const fieldElement = document.createElement("div");
    fieldElement.className = "history-field";

    // Используются внешними скриптами:
    // context_highlighter.js и score_badges.js
    fieldElement.dataset.fieldKey = field.key ?? "";
    fieldElement.dataset.fieldPath = field.path ?? "";
    fieldElement.dataset.rawValue = field.raw_value ?? "";
    fieldElement.dataset.description = field.description ?? "";

    if (field.description) {
        fieldElement.title = field.description;
    }


    // --------------------------------------------------------
    // Название поля
    // --------------------------------------------------------

    const label = document.createElement("span");
    label.className = "history-field-label";
    label.textContent = field.label ?? "";


    // --------------------------------------------------------
    // Значение
    // --------------------------------------------------------

    const value = document.createElement("span");
    value.className = "history-field-value";
    value.textContent = field.value ?? "";


    // --------------------------------------------------------
    // Отсутствующее значение
    // --------------------------------------------------------

    if (field.value === "не указано") {
        fieldElement.classList.add("history-field-missing");
    }


    fieldElement.append(label, value);

    return fieldElement;
}


function createHistorySection(section) {
    const sectionElement = document.createElement("section");
    sectionElement.className = "history-section";


    // --------------------------------------------------------
    // Заголовок секции
    // --------------------------------------------------------

    const title = document.createElement("div");
    title.className = "history-section-title";
    title.textContent = section.title ?? "";

    sectionElement.appendChild(title);


    // --------------------------------------------------------
    // Поля
    // --------------------------------------------------------

    const fields = Array.isArray(section.fields)
        ? section.fields
        : [];

    fields.forEach(field => {
        sectionElement.appendChild(
            createHistoryField(field)
        );
    });

    return sectionElement;
}


function renderHistory(history) {
    jsonContent.replaceChildren();

    if (!Array.isArray(history) || history.length === 0) {
        jsonContent.textContent = "Данные отсутствуют.";
        return;
    }

    const fragment = document.createDocumentFragment();

    history.forEach(section => {
        fragment.appendChild(
            createHistorySection(section)
        );
    });

    jsonContent.appendChild(fragment);
}


// ============================================================
// ОТКРЫТИЕ КЕЙСА
// ============================================================

function setActiveCase(button) {
    document
        .querySelectorAll(".case-item.active")
        .forEach(item => {
            item.classList.remove("active");
        });

    button.classList.add("active");
}


async function openCase(button) {
    const filename = button.dataset.file;

    if (!filename) {
        return;
    }


    // --------------------------------------------------------
    // Отменяем предыдущую загрузку
    // --------------------------------------------------------

    if (caseRequestController) {
        caseRequestController.abort();
    }

    caseRequestController = new AbortController();


    // --------------------------------------------------------
    // Состояние загрузки
    // --------------------------------------------------------

    setActiveCase(button);

    currentFile.textContent = filename;
    jsonContent.textContent = "Загрузка...";
    mdContent.textContent = "Загрузка...";


    try {
        const response = await fetch(
            `/api/cases/${encodeURIComponent(filename)}`,
            {
                signal: caseRequestController.signal
            }
        );

        if (!response.ok) {
            throw new Error(
                `Ошибка загрузки: ${response.status}`
            );
        }

        const data = await response.json();


        // ----------------------------------------------------
        // История болезни
        // ----------------------------------------------------

        renderHistory(data.history);


        // ----------------------------------------------------
        // Контекст в оригинальном MD
        // ----------------------------------------------------

        if (window.ContextHighlighter) {
            window.ContextHighlighter.setCase(
                data.md,
                data.context
            );
        } else {
            // Запасной вариант, если скрипт подсветки
            // по какой-либо причине не подключён.
            mdContent.textContent =
                data.md || "Оригинальный документ отсутствует.";
        }


        // ----------------------------------------------------
        // SCORE
        // ----------------------------------------------------

        if (window.ScoreBadges) {
            window.ScoreBadges.setCase(
                data.score
            );
        }


        // ----------------------------------------------------
        // Имя исходного MD
        // ----------------------------------------------------

    } catch (error) {

        // AbortError возникает при нормальном переключении
        // между кейсами и не является ошибкой интерфейса.
        if (error.name === "AbortError") {
            return;
        }

        console.error(error);

        jsonContent.textContent =
            "Ошибка загрузки истории.";

        mdContent.textContent =
            "Ошибка загрузки оригинального документа.";
    }
}


// ============================================================
// СПИСОК КЕЙСОВ
// ============================================================

function setSessionIcon(container, isCollapsed) {
    if (!container) {
        return;
    }

    const icon = document.createElement("i");

    icon.setAttribute(
        "data-lucide",
        isCollapsed ? "folder" : "folder-open"
    );

    container.replaceChildren(icon);

    if (window.lucide) {
        window.lucide.createIcons();
    }
}


function toggleSession(toggle) {
    const group = toggle.closest(".session-group");

    if (!group) {
        return;
    }

    const files = group.querySelector(".session-files");
    const icon = toggle.querySelector(".session-icon");

    const isCollapsed =
        group.classList.toggle("collapsed");

    if (files) {
        files.style.display =
            isCollapsed ? "none" : "block";
    }

    setSessionIcon(icon, isCollapsed);
}


// ============================================================
// СОБЫТИЯ СПИСКА
// ============================================================

// Используем делегирование событий вместо отдельного
// addEventListener для каждой кнопки.

if (casesList) {
    casesList.addEventListener("click", event => {

        // ----------------------------------------------------
        // Выбор кейса
        // ----------------------------------------------------

        const caseButton =
            event.target.closest(".case-item");

        if (caseButton && casesList.contains(caseButton)) {
            openCase(caseButton);
            return;
        }


        // ----------------------------------------------------
        // Сворачивание / раскрытие сессии
        // ----------------------------------------------------

        const sessionToggle =
            event.target.closest(".session-toggle");

        if (
            sessionToggle &&
            casesList.contains(sessionToggle)
        ) {
            toggleSession(sessionToggle);
        }
    });
}


// ============================================================
// BATCH UPLOAD + PROCESSING
// ============================================================

function isSupportedTextFile(file) {
    const name = file.name.toLowerCase();
    return name.endsWith(".md") || name.endsWith(".txt");
}

function setProcessingState(isProcessing, progressText = "") {
    if (!processButton) return;
    processButton.disabled = isProcessing;

    // Keep the designer icon/markup intact while updating only the label.
    const label = processButton.querySelector(".process-label");
    if (label) {
        label.textContent = isProcessing
            ? (progressText || "Обработка...")
            : "Обработать";
        label.title = isProcessing ? progressText : "";
    }
}

function makeBatchId() {
    if (window.crypto && typeof window.crypto.randomUUID === "function") {
        return window.crypto.randomUUID().replaceAll("-", "_");
    }
    return `batch_${Date.now()}_${Math.random().toString(36).slice(2, 12)}`;
}

async function postForm(url, formData) {
    const response = await fetch(url, {
        method: "POST",
        body: formData
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
        throw new Error(payload.error || `Ошибка HTTP ${response.status}`);
    }
    return payload;
}

if (processButton && mdFileInput) {
    processButton.addEventListener("click", () => mdFileInput.click());

    mdFileInput.addEventListener("change", async () => {
        const files = Array.from(mdFileInput.files);
        if (files.length === 0) return;

        const invalidFile = files.find(file => !isSupportedTextFile(file));
        if (invalidFile) {
            alert(
                `Можно выбирать только файлы .md или .txt\n\n` +
                `Недопустимый файл: ${invalidFile.name}`
            );
            mdFileInput.value = "";
            return;
        }

        const batchId = makeBatchId();
        setProcessingState(true, `Подготовка пакета: ${files.length} файлов`);
        if (selectedFile) {
            selectedFile.textContent = `Анализ структуры пакета: ${files.length} файлов`;
        }

        try {
            // Backend receives the complete cohort first. This is required for
            // clustering, probe selection and adaptive routing before extraction.
            const prepareForm = new FormData();
            files.forEach(file => prepareForm.append("files", file));
            prepareForm.append("anonymize", anonymizeToggle?.checked ? "1" : "0");
            prepareForm.append("csrf_token", csrfToken);
            prepareForm.append("batch_id", batchId);

            const prepared = await postForm("/api/batch/prepare", prepareForm);
            const items = Array.isArray(prepared.files) ? prepared.files : [];
            const defaultOrder = items.map((_, index) => index);
            const processingOrder =
                Array.isArray(prepared.processing_order) &&
                prepared.processing_order.length === items.length
                    ? prepared.processing_order
                    : defaultOrder;
            const probeIds = new Set(
                Array.isArray(prepared.probe_document_ids)
                    ? prepared.probe_document_ids
                    : []
            );

            for (let step = 0; step < processingOrder.length; step += 1) {
                const index = Number(processingOrder[step]);
                const item = items[index];
                if (!item) continue;

                const isProbe = probeIds.has(item.md_filename);
                const phase = isProbe ? "Проверка согласованности" : "Обработка";
                const progress = `${phase} ${step + 1}/${items.length}: ${item.md_filename}`;

                setProcessingState(true, progress);
                if (selectedFile) {
                    selectedFile.textContent =
                        `${phase}: ${item.md_filename} (${step + 1}/${items.length})`;
                }

                const processForm = new FormData();
                processForm.append("csrf_token", csrfToken);
                processForm.append("batch_id", prepared.batch_id || batchId);
                processForm.append("index", String(index));

                const processed = await postForm("/api/batch/process", processForm);
                if (processed?.calibration?.ready && selectedFile) {
                    const expanded =
                        processed.calibration.adaptive_policy_summary
                            ?.expanded_global_fields?.length || 0;
                    selectedFile.textContent =
                        `Калибровка завершена: адаптивный routing (${expanded} расширенных полей)`;
                }
            }

            setProcessingState(true, "Финализация score пакета...");
            if (selectedFile) {
                selectedFile.textContent = "Финализация batch-score...";
            }

            const finalForm = new FormData();
            finalForm.append("csrf_token", csrfToken);
            finalForm.append("batch_id", prepared.batch_id || batchId);
            await postForm("/api/batch/finalize", finalForm);

            if (selectedFile) {
                selectedFile.textContent = `Готово файлов: ${items.length}`;
            }
            window.location.reload();
        } catch (error) {
            console.error(error);
            if (selectedFile) {
                selectedFile.textContent = error.message || "Ошибка обработки";
            }
        } finally {
            setProcessingState(false);
            mdFileInput.value = "";
        }
    });
}

/* ============================================================
   СВОРАЧИВАНИЕ ЛЕВОЙ ПАНЕЛИ
   ============================================================ */

const sidebarToggle = document.getElementById("sidebar-toggle");
const casesPage = document.querySelector(".cases-page");

if (sidebarToggle && casesPage) {

    sidebarToggle.addEventListener("click", () => {

        const collapsed =
            casesPage.classList.toggle("sidebar-collapsed");

        sidebarToggle.innerHTML = collapsed
            ? '<i data-lucide="panel-left-open"></i>'
            : '<i data-lucide="panel-left-close"></i>';

        sidebarToggle.setAttribute(
            "aria-label",
            collapsed
                ? "Показать левую панель"
                : "Свернуть левую панель"
        );

        sidebarToggle.title = collapsed
            ? "Показать левую панель"
            : "Свернуть левую панель";

        lucide.createIcons();
    });

}