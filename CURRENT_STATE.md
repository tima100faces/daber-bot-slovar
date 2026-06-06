# DABER — Current State (06.06.2026)

## Статистика
- **Слов:** 8 048 + глаголов 4 748 = 12 796
- **Verb forms:** 126 051
- **Опубликовано фактов:** 36 (+52 драфта)
- **Частотность:** 50K записей, 6 932 слов сопоставлено (85.5%)
- **WOTD-пул:** 2 199 слов (freq_rank 200–10000, не служебные, ≥4 букв)
- **Pending queue:** 0

---

## 06.06.2026 — Миграция на Sonnet + дозагрузка глаголов

### Загружено 140 пропущенных глаголов
- 4 608 → 4 748 глаголов (все из Pealim YAML)
- 122 410 → 126 051 verb forms
- 85 с переводами, 55 пассивных (pual/hufal)

### Enrichment глаголов (Sonnet)
- `enrich_verbs.py` — обогащение 140 новых глаголов через Sonnet
- Заполняет: `verbs.translation_enriched`, `verb_examples`, `verb_synonyms`, `verbs.notes`

### Полный переход на Sonnet
- `pipeline.py` — Gemini → Sonnet (вызов `call_sonnet` вместо `call_gemini`)
- `run.py` — обновлён, требует `ANTHROPIC_API_KEY`
- Единственный оставшийся Gemini-референс: `GOOGLE_API_KEY` в .env (не используется пайплайном)

### Фикс бага с переводом глаголов
- В модалке глагола показывался `—` вместо перевода при пустом `translation_enriched`
- Исправлено: фолбэк на `translation_ru`

### README.md
- Полная документация инфраструктуры: IP, порты, nginx, БД, API, крон

---

## Новый функционал (04.06–05.06.2026)

### Слово дня (WOTD)
- ✅ Детерминированное слово по хешу даты (всегда одно и то же весь день)
- ✅ API: `GET /api/word-of-day`
- ✅ Выбор из пула 2 199 слов: частотность 200–10000, исключены служебные POS и слова <4 букв
- ✅ Модалка без крестика — закрытие по клику снаружи / Escape
- ✅ Транслит: заглавные буквы → строчные с акцентом ударения
- ✅ GA-событие: `wotd_view`
- ✅ Кнопка «Слово дня» на главной под поиском

### Викторина (Quiz)
- ✅ 4 варианта ответа — все той же части речи, что целевое слово
- ✅ 3 жизни: красные inline SVG-сердечки (живые) / серый контур (потерянные)
- ✅ Game over после 3 ошибок, лучший результат сохраняется
- ✅ Сброс в полночь через `localStorage` (`quiz_date`, `quiz_lives`, `quiz_best`)
- ✅ GA-событие: `quiz_answer` с полем `lives`
- ✅ Кнопка «Викторина» на главной под поиском

### Частотность слов
- ✅ Таблица `word_frequencies` — 50K слов из hermitdave/FrequencyWords (OpenSubtitles 2018)
- ✅ 85.5% словаря сопоставлено (6 932 / 8 111)
- ✅ Используется для фильтра WOTD (freq_rank 200–10000)

### UI/UX — морда
- ✅ POS-лейблы во множественном числе: «глаголы», «существительные», «фразы» и т.д.
- ✅ `phrase` → «фразы» (было «phrase» по-английски)
- ✅ Гендеры: `муж./жен.` → `м.р./ж.р./ср.р.`
- ✅ Склонение примеров: 1 пример, 2–4 примера, 5+ примеров
- ✅ Крестики убраны со всех модалок — закрытие по клику снаружи / Escape
- ✅ Переключатель темы в навбаре: чистая иконка 20px, без фона/рамки
- ✅ Пилюли POS без счётчиков, только текст
- ✅ Факт-тизер и кнопка «Слово дня» скрываются при поиске, возвращаются при очистке
- ✅ Обработка URL-параметра `?q=` при загрузке страницы
- ✅ Inline SVG-сердечки для викторины (heart.svg удалён)

---

## Блог «Факты об иврите»
- ✅ **36 фактов опубликовано**, 52 драфта
- ✅ **API**: `GET /api/facts/random`, `GET /api/facts` (пагинация), `GET /api/facts/{id}`
- ✅ **Страница `/facts`**: lazy load (IntersectionObserver), 4 типа, без эмодзи
- ✅ **Страница `/facts/{id}`**: Schema.org Article, Open Graph, без эмодзи
- ✅ **Главная**: карточка факта `<a href>` (не onclick), GA `fact_click`
- ✅ **Админка**: `/admin/facts` — генерация, публикация, удаление; единые кнопки `.btn`
- ✅ **NGINX**: `location /facts` → прокси на uvicorn
- ✅ **Анти-дубликация**: скрипт проверяет существующие факты перед генерацией
- ✅ **Источники**: Wikipedia API (15 статей) + NatGeo/Britannica → `enrichment/sources_raw.md` (11K)
- ✅ **Генерация**: `enrichment/generate_facts.py` — Sonnet рерайтит источники в факты

### Шрифты и CSS
- ✅ Arimo — единственный шрифт всего сайта (--font-body/display/hebrew)
- ✅ **Вынос CSS**: `static/components.css` (1000 строк) — все переиспользуемые стили
- ✅ `index.html` `<style>` сокращён с 1000 до 175 строк (только страничное)
- ✅ Новый токен `--color-legal` для ссылок футера
- ✅ Стили inline SVG-сердечек (`.heart-lost`)

### Фиксы UI
- ✅ Поле поиска светлее (`--color-surface-2`) — видно на солнце
- ✅ Иконки на accent-кнопках белые (`brightness(0) invert(1)`)
- ✅ Футер: меньше шрифт (0.8rem), меньше паддинг (1.5rem), muted цвет

---

## Модульная архитектура админки
- ✅ **9 отдельных страниц**: `pending.html`, `approved.html`, `rejected.html`, `feedback.html`, `words.html`, `costs.html`, `contact.html` (сообщения с формы), `verify.html` (очередь верификации), `duplicates.html` (дубли headword), `facts.html` (блог-факты)
- ✅ **Общие файлы**: `_admin.css` (~450 строк стилей), `_core.js` (~75 строк — auth, счётчики, хелперы)
- ✅ **Роуты FastAPI**: `/admin/pending`, `/admin/approved`, `/admin/rejected`, `/admin/feedback`, `/admin/words`, `/admin/costs`, `/admin/contact`, `/admin/verify`, `/admin/duplicates`, `/admin/facts`
- ✅ **Редирект**: `/admin` → `/admin/pending`
- ✅ **Авторизация**: все `/admin/api/*` ручки проверяют `admin_required` (включая факты — закрыто 06.06.2026)
- ✅ Каждая страница — полноценный букмаркабельный URL

### Иконки и логотип
- ✅ Все иконки — локальные Tabler SVG в `/static/icons/` (19 шт.)
- ✅ Иконки инвертируются в тёмной теме (`filter: invert(1)`)
- ✅ Логотип Daber: `mask-image` с акцентным цветом (как на морде) + `<img>` fallback для Safari
- ✅ 0 внешних запросов
- ✅ heart.svg удалён — викторина использует inline SVG

### Фиксы админки
- ✅ **Контент-race-condition**: `DOMContentLoaded` вместо немедленного IIFE
- ✅ **Счётчики**: `loadAllCounts()` при загрузке + после approve/reject, больше не сбрасываются
- ✅ **Мобильное меню**: бургер с выезжающим сайдбаром

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
| Claude Sonnet 4 | Экстракция слов + верификация + факты + обогащение глаголов | $3.00 / $15.00 |
| Gemini 2.5 Flash | Не используется в пайплайне (ключ сохранён для будущих задач) | — |

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

### Публичные
- `GET /api/search?q=...` — поиск (префикс+точное на иврите, перевод+транслит на кириллице)
- `GET /api/word/{word}?id=&type=` — детальная карточка
- `GET /api/letter/{letter}` — слова на букву
- `GET /api/pos/{pos}` — фильтр по части речи
- `GET /api/random?n=` — случайные слова
- `GET /api/stats` — статистика (кэш 1 час)
- `POST /api/feedback` — сообщить об ошибке
- `GET /api/admin/session` — проверка админа (без 401)

### Слово дня и Викторина
- `GET /api/word-of-day` — детерминированное слово дня (хеш даты, freq 200–10000)
- `GET /api/quiz` — случайное слово + 3 дистрактора той же POS

### Факты
- `GET /api/facts/random` — случайный факт
- `GET /api/facts?page=&limit=` — пагинация
- `GET /api/facts/{id}` — детальная

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
- `words` — живой словарь (8 111) (headword, pos_slug, gender, number, grammar_json, ...)
- `verbs` — глаголы из Pealim (4 607)
- `word_forms`, `word_examples`, `word_synonyms`, `word_phrases` — связи
- `word_frequencies` — частотность 50K слов (hermitdave/FrequencyWords)
- `pending_words` — очередь модерации (status: pending/approved/rejected, translation_ru, translation_enriched, reviewer_note, number)
- `language_facts` — факты об иврите (36 опубликовано, 52 драфта)
- `enrichment_costs` — затраты по моделям (model, tokens, cost_usd, words_inserted, ...)
- `enrichment_settings` — daily_limit, paused
- `user_feedback` — жалобы пользователей

---

## Файлы проекта
- `main.py` — FastAPI (~2100 строк: поиск, WOTD, Quiz, факты, админ-роуты, enrichment-триггер)
- `static/index.html` — фронтенд: поиск, WOTD-модалка, Quiz-модалка, викторина с inline SVG-сердечками
- `static/components.css` — общие компонентные стили (~1000 строк)
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
- `static/icons/` — 19 локальных Tabler SVG (heart.svg удалён)
- `enrichment/run.py` — оркестратор (сбор → экстракция → верификация)
- `enrichment/sources.py` — RSS/Telegram/Reddit источники
- `enrichment/pipeline.py` — Sonnet-экстракция + вызов верификации
- `enrichment/verify.py` — Layer 1 (правила) + Layer 2 (Sonnet)
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
- Модалки: без крестиков — закрытие по клику снаружи / Escape
- Переключатель темы: в навбаре, чистая иконка 20px без рамки
- POS-пилюли: только текст, без счётчиков
- Викторина: 3 жизни (inline SVG-сердечки), game over, бэст-скор
- WOTD: детерминированное по дате, модалка без крестика
