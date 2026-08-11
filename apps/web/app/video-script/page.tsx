"use client";

import { GenerationForm } from "../components/GenerationForm";
import { RequireAuth } from "../components/RequireAuth";

// Dedicated page for content_kind="video_script" (CIN-129) -- was
// previously just a "Тип контента" option on /generate when Формат
// был "Текст". Same generation/publish flow, just pinned to
// text+video_script and its own place in the nav instead of being
// buried in a dropdown.
export default function VideoScriptPage() {
  return (
    <RequireAuth>
      <GenerationForm
        heading="Сценарий видео"
        subtitle="Опишите видео — Cindra подготовит сценарий по кадрам с репликами и таймкодами"
        lockedContentType="text"
        lockedContentKind="video_script"
        topicLabel="О чём видео"
        topicPlaceholder="например, обзор новой коллекции для Reels"
      />
    </RequireAuth>
  );
}
