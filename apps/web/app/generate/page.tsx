"use client";

import { GenerationForm } from "../components/GenerationForm";
import { RequireAuth } from "../components/RequireAuth";

// "Сценарий видео" (content_kind="video_script") moved to its own
// page (CIN-129) -- excluded here rather than from the underlying
// publish matrix, which still allows it (that page submits it
// directly, unchanged on the backend).
export default function GeneratePage() {
  return (
    <RequireAuth>
      <GenerationForm
        heading="Генерация контента"
        subtitle="Опишите задачу — Cindra подготовит черновик под выбранный канал"
        excludeContentKinds={["video_script"]}
      />
    </RequireAuth>
  );
}
