(() => {
    const historyContent = document.getElementById("json-content");
    const mdContent = document.getElementById("md-content");

    if (!historyContent || !mdContent) {
        console.error("ContextHighlighter: элементы интерфейса не найдены");
        return;
    }

    let mdText = "";
    let contexts = {};

    // Номер последнего запроса на подсветку.
    // Нужен, чтобы старые requestAnimationFrame / таймеры
    // не вмешивались после нового клика.
    let highlightRevision = 0;

    let pendingRafs = [];
    let repaintTimer = null;

    // Если Safari всё равно будет иногда плохо отрисовывать
    // подсветку во время анимации, поменяй "smooth" на "auto".
    const SCROLL_BEHAVIOR = "smooth";


    function cancelPendingWork() {
        pendingRafs.forEach(id => cancelAnimationFrame(id));
        pendingRafs = [];

        if (repaintTimer !== null) {
            clearTimeout(repaintTimer);
            repaintTimer = null;
        }
    }


    function nextFrame(callback) {
        const id = requestAnimationFrame(() => {
            pendingRafs = pendingRafs.filter(
                rafId => rafId !== id
            );

            callback();
        });

        pendingRafs.push(id);

        return id;
    }


    function getEvidenceRange(context) {
        if (!context) {
            return null;
        }

        const start = Number(context.evidence_start);
        const end = Number(context.evidence_end);

        if (
            !Number.isFinite(start) ||
            !Number.isFinite(end) ||
            start < 0 ||
            end <= start ||
            start >= mdText.length
        ) {
            return null;
        }

        return {
            start,
            end: Math.min(end, mdText.length)
        };
    }


    function findScrollContainer(element) {
        let parent = element.parentElement;

        while (
            parent &&
            parent !== document.body
        ) {
            const style = getComputedStyle(parent);

            const overflowY = style.overflowY;

            const canScroll =
                (
                    overflowY === "auto" ||
                    overflowY === "scroll" ||
                    overflowY === "overlay"
                ) &&
                parent.scrollHeight > parent.clientHeight;

            if (canScroll) {
                return parent;
            }

            parent = parent.parentElement;
        }

        return (
            document.scrollingElement ||
            document.documentElement
        );
    }


    function stopCurrentSmoothScroll(scroller) {
        // Если пользователь быстро кликнул по другому полю,
        // сначала останавливаем предыдущую smooth-прокрутку.

        if (
            scroller === document.scrollingElement ||
            scroller === document.documentElement ||
            scroller === document.body
        ) {
            window.scrollTo({
                top: window.scrollY,
                left: window.scrollX,
                behavior: "auto"
            });

            return;
        }

        scroller.scrollTo({
            top: scroller.scrollTop,
            left: scroller.scrollLeft,
            behavior: "auto"
        });
    }


    function scrollMarkToCenter(mark) {
        const scroller =
            findScrollContainer(mark);

        stopCurrentSmoothScroll(scroller);

        const markRect =
            mark.getBoundingClientRect();


        // Если скроллится вся страница
        if (
            scroller === document.scrollingElement ||
            scroller === document.documentElement ||
            scroller === document.body
        ) {
            const targetTop =
                window.scrollY +
                markRect.top -
                (window.innerHeight / 2) +
                (markRect.height / 2);

            window.scrollTo({
                top: Math.max(0, targetTop),
                behavior: SCROLL_BEHAVIOR
            });

            return;
        }


        // Если скроллится конкретная правая панель
        const scrollerRect =
            scroller.getBoundingClientRect();

        const targetTop =
            scroller.scrollTop +
            (markRect.top - scrollerRect.top) -
            (scroller.clientHeight / 2) +
            (markRect.height / 2);

        scroller.scrollTo({
            top: Math.max(0, targetTop),
            behavior: SCROLL_BEHAVIOR
        });
    }


    function forceRepaint(element) {
        /*
            Safari иногда плохо перерисовывает background
            у многострочного inline-элемента во время smooth scroll.

            Незаметно меняем opacity, заставляя браузер
            пересоздать слой и полностью перерисовать mark.
        */

        const oldOpacity =
            element.style.opacity;

        element.style.opacity = "0.999";

        // Принудительный layout
        void element.getBoundingClientRect();

        nextFrame(() => {
            if (!element.isConnected) {
                return;
            }

            element.style.opacity =
                oldOpacity;
        });
    }


    function renderHighlight(start, end) {
        const fragment =
            document.createDocumentFragment();


        // Текст до выделения
        fragment.appendChild(
            document.createTextNode(
                mdText.slice(0, start)
            )
        );


        // Само выделение
        const mark =
            document.createElement("mark");

        mark.className =
            "md-context-highlight";

        mark.textContent =
            mdText.slice(start, end);


        /*
            Особенно полезно для Safari,
            когда выделение занимает несколько строк.
        */
        mark.style.webkitBoxDecorationBreak =
            "clone";

        mark.style.boxDecorationBreak =
            "clone";


        fragment.appendChild(mark);


        // Текст после выделения
        fragment.appendChild(
            document.createTextNode(
                mdText.slice(end)
            )
        );


        /*
            replaceChildren стабильнее,
            чем:

            innerHTML = ""
            appendChild(...)
            appendChild(...)
        */
        mdContent.replaceChildren(fragment);


        return mark;
    }


    function highlightField(key) {
        const context = contexts[key];
        const range = getEvidenceRange(context);

        console.log("Клик по полю:", key);
        console.log("Контекст:", context);

        if (!range) {
            console.log("У поля нет корректного evidence");
            return;
        }

        // Если оригинал свёрнут — раскрываем его.
        const panelWasOpened =
            window.MdPanel?.open() === true;

        cancelPendingWork();
        const revision = ++highlightRevision;

        const render = () => {
            if (revision !== highlightRevision) {
                return;
            }

            const mark = renderHighlight(
                range.start,
                range.end
            );

            void mark.getBoundingClientRect();
            void mdContent.getBoundingClientRect();

            nextFrame(() => {
                if (
                    revision !== highlightRevision ||
                    !mark.isConnected
                ) {
                    return;
                }

                nextFrame(() => {
                    if (
                        revision !== highlightRevision ||
                        !mark.isConnected
                    ) {
                        return;
                    }

                    scrollMarkToCenter(mark);
                    forceRepaint(mark);

                    repaintTimer = setTimeout(() => {
                        repaintTimer = null;

                        if (
                            revision === highlightRevision &&
                            mark.isConnected
                        ) {
                            forceRepaint(mark);
                        }
                    }, 450);
                });
            });
        };

        if (panelWasOpened) {
            setTimeout(render, 220);
        } else {
            render();
        }
    }

    /*
        Один обработчик клика
        на всю левую панель.
    */
    historyContent.addEventListener(
        "click",
        event => {

            const field =
                event.target.closest(
                    ".history-field"
                );


            if (
                !field ||
                !historyContent.contains(field)
            ) {
                return;
            }


            const key =
                field.dataset.fieldKey;


            if (!key) {
                return;
            }


            highlightField(key);
        }
    );


    /*
        Публичный интерфейс.
    */
    window.ContextHighlighter = {

        setCase(newMd, newContexts) {

            /*
                На всякий случай отменяем подсветку
                предыдущего пациента.
            */
            cancelPendingWork();

            ++highlightRevision;


            mdText =
                typeof newMd === "string"
                    ? newMd
                    : "";


            contexts =
                newContexts &&
                typeof newContexts === "object"
                    ? newContexts
                    : {};


            console.log(
                "ContextHighlighter загружен"
            );

            console.log(
                "Количество контекстов:",
                Object.keys(contexts).length
            );


            /*
                Возвращаем обычный MD-текст,
                без старого mark.
            */
            mdContent.textContent =
                mdText;


            /*
                Отмечаем поля слева,
                для которых существует evidence.

                Важно сначала удалить старый has-context,
                потому что при переключении пациента
                он мог остаться от предыдущего кейса.
            */
            document
                .querySelectorAll(".history-field")
                .forEach(field => {

                    field.classList.remove(
                        "has-context"
                    );


                    const key =
                        field.dataset.fieldKey;


                    const range =
                        getEvidenceRange(
                            contexts[key]
                        );


                    if (range) {
                        field.classList.add(
                            "has-context"
                        );
                    }

                });
        }
    };

})();