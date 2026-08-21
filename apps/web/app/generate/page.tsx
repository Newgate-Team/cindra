"use client";

import { GenerationForm } from "../components/GenerationForm";
import { RequireAuth } from "../components/RequireAuth";

// This page is «Посты»: text and image content only (CIN-136). Video
// moved to the studio (/video) with its own project flow -- excluded
// here via excludeContentTypes rather than the publish matrix, which
// still allows video for the studio's own publish step. video_script
// lives in the studio's script step now too (was /video-script,
// CIN-129/130).
export default function GeneratePage() {
  return (
    <RequireAuth>
      <GenerationForm
        heading="Посты"
        subtitle="Опишите задачу — Cindra подготовит черновик под выбранный канал"
        excludeContentKinds={["video_script"]}
        excludeContentTypes={["video"]}
      />
    </RequireAuth>
  );
}
