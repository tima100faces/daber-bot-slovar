# DABER — Current State (04.06.2026)

## Сегодня сделано: Блог фактов, Arimo, вынос CSS

### Блог «Факты об иврите»
- ✅ Таблица `language_facts` (fact_type, title, fact_body, source_url, is_published)
- ✅ **API**: `GET /api/facts/random`, `GET /api/facts`, `GET /api/facts/{id}`, админ-CRUD
- ✅ **Страница `/facts`**: лента с пагинацией, 4 типа фактов (история, этимология, сравнение, а вы знали)
- ✅ **Страница `/facts/{id}`**: отдельный факт + Schema.org Article для Google
- ✅ **Главная**: карточка «Интересный факт» под кнопкой «Слово дня» (JS: `loadFactTeaser`)
- ✅ **Админка `/admin/facts`**: список, опубликовать/снять/удалить, сгенерировать
- ✅ **Генерация**: `enrichment/generate_facts.py` — Sonnet рерайтит источники в факты
- ✅ **Источники**: NatGeo + Britannica → `enrichment/sources_raw.md` (11K chars)
- ✅ 10 фактов сгенерированы и опубликованы

### Шрифты и CSS
- ✅ Arimo — единственный шрифт всего сайта (--font-body/display/hebrew)
- ✅ **Вынос CSS**: `static/components.css` (1000 строк) — все переиспользуемые стили
- ✅ `index.html` `<style>` сокращён с 1000 до 175 строк (только страничное)
- ✅ Новый токен `--color-legal` для ссылок футера

### Фиксы UI
- ✅ Поле поиска светлее (`--color-surface-2`) — видно на солнце
- ✅ Иконки на accent-кнопках белые (`brightness(0) invert(1)`)
- ✅ Футер: меньше шрифт (0.8rem), меньше паддинг (1.5rem), muted цвет

### Модульная архитектура
- ✅ **6 отдельных страниц**: `pending.html`, `approved.html`, `rejected.html`, `feedback.html`, `words.html`, `costs.html`
- ✅ **Общие файлы**: `_admin.css` (~450 строк стилей), `_core.js` (~75 строк — auth, счётчики, хелперы)
- ✅ **Роуты FastAPI**: `/admin/pending`, `/admin/approved`, `/admin/rejected`, `/admin/feedback`, `/admin/words`, `/admin/costs`
- ✅ **Редирект**: `/admin` → `/admin/pending`
- ✅ Каждая страница — полноценный букмаркабельный URL

### Иконки и логотип
- ✅ Все 16 иконок — локальные Tabler SVG в `/static/icons/`
- ✅ Иконки инвертируются в тёмной теме (`filter: invert(1)`)
- ✅ Логотип Daber: `mask-image` с акцентным цветом (как на морде) + `<img>` fallback для Safari
- ✅ 0 внешних запросов

### Фиксы
- ✅ **Контент-race-condition**: `DOMContentLoaded` вместо немедленного IIFE — `onPageLoad()` гарантированно вызывается после загрузки всех скриптов
- ✅ **Счётчики**: `loadAllCounts()` при загрузке + после approve/reject, больше не сбрасываются
- ✅ **Сохранение таба**: `sessionStorage` (для старого SPA)
- ✅ **Мобильное меню**: бургер с выезжающим сайдбаром

### Сайдбар
- Логотип Daber · ADMIN
- Tabler-иконки: list-check, circle-check, circle-x, message-report, books, coins
- Бейджи счётчиков, активный таб подсвечен
- Кнопка «Выйти» с иконкой logout внизу

---

## Архитектура админки

```
static/admin/
├── _admin.css          — общие стили (сайдбар, карточки, формы, модалки, mobile)
├── _core.js            — общий JS (auth, checkAuth, loadAllCounts, esc, toggleSidebar)
├── pending.html        — модерация (одобрить/отклонить/✎/проверить/👁)
├── approved.html       — одобренные слова
├── rejected.html       — отклонённые слова
├── feedback.html       — жалобы (переключение решено/не решено)
├── words.html          — редактор словаря (⚠ подозрительные/🔍 поиск/✎/удалить)
├── costs.html          — затраты + enrichment (пауза/собрать/лимит/история)
└── login.html          — TOTP-логин
```

Каждая страница — самодостаточный HTML с сайдбаром, топбаром и контентной областью.
Сайдбар дублирован (статический HTML ~55 строк), навигация через `<a href>`.

---

## Архитектура верификации

### Layer 1 — Морфология + БД (бесплатно)
- Мишкали (морфологические шаблоны): CCaC, CiCCuC, miCCaC, hitpael-префиксы
- Окончания: `ה-` (обычно f), `ים-` (обычно m.pl)
- Кросс-чек: поиск однокоренных слов в БД, сравнение POS/рода
- Фразы (`pos_slug: phrase`) — проверки пропускаются

### Layer 2 — Sonnet (батчинг)
- Все подозрительные слова → один API-вызов
- Каждое слово с `context_sentence` и `context_translation`
- Результат: `verdict: ok | fix` с конкретными исправлениями
- Затраты пишутся в `enrichment_costs` с `model='claude-sonnet-4-20250514'`

---

## Enrichment Pipeline

### Модели
| Модель | Роль | Цена (input/output за 1M) |
|--------|------|---------------------------|
| Gemini 2.5 Flash | Экстракция слов из текстов | $0.15 / $0.60 |
| Claude Sonnet 4 | Верификация подозрительных слов | $3.00 / $15.00 |

### Источники
| Тип | Источник | Статус |
|-----|----------|--------|
| RSS | Ynet, Haaretz, Walla, Mako, Israel Hayom | ✅ |
| RSS | Nevo (юрбаза) | ✅ |
| Telegram новости | @rotternews, @kikar_shabbat | ❌ ошибка URL |
| Telegram магазины | @kspcoil, @superpharmil, @dilimshavima, @haregakaniti | ✅ |
| Reddit | r/Israel, r/hebrew, r/ani_bm | ❌ сломан |

### Дедупликация
- Проверяются: words, pending_words, verbs
- Глаголы запрещены в промпте + пост-фильтр
- Минимальная длина: 80 символов (RSS), 30 (Telegram)

---

## API

### Основные
- `GET /api/search?q=...` — поиск (префикс+точное на иврите, перевод+транслит на кириллице)
- `GET /api/stats` — статистика (кэш 1 час)

### Админка (страницы)
- `GET /admin` → редирект на `/admin/pending`
- `GET /admin/pending` — модерация
- `GET /admin/approved` — одобренные
- `GET /admin/rejected` — отклонённые
- `GET /admin/feedback` — жалобы
- `GET /admin/words` — редактор словаря
- `GET /admin/costs` — затраты + enrichment
- `GET /admin/login` — TOTP-логин

### Админка (API)
- `GET /admin/api/pending?status=pending&id=N` — список/одно pending-слово
- `GET /admin/api/pending/{id}/preview` — превью с enriched-переводами
- `PUT /admin/api/pending/{id}` — редактирование (pos_slug, gender, number, translation_ru, translation_enriched, notes, ...)
- `POST /admin/api/pending/{id}/approve` — одобрить → вставка в words
- `POST /admin/api/pending/{id}/reject` — отклонить
- `GET /admin/api/pending/{id}/check` — проверка дубликатов
- `DELETE /admin/api/word/{id}?type=word|verb` — удаление (каскад)
- `GET /admin/api/costs/summary` — затраты: today_models, month_models, pricing
- `POST /admin/api/enrichment/run` — ручной запуск enrichment
- `GET /admin/api/enrichment/status` — статус (paused, daily_limit, today_inserted)

---

## База данных

### Основные таблицы
- `words` — живой словарь (headword, pos_slug, gender, number, grammar_json, ...)
- `pending_words` — очередь модерации (status: pending/approved/rejected, translation_ru, translation_enriched, reviewer_note, number)
- `enrichment_costs` — затраты по моделям (model, tokens, cost_usd, words_inserted, ...)
- `enrichment_settings` — daily_limit, paused

---

## Файлы
- `main.py` — FastAPI (~2100 строк, факты API, роуты страниц, enrichment-триггер)
- `static/index.html` — фронтенд словаря (маска логотипа, локальные Tabler, карточка факта)
- `static/components.css` — общие компонентные стили (~1000 строк, вынесены из index.html)
- `static/facts.html` — страница ленты фактов
- `static/fact.html` — страница отдельного факта + Schema.org
- `static/admin/_admin.css` — общие стили админки (~450 строк)
- `static/admin/_core.js` — общий JS админки (~75 строк)
- `static/admin/facts.html` — админка фактов (список, генерация)
- `static/admin/pending.html` — модерация (~420 строк)
- `static/admin/approved.html` — одобренные (~90 строк)
- `static/admin/rejected.html` — отклонённые (~90 строк)
- `static/admin/feedback.html` — жалобы (~100 строк)
- `static/admin/words.html` — редактор словаря (~250 строк)
- `static/admin/costs.html` — затраты (~240 строк)
- `static/admin/login.html` — TOTP-логин
- `static/design-system.css`, `fonts.css` — дизайн-токены и шрифты
- `static/icons/` — 19 локальных Tabler SVG
- `enrichment/sources.py` — RSS/Telegram/Reddit источники
- `enrichment/pipeline.py` — Gemini-экстракция + вызов верификации
- `enrichment/verify.py` — Layer 1 (правила) + Layer 2 (Sonnet)
- `enrichment/run.py` — оркестратор (сбор → экстракция → верификация)
- `enrichment/generate_facts.py` — генерация фактов через Sonnet из источников
- `enrichment/sources_raw.md` — сырой материал из NatGeo + Britannica
- `backup.sh` — pg_dump ежедневно
- `.env` — ANTHROPIC_API_KEY, ADMIN_TOTP_SECRET, DB_PASSWORD

---

## UI / UX
- Тёмная/светлая темы, дизайн-токены в `design-system.css`
- Шрифт: Arimo для всего (body + display + hebrew), JetBrains Mono для кода
- Локальные шрифты и иконки: 0 внешних запросов
- Мобильные: 100dvh, бургер-меню, адаптивные чипы
- SEO: Open Graph, Schema.org Article, favicon.svg
- Блог фактов: `/facts` + отдельные страницы с rich snippets
- Cloudflare + nginx rate limit
- Админка: модульная, сайдбар, букмаркабельные URL
