# packages/shared-types

Контракт между `apps/web` (TypeScript) и `apps/api` (Python/FastAPI).

FastAPI генерирует OpenAPI-схему автоматически; отсюда TS-типы для фронтенда генерируются инструментом вроде `openapi-typescript` (а не общим TS-кодом, как было бы при NestJS-бэкенде).
