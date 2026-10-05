# Cardio Hybrid Extraction Pipeline

Репозиторий содержит MVP для преобразования синтетических кардиологических выписных эпикризов (`.md`/`.txt`) в структурированный JSON-контракт кейса: 9 клинических групп и 50 строковых полей. Архитектура rules-first и evidence-bound: детерминированные правила извлекают значения и точные фрагменты-основания, а локальная и внешняя LLM подключаются опционально только как дополнительные источники кандидатов.

Главный принцип: система должна извлекать документированный факт, а не ставить диагноз, вычислять отсутствующие показатели или переносить значение из недопустимого клинического контекста.

## 1. Что реализовано

- Детерминированный regex/normalization extractor для 50 полей.
- Разделение документа на клинические секции с защитой от «протекания» через неизвестные заголовки.
- Field-specific source restrictions через `config/field_specs.json`.
- Отдельные case-first обработчики для сложных полей: ICD, ТЛТ, факт КАГ, стенозы LAD/RCA, терапия при выписке.
- Conservative typo layer: исправления используются только в shadow-копии, evidence проецируется обратно на оригинальный текст.
- Опциональная локальная LLM и OpenAI-compatible API.
- Проверка схемы LLM-ответа и точного evidence.
- Arbitration между regex / typo-regex / local / API кандидатами.
- Per-field и file-level scoring.
- Batch orchestration: preview всех документов, clustering, выбор probe-файлов, сравнение rules/API, adaptive routing.
- Peer/cohort signal как ограниченный tie-break/penalty, но не как источник клинических значений.
- Flask backend, HTML/CSS/JS frontend, авторизация, CSRF, история сессий, ZIP-выгрузка.
- Docker deployment.
- CLI для одиночных файлов, папок, manifest batch и long-lived workers.
- Подготовленная инфраструктура для дообучения малых LLM: сбор SFT dataset, LoRA/QLoRA, benchmark и evaluation.
- Pytest regression suite и контрольный batch-прогон.

## 2. Структура репозитория

```text
.
├── README.md
├── .gitignore
├── project/
│   ├── app.py                       # Flask backend и web API
│   ├── processor.py                 # bridge web -> pipeline/orchestrator
│   ├── worker.py                    # long-lived JSONL worker
│   ├── process_one.py               # CLI одного файла
│   ├── process_batch.py             # manifest batch + ThreadPool workers
│   ├── orchestrators/
│   │   ├── process_folder.py        # folder batch orchestration
│   │   └── compare_models.py        # сравнение local/API/hybrid runs
│   ├── src/
│   │   ├── pipeline.py              # основной hybrid processing pipeline
│   │   ├── sections.py              # разметка секций и границ
│   │   ├── regex_extractor.py       # deterministic medical extraction
│   │   ├── normalizers.py           # допустимые нормализации
│   │   ├── typo.py                  # typo shadow layer
│   │   ├── llm_common.py            # field cards, prompt, evidence validation
│   │   ├── local_extractor.py       # локальная LLM
│   │   ├── api_extractor.py         # OpenAI-compatible API
│   │   ├── arbitration.py           # arbitration + scoring
│   │   ├── validators.py            # format/cross-field validation
│   │   ├── batch_orchestrator.py    # probes и adaptive routing
│   │   ├── batch_similarity.py      # clustering/peer signals
│   │   └── anonymizer.py            # optional anonymization
│   ├── config/                      # runtime profiles, contracts, patterns
│   │   └── expert_sources/          # исходные экспертные таблицы правил
│   ├── scripts/                     # setup/evaluation/training scripts
│   ├── tests/                       # pytest
│   ├── examples/                    # минимальный demo input
│   ├── templates/                   # frontend HTML
│   ├── static/                      # frontend JS/CSS/images
│   ├── Dockerfile
│   ├── docker-compose.yml
│   └── .env.example
└── control_results/
    ├── inputs/                      # 10 контрольных документов
    ├── result/                      # финальные JSON
    ├── context/                     # evidence/context artifacts
    ├── score/                       # QA/routing artifacts
    └── run_summary.json
```

## 3. Полный processing pipeline

```text
input .md/.txt
      |
      v
validation / optional anonymization
      |
      v
section splitting + unknown-heading boundaries
      |
      v
rules-first deterministic preview
      |-- regex candidates
      |-- normalizers
      |-- special case-first extractors
      |-- typo-shadow regex candidates
      |
      +------------------------------+
      | batch mode                   |
      |                              v
      |                    all-document preview
      |                              |
      |                    TF-IDF/SVD/HDBSCAN
      |                              |
      |                    representative probes
      |                              |
      |                  rules <-> API comparison
      |                              |
      |                    adaptive routing policy
      +------------------------------+
      |
      +--> optional local LLM
      +--> optional API LLM
      |
      v
schema/evidence validation
      |
      v
candidate arbitration
      |
      v
cross-field validation
      |
      v
result.json + context.json + score.json
      |
      v
optional bounded batch consistency score adjustment
```

### 3.1 Секции и сегментация текста

`src/sections.py` распознаёт известные клинические заголовки через `config/section_aliases.json` и строит диапазоны `(start, end, text)`. Для каждого поля `config/field_specs.json` задаёт разрешённые `source_sections`.

Дополнительно parser рассматривает очевидный неизвестный standalone-заголовок, например `Прочее:`, как границу. Такой заголовок не создаёт новую semantic section, но завершает предыдущую. Это предотвращает ситуацию, когда секция `Диагноз` случайно продолжается через весь оставшийся документ.

### 3.2 Regex extraction и case-first rules

`src/regex_extractor.py` возвращает `Candidate`: `field`, `value`, `source`, `confidence`, exact evidence span и metadata. Большинство полей извлекается только из разрешённых секций.

Специальная логика используется для полей, где простой `first regex match` недостаточен:

- `diagnosis_icd` — несколько ICD-кандидатов, бонус контексту основного диагноза;
- `ca_fact` — `Y` выполнена КАГ, `R` отказ, `N` нет подтверждения;
- `ca_lad` / `rca` — отдельные vessel segments, evidence включает название сосуда, выбирается максимальная документированная степень стеноза;
- лекарства — сначала explicit discharge windows, чтобы не смешивать стационарную и выписную терапию;
- отрицания — проверяются в локальном clause/context;
- absence policy применяется только после отсутствия прямого валидного кандидата.

### 3.3 Отдельная логика TLT

`tlt` больше не обрабатывается обычным section-bound generic regex. Для него используется `_extract_tlt()` с episode-wide scope.

Причина: выполненная тромболитическая терапия может быть описана в `Лечение`, `Анамнез заболевания`, `Течение госпитализации`, `Лечение и исход` или в другом нарративном блоке.

Положительный `tlt=1` требует сильного факта выполнения:

- `проведена ТЛТ`;
- `проведён тромболизис`;
- `выполнена тромболитическая терапия`;
- `введена тенектеплаза/альтеплаза/...` и аналогичные конструкции.

Не считаются ТЛТ: ЧКВ, стентирование, тромбоаспирация, показание/план на ТЛТ. Явные отрицания дают `0`; при отсутствии упоминания используется контрактная `zero_if_unmentioned` policy.

TLT scope согласован на всех слоях:

1. `regex_extractor.py` ищет performed event по всему эпикризу;
2. `field_specs.json` задаёт `source_sections=["episode"]`;
3. `llm_common.py` выделяет episode-wide поле в отдельный LLM batch, чтобы whole-document TLT context не расширял секции остальных диагнозов;
4. typo shadow вызывает тот же TLT extractor на исправленной копии и проецирует evidence обратно;
5. `deterministic_preview()` использует ту же логику, поэтому TLT участвует в batch clustering/extraction masks и peer priors;
6. `batch_similarity.peer_value_fields` содержит `tlt`.

### 3.4 Typo layer

Если `pipeline.use_typo_layer=true`, строится исправленная shadow-версия. Кандидаты из неё получают source `typo_regex`, меньшую confidence и evidence, спроецированное на исходный документ. Исправленный текст не заменяет оригинал и не становится самостоятельным evidence.

### 3.5 Опциональные LLM layers

`src/llm_common.py` формирует FIELD_CARDS только для реально запрашиваемых полей. Карточка содержит допустимые секции, selection policy, missing policy, instruction и allowed values.

LLM должна вернуть только:

```json
{
  "field_name": {
    "value": "string",
    "evidence": "exact continuous substring"
  }
}
```

Проверяются:

- точный набор ключей;
- строковый тип;
- формат/closed values;
- допустимость absence code;
- наличие evidence в исходном тексте.

При `require_exact_llm_evidence=true` LLM-кандидат с неподтверждённой цитатой не может получить нормальный arbitration score.

## 4. Batch orchestration

Batch orchestration находится в `src/batch_orchestrator.py` и `src/batch_similarity.py`.

### 4.1 Prepare phase

Перед дорогой обработкой всех документов система делает deterministic preview для всего batch. На его основе строятся:

- extraction masks;
- частотные признаки;
- TF-IDF n-grams;
- SVD semantic vectors;
- HDBSCAN clusters;
- clinical-neighbor graph по высокоуверенным структурированным полям.

Количество кластеров заранее не фиксируется.

### 4.2 Probe selection

По умолчанию выбирается 5 probe-файлов. Sampling комбинирует:

- cluster representatives/medoids;
- template-shift / необычные extraction masks;
- farthest-first diversity.

Probe-файлы обрабатываются первыми. В hybrid режиме на них rules сравниваются с API по evidence-supported значениям.

События сравнения:

- `agree` — rules и API совпали;
- `regex_coverage_gap` — rules не дали прямой value, API дал валидный evidence-bound value;
- `api_coverage_gap` — rules нашли значение, API нет;
- `semantic_conflict` — оба источника нашли валидные, но разные значения.

Если probe statistics показывают систематический regex gap или конфликт для поля/кластера, adaptive policy расширяет LLM routing именно там. Это позволяет не отправлять все 50 полей каждого документа в дорогую модель.

### 4.3 Processing остальных файлов

После probes оставшиеся документы получают routing policy с учётом:

- обычной uncertainty;
- global field policy;
- cluster-specific policy;
- batch train/test template shift;
- provider availability.

### 4.4 Finalize phase

После получения всех документов выполняется только score-level consistency adjustment. Cohort statistics не создают и не подменяют клиническое значение.

## 5. Workers и параллелизм

В проекте есть три уровня выполнения.

### Web deployment

Docker запускает Gunicorn:

```text
1 process × 4 threads
```

Один Gunicorn process выбран намеренно: встроенная Transformers-модель хранится в памяти процесса, поэтому несколько Gunicorn workers дублировали бы веса и резко увеличивали RAM/VRAM. Внутри `LocalExtractor` генерация защищена `threading.Lock`, то есть одна in-process local model не выполняет несколько `generate()` одновременно.

Web UI обрабатывает один batch в порядке `prepare -> probes -> remaining -> finalize`. `static/js/cases.js` отправляет `/api/batch/process` последовательно, чтобы adaptive policy была определена probe-результатами до следующих файлов и пользователь видел детерминированный прогресс.

### Folder/manifest CLI

`orchestrators/process_folder.py` и `process_batch.py`:

1. сначала последовательно выполняют selected probes;
2. обновляют calibration/adaptive policy;
3. оставшиеся документы запускают через `ThreadPoolExecutor`;
4. число потоков задаётся `--workers` или `runtime.folder_workers`.

В `default.toml` — 4 workers; в облегчённом `regex_only.toml` — 8.

### Long-lived workers

`worker.py` реализует stdin/stdout JSONL worker. `Pipeline` создаётся один раз при старте process, поэтому локальная модель тоже загружается один раз и переиспользуется между заданиями.

Это подготовленная точка для масштабирования через внешнюю очередь: можно поднять N worker-процессов за Redis/RQ, Celery, RabbitMQ, Kafka или собственной job queue. В текущем MVP `worker.py` не подключён к Docker web UI автоматически — default web deployment работает без внешнего брокера.

## 6. Система скоринга

Scoring — инженерный QA-сигнал, а не вероятность клинической истинности.

### 6.1 Candidate score

Базово:

```text
candidate_score = candidate_confidence × source_weight
```

Source weights:

```text
regex      1.00
typo_regex 0.92
local      0.94
api        0.96
```

Далее применяются ограничения:

- invalid format: `×0.10`;
- invalid evidence: `×0.25`;
- LLM без exact evidence при строгом режиме: score ограничивается `<=0.12`;
- selected regex-preferred fields: небольшой `×1.04`;
- selected LLM-preferred semantic fields: `×1.04`;
- technical absence candidate: `×0.95`;
- agreement нескольких независимых source families для одного value: бонус до `+0.16`;
- конфликт с близким competing value: итог поля `-0.12`;
- adaptive LLM bonus: максимум `+0.05` только для валидного evidence-supported LLM candidate.

Peer prior может дать максимум небольшой tie-break bonus только кандидатам, уже найденным в текущем документе и находящимся близко к лучшему document-local score. Peer signal не создаёт новый candidate.

### 6.2 Field labels

Из `config/quality_thresholds.json`:

```text
good        score >= 0.88
suspicious  0.55 <= score < 0.88
bad         score < 0.55
```

Invalid format/evidence принудительно даёт `bad`; конфликт может принудительно дать `suspicious`.

### 6.3 File score

Для извлечённых полей:

```text
mean = mean(selected_field_scores)
coverage = extracted_fields / 50
conflict_fraction = conflicting_extracted_fields / extracted_fields
critical = number_of_critical_cross_field_issues

file_score = mean × (0.78 + 0.22 × coverage)
             - min(0.18, 0.30 × conflict_fraction)
             - min(0.25, 0.10 × critical)
```

Результат ограничивается `[0,1]`.

File labels:

```text
good        score >= 0.84
suspicious  0.70 <= score < 0.84
bad         score < 0.70
```

Critical validation issue принудительно даёт `bad`; high conflict — `suspicious`.

### 6.4 Batch score adjustment

После обработки batch рассчитывается presence consistency внутри HDBSCAN cluster и clinical neighbors. В текущей конфигурации adjustment консервативный:

- максимальный отрицательный: `-0.05`;
- максимальный положительный: `0.00`.

Таким образом cohort может пометить необычный результат как менее надёжный, но не может повысить score только потому, что «соседи похожи».

## 7. Frontend и backend

Frontend: `project/templates/` + `project/static/`.

Интерфейс собран как отдельный presentation layer поверх production API: дизайнерская разметка и стили не дублируют backend-логику. В текущей версии доступны светлая/тёмная темы, сворачиваемая левая панель, структурированная история рядом с оригинальным документом, context highlighting, field score badges, история сессий, ZIP-выгрузка результатов и профильное меню. Production-only функции — CSRF, авторизация, анонимизация и batch orchestration — сохранены при обновлении дизайна.

Пользовательский поток:

1. авторизация;
2. загрузка `.md/.txt`;
3. optional anonymization;
4. batch prepare;
5. probes/calibration;
6. processing остальных файлов;
7. finalize score;
8. просмотр result/context/score;
9. ZIP финальных JSON.

Основные backend endpoints:

| Method | Endpoint | Назначение |
|---|---|---|
| `GET` | `/health` | Docker healthcheck |
| `GET/POST` | `/login` | вход/регистрация |
| `POST` | `/logout` | завершение сессии |
| `GET` | `/cases` | UI результатов |
| `GET` | `/api/cases/<filename>` | result/context/score |
| `POST` | `/api/batch/prepare` | upload + preview + clustering + probe selection |
| `POST` | `/api/batch/process` | обработка одного batch item |
| `POST` | `/api/batch/finalize` | batch consistency score finalize |
| `GET` | `/api/sessions/<session_id>/download` | ZIP финальных JSON |
| `POST` | `/api/process` | direct/legacy processing |
| `GET` | `/api/system/status` | состояние providers/orchestration |

State-changing web requests защищены CSRF token.

## 8. Вход и выход

Вход:

- `.md` или `.txt`;
- UTF-8 / UTF-8 BOM;
- синтетический кардиологический эпикриз.

Выход для каждого документа:

```text
result/<name>.json            # 50 полей в 9 группах
context/<name>_context.json   # evidence spans + selected source
score/<name>_score.json       # field/file QA + routing metadata
```

`result/*.json` — пользовательский submission result. `context` и `score` — служебные QA artifacts.

## 9. Короткий Docker deploy

Требования: Docker Engine/Desktop + Compose v2.

```bash
cd project
cp .env.example .env
# заменить APP_SECRET_KEY и REGISTRATION_SECRET_KEY

docker compose up -d --build
curl http://localhost:8000/health
```

UI: `http://localhost:8000/login`.

Остановка:

```bash
docker compose down
```

Для rules/API-only без встроенной Transformers-модели в `.env`:

```dotenv
INSTALL_LOCAL=0
LOCAL_LLM_ENABLED=0
```

Для полностью rules-only режима дополнительно:

```dotenv
LLM_API_ENABLED=0
```

API credentials задаются только через `.env`:

```dotenv
LLM_API_BASE_URL=https://provider.example/v1
LLM_API_MODEL=model-name
LLM_API_KEY=secret
```

Не коммитьте `.env`.

## 10. Короткий local setup: Linux и macOS

### Linux

Python 3.11+:

```bash
cd project
bash scripts/setup_linux.sh
source .venv/bin/activate
python app.py
```

### macOS / Apple Silicon / Intel

Вариант с Conda/Miniforge:

```bash
cd project
bash scripts/setup_macos.sh
conda activate cardio-extractor
python app.py
```

Локальный Transformers backend выбирает `cuda`, `mps` или `cpu` автоматически при `local_device=auto`. На Mac без необходимости локальной модели проще использовать Docker или rules/API-only profile.

## 11. Конфигурация

Основные профили:

- `config/default.toml` — локальная разработка, orchestration и batch similarity включены;
- `config/docker.toml` — Docker hybrid profile;
- `config/deployment.toml` — deployment overrides/orchestration;
- `config/regex_only.toml` — deterministic режим без LLM/API;
- `config/local_server.toml` — local OpenAI-compatible server;
- `config/hybrid_api_example.toml` — пример API hybrid;
- `config/model_runs.toml` — benchmark matrix.

Правила и контракт:

- `field_specs.json` — field type/source/selection/absence policies;
- `regex_patterns.json` — generic regex patterns;
- `field_values.json` — aliases/closed values;
- `section_aliases.json` — клинические заголовки;
- `quality_thresholds.json` — scoring thresholds;
- `case_contract.json` — контракт кейса;
- `expert_guidance.json` — агрегированные экспертные инструкции;
- `expert_sources/` — исходные экспертные CSV.

## 12. Тесты и контрольный прогон

Unit/regression suite:

```bash
cd project
pytest -q
```

Финальная проверенная версия: `28 passed`.

Отдельно добавлены TLT regression tests:

- положительная ТЛТ вне `Лечение`;
- комбинированный заголовок `Лечение и исход`;
- введение именованного тромболитика;
- явное отрицание;
- ЧКВ/стентирование/тромбоаспирация не дают `tlt=1`;
- episode-wide TLT LLM batch изолирован от других diagnosis fields;
- TLT участвует в deterministic preview для batch orchestration.

`control_results/` содержит свежий прогон 10 документов текущим кодом с batch analysis включённым и providers отключёнными через environment. Все 10 документов обработаны без ошибок и получили `good`.

Воспроизведение:

```bash
cd project
LOCAL_LLM_ENABLED=0 LLM_API_ENABLED=0 \
python orchestrators/process_folder.py ../control_results/inputs \
  --output ../control_results_repro \
  --config config/default.toml \
  --workers 4
```

## 13. Масштабирование, улучшение и обучение моделей

Эта секция описывает не только идеи, но и уже подготовленные точки расширения.

### 13.1 Горизонтальное масштабирование inference

Текущий Docker profile хранит optional local model внутри web process. Для большого потока рекомендуется разделить систему на:

```text
web/API tier -> job queue -> N extraction workers -> result store
                         -> shared LLM inference service
```

Практический путь:

1. web tier сделать stateless;
2. manifests/results хранить в общей БД/object storage, а не в локальном Docker volume;
3. поставить Redis/RabbitMQ/Kafka/job service;
4. использовать существующий `worker.py` как основу long-lived consumer;
5. масштабировать rules/API workers независимо от frontend;
6. локальную LLM вынести в отдельный vLLM/TGI/OpenAI-compatible service, чтобы несколько web workers не дублировали веса;
7. один GPU inference service обслуживает множество extraction workers;
8. autoscaling привязать к queue depth, latency и GPU utilization.

Для batch orchestration coordinator должен сначала сохранить `prepared` state и завершить probes. После этого remaining documents можно fan-out параллельно по workers; adaptive policy должна лежать в общем state store.

### 13.2 Улучшение regex/rules

Probe comparison уже собирает сигналы `regex_coverage_gap` и `semantic_conflict`. Их можно превратить в active rule-development loop:

1. выбрать evidence-bound gaps;
2. сгруппировать по field/template/cluster;
3. вручную проверить;
4. добавить новые aliases/regex/custom extractor;
5. прогнать tests + reviewed gold;
6. обновить `train_regex_baseline.json` и thresholds только после валидации.

TLT extractor является примером такого перехода от generic section regex к отдельной event-oriented функции.

### 13.3 Подготовка supervised dataset

`scripts/build_finetune_dataset.py` строит SFT JSONL из документов + проверенных `result/context` файлов и использует тот же runtime prompt/field-card contract. Это позволяет обучать модель именно на той схеме, которая используется на inference.

Пример:

```bash
python scripts/build_finetune_dataset.py \
  ../control_results/inputs \
  /path/to/reviewed_labels \
  --out finetune.jsonl \
  --mode group \
  --require-context
```

Для реального обучения labels должны быть вручную проверенными или organizer gold; собственные predictions нельзя считать независимой разметкой.

### 13.4 LoRA / QLoRA

`scripts/train_models.py` поддерживает несколько small instruct models из `config/local_models.json`.

```bash
pip install -r requirements-train.txt
python scripts/train_models.py \
  --train finetune.jsonl \
  --models qwen_0_5b qwen_1_5b \
  --out adapters
```

На CUDA можно добавить `--qlora`. На MPS/CPU используется обычный LoRA path.

`LocalExtractor` умеет загрузить PEFT adapter, если `local_model_path` указывает на каталог с `adapter_config.json`.

### 13.5 Сравнение моделей и выбор production model

Подготовлены:

- `scripts/benchmark_models.py` — benchmark local model presets;
- `orchestrators/compare_models.py` — matrix local/API/hybrid runs;
- `scripts/evaluate_against_gold.py` — exact evaluation на reviewed gold;
- `scripts/evaluate_aggregate_gold.py` — aggregate metrics;
- `config/model_runs.toml` — конфигурация сравнительных запусков.

Рекомендуемый критерий выбора — не только mean accuracy, но и:

- per-field exact match;
- evidence validity;
- critical-field recall;
- latency;
- RAM/VRAM;
- стоимость API;
- stability на новых templates/clusters.

### 13.6 Дальнейшее улучшение scoring

Текущий score эвристический и должен калиброваться на отдельной reviewed validation выборке. Следующий шаг — построить calibration model, которая предсказывает вероятность field/file error по features:

- source family;
- raw candidate score;
- source agreement;
- exact evidence;
- conflict margin;
- template/cluster distance;
- API/rules disagreement;
- validation flags.

До такой калибровки UI score следует интерпретировать как operational triage score, а не вероятность правильности.

### 13.7 Observability и production hardening

Для production дополнительно нужны:

- structured logs и correlation IDs;
- Prometheus/OpenTelemetry metrics;
- request/job timeouts и retries на уровне очереди;
- DB migrations и external session store;
- object storage для исходников/results;
- rate limiting;
- secrets manager;
- backup/retention policy;
- load tests;
- отдельная reviewed clinical validation set;
- monitoring template drift и `regex_coverage_gap` rates.

## 14. Медицинские и эксплуатационные ограничения

- MVP предназначен для структурирования текста, а не для медицинского решения или назначения лечения.
- QA score не равен клинической вероятности правильности.
- Система не должна вычислять отсутствующие значения или диагностировать по косвенным признакам, если контракт этого не разрешает.
- Cohort/cluster information не является источником клинической истины.
- При работе с реальными данными нужны требования организации к ПДн/медицинской тайне, access control, audit, retention и защищённой инфраструктуре.
- Встроенная анонимизация является дополнительным техническим слоем, а не юридической гарантией полного обезличивания.

## 15. Секреты и GitHub hygiene

В репозитории не должны находиться:

- `.env`;
- API keys;
- локальные базы пользователей;
- runtime uploads/results;
- model weights/cache;
- `__pycache__`, `.pytest_cache`, `.DS_Store`, `__MACOSX`.

Для запуска используется только `.env.example`, из которого локально создаётся `.env`.
