# apps/web

Frontend Cindra: Next.js + TypeScript.

## Разработка

```bash
npm install
npm run dev
```

Ожидает apps/api на `http://localhost:8000` (см. `NEXT_PUBLIC_API_URL`, по умолчанию — этот адрес, менять не нужно для локальной разработки с дефолтными портами).

Для продакшена задайте `NEXT_PUBLIC_SITE_URL` (адрес сайта без слэша в конце) — от него строятся `sitemap.xml`, `robots.txt` и превью ссылок (`opengraph-image`); без него берётся текущий боевой адрес из `lib/site.ts`. Иконка вкладки, иконка для iOS и манифест PWA генерируются кодом (`app/icon.tsx`, `app/apple-icon.tsx`, `app/manifest.ts`) из того же градиента, что и логотип.

Опционально для подключения Instagram (см. CIN-52):

```
NEXT_PUBLIC_META_APP_ID=...
NEXT_PUBLIC_META_REDIRECT_URI=...
```

Без них кнопка подключения Instagram на экране «Соцсети» остаётся отключённой с пояснением, а не ведёт в нерабочий флоу.

## Экраны

- `/generate` — генерация контента + просмотр/редактирование результата перед публикацией (CIN-35, CIN-38)
- `/calendar` — список публикаций и их статус (CIN-36)
- `/social-accounts` — подключение/отключение Telegram и Instagram (CIN-37)
- `/billing` — текущий тариф (CIN-39)
