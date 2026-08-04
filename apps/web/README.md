# apps/web

Frontend Cindra: Next.js + TypeScript.

## Разработка

```bash
npm install
npm run dev
```

Ожидает apps/api на `http://localhost:8000` (см. `NEXT_PUBLIC_API_URL`, по умолчанию — этот адрес, менять не нужно для локальной разработки с дефолтными портами).

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
