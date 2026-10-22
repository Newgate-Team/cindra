# apps/api

Backend Cindra: FastAPI + Python.

## Разработка

```bash
cp .env.example .env
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

`.env` — gitignored, только для локальных значений. Продакшн-секреты и значения `.env` — разные, не путать «протестировано локально» с «работает в проде».

Health-check: `GET /health`.

## Тесты и линт

```bash
pytest
ruff check .
```

## Деплой (Railway)

Три сервиса из одного репозитория (Railway привязывает один конфиг-файл к одному Root Directory -- отдельного multi-service синтаксиса нет, см. `railway.toml`, читается всеми тремя):

| Сервис | Root directory | Start Command | Примечание |
|---|---|---|---|
| API | `apps/api` | (по умолчанию из `Dockerfile`/`railway.toml`) | `alembic upgrade head` + uvicorn. **Healthcheck Path `/health` -- задать вручную в Settings этого сервиса** (Dashboard-оверрайд, не в `railway.toml`, см. CIN-92: файл общий для всех трёх сервисов, worker/beat не слушают HTTP и падают на healthcheck, если он задан в файле) |
| Worker | `apps/api` | `celery -A app.celery_app worker --loglevel=info` | тот же образ, Custom Start Command в Dashboard, healthcheck НЕ задавать |
| Beat | `apps/api` | `celery -A app.celery_app beat --loglevel=info` | тот же образ, Custom Start Command в Dashboard, healthcheck НЕ задавать; **не может быть на засыпающем тарифе** -- отвечает за запланированные публикации и ежедневный бэкап БД (см. "Бэкапы" ниже) |

Плюс managed-аддоны Postgres и Redis (Railway создаёт `DATABASE_URL`/`REDIS_URL`-совместимые переменные автоматически при подключении аддона -- сверить с именами, которые ждёт `config.py`, при необходимости смэпить вручную).

**Переменные окружения** (см. полный список и комментарии в `.env.example`) -- вводятся в Railway Dashboard, не коммитятся:

```
DATABASE_URL
REDIS_URL
JWT_SECRET
SOCIAL_TOKEN_ENCRYPTION_KEY
CORS_ORIGINS               # https://<домен фронтенда на Vercel>
GEMINI_API_KEY
GEMINI_MODEL
IMAGE_MODEL
VEO_MODEL
TELEGRAM_BOT_TOKEN
META_APP_ID
META_APP_SECRET
META_REDIRECT_URI           # https://<домен фронтенда на Vercel>/oauth/instagram/callback -- страница-колбэк живёт в apps/web, не в API
GOOGLE_CLIENT_ID             # вход через Google -- см. CIN-133; тот же ID отдельно нужен фронтенду как NEXT_PUBLIC_GOOGLE_CLIENT_ID (Vercel, не Railway)
TIKTOK_CLIENT_KEY
TIKTOK_CLIENT_SECRET
TIKTOK_REDIRECT_URI          # https://<домен фронтенда на Vercel>/oauth/tiktok/callback
YOUTUBE_CLIENT_ID            # отдельный OAuth-клиент от GOOGLE_CLIENT_ID -- см. .env.example
YOUTUBE_CLIENT_SECRET
YOUTUBE_REDIRECT_URI         # https://<домен фронтенда на Vercel>/oauth/youtube/callback
GOOGLE_DRIVE_CLIENT_ID       # можно тот же OAuth-клиент, что YouTube (+ включить Drive API) -- см. .env.example
GOOGLE_DRIVE_CLIENT_SECRET
GOOGLE_DRIVE_REDIRECT_URI    # https://<домен фронтенда на Vercel>/oauth/google-drive/callback; экран согласия -- «In production», иначе токены живут 7 дней
LINKEDIN_CLIENT_ID
LINKEDIN_CLIENT_SECRET
LINKEDIN_REDIRECT_URI        # https://<домен фронтенда на Vercel>/oauth/linkedin/callback
LINKEDIN_API_VERSION         # YYYYMM, сверить с актуальной документацией LinkedIn перед деплоем
REDDIT_CLIENT_ID
REDDIT_CLIENT_SECRET
REDDIT_REDIRECT_URI          # https://<домен фронтенда на Vercel>/oauth/reddit/callback
REDDIT_USER_AGENT            # обязателен описательный формат, иначе Reddit режет по rate limit
TWITTER_CLIENT_ID
TWITTER_CLIENT_SECRET
TWITTER_REDIRECT_URI         # https://<домен фронтенда на Vercel>/oauth/twitter/callback -- уточнить, что тариф X API включает write-доступ
R2_ACCOUNT_ID
R2_ACCESS_KEY_ID
R2_SECRET_ACCESS_KEY
R2_BUCKET_NAME
R2_PUBLIC_URL_BASE
BACKUP_ENCRYPTION_KEY        # см. CIN-160 ниже -- без него ежедневный бэкап падает явно
MONTAGE_AGENT_TOKEN          # секрет агента DaVinci (davinci_agent/); пусто = монтажи ждут в очереди
STREAM_AGENT_TOKEN           # секрет агента эфиров (stream_agent/), отдельный от DaVinci; пусто = эфиры ждут в очереди
GEMINI_TTS_MODEL             # голос аватара в эфире; по умолчанию gemini-3.8-flash-lite-tts (сверить со списком моделей Gemini TTS перед деплоем)
PAYPAL_CLIENT_ID             # live, не sandbox
PAYPAL_CLIENT_SECRET         # live
PAYPAL_MODE=live
PAYPAL_PRO_PLAN_ID
PAYPAL_BUSINESS_PLAN_ID
PAYPAL_WEBHOOK_ID             # регистрируется после первого деплоя, когда есть реальный URL
```

Redirect URI каждой соцсети должен быть зарегистрирован в её же Developer Portal буквально таким же значением -- расхождение (например http vs https, конечный слэш) роняет OAuth-обмен кода на токен с ошибкой на стороне платформы, не Cindra.

## Агент эфиров (stream_agent)

Эфир с AI-аватаром ведёт отдельный процесс на машине, которая кодирует видео (720p30 H.264 ≈ 2–4 ядра CPU на эфир), — не Railway. Он сам забирает запущенные эфиры из API (`/streams/agent/*`, секрет `STREAM_AGENT_TOKEN`), держит сессии LiveAvatar (меняет их до истечения лимита плана — 20/60 мин — в паузе между репликами), читает чат Twitch и шлёт RTMP на `ingest.global-contribute.live-video.net`.

```bash
pip install -e ".[stream-agent]"          # livekit, PyAV (FFmpeg внутри), numpy, aiohttp
sudo apt-get install -y fonts-dejavu-core # кириллица в подписях на экране
python -m stream_agent --api https://<api> --token <STREAM_AGENT_TOKEN>
```

Один процесс = один эфир; для нескольких эфиров — несколько процессов/машин. Упавший агент: эфир через 2 минуты без heartbeat подхватывает другой.

**Проверка всего пути локально, без ключей** (LiveKit в Docker, симулятор LiveAvatar с фейковым аватаром, заглушка Gemini с речью macOS `say`, ffmpeg вместо Twitch):

```bash
docker run -d --name cindra-livekit -p 7880:7880 -p 7881:7881 -p 7882:7882/udp livekit/livekit-server --dev --bind 0.0.0.0 --node-ip 127.0.0.1
LIVEAVATAR_SIM_LIVEKIT_URL=ws://localhost:7880 LIVEAVATAR_SIM_MAX_SESSION=120 uvicorn devtools.liveavatar_simulator:app --port 8767
uvicorn devtools.gemini_stub:app --port 8768
STREAM_AGENT_TOKEN=dev LIVEAVATAR_API_BASE_URL=http://localhost:8767 GEMINI_API_BASE_URL=http://localhost:8768 GEMINI_API_KEY=stub uvicorn app.main:app --port 8000
ffmpeg -listen 1 -i rtmp://127.0.0.1:1935/live/<ключ эфира> -c copy received.flv
python -m stream_agent --api http://localhost:8000 --token dev --rtmp-url "rtmp://127.0.0.1:1935/live/{key}" --liveavatar-url http://localhost:8767
```

Дальше в приложении: «Инструменты» → LiveAvatar с ключом `la_sim_любой` → «Эфир» → создать и запустить. Сессии по 2 минуты — смена аватара видна по цвету фона. **Не запускать pytest во время такого прогона**: тесты чистят ту же локальную БД.

## Бэкапы

Railway Trial/Hobby не даёт автоматических бэкапов/PITR для managed Postgres (только на Pro). Вместо апгрейда -- см. CIN-91: `app.scheduler.tasks.backup_database`, Celery beat, ежедневно в 03:00 UTC. Дампает БД через `pg_dump`, гзипует, шифрует (см. CIN-160 ниже) и заливает в тот же R2-бакет, что и медиа (`backups/postgres/YYYY-MM-DD.sql.gz.enc`), храня последние 14 дампов.

**CIN-160: обязателен `BACKUP_ENCRYPTION_KEY`.** R2-бакет с медиа обязан быть публично читаемым (иначе не работает сама публикация постов), а у R2 нет per-prefix ACL -- значит `backups/` доступен по тому же публичному URL, что и любая опубликованная картинка/видео, без всякой авторизации. Дамп поэтому шифруется Fernet перед загрузкой (`app.scheduler.backup._fernet`); без ключа таск падает явно, а не грузит дамп открытым текстом. Сгенерировать ключ:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Восстановление из дампа:

```bash
# скачать дамп конкретного дня из R2 (например через rclone/aws-cli, настроенный на R2-эндпоинт), затем расшифровать и распаковать:
python -c "
from cryptography.fernet import Fernet
data = open('2026-08-04.sql.gz.enc', 'rb').read()
open('2026-08-04.sql.gz', 'wb').write(Fernet('$BACKUP_ENCRYPTION_KEY').decrypt(data))
"
gunzip -c 2026-08-04.sql.gz | psql "$DATABASE_URL"
```

`DATABASE_URL` здесь -- обычный `postgresql://` (без `+psycopg`, это специфика SQLAlchemy-драйвера, `psql`/`pg_dump` его не понимают).
