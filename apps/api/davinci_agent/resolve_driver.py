"""Drives DaVinci Resolve Studio through its scripting API.

Method names and settings keys follow Blackmagic's scripting README
(v21.0.4): ProjectManager.CreateProject, MediaPool.
ImportTimelineFromFile, Timeline.CreateSubtitlesFromAudio,
Project.SetRenderSettings (ExportSubtitle + SubtitleFormat "BurnIn"),
AddRenderJob / StartRendering / GetRenderJobStatus. External scripting
is Studio-only (since 19.1); the free edition refuses the connection.
"""

from __future__ import annotations

import os
import platform
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any


class ResolveError(Exception):
    pass


_DEFAULT_MODULE_DIRS = {
    "Darwin": "/Library/Application Support/Blackmagic Design/DaVinci Resolve/"
    "Developer/Scripting/Modules",
    "Windows": os.path.expandvars(
        r"%PROGRAMDATA%\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting\Modules"
    ),
    "Linux": "/opt/resolve/Developer/Scripting/Modules",
}


def connect() -> Any:
    """The running Resolve Studio instance. Start it first (headless is
    fine: `Resolve -nogui`)."""
    module_dir = os.environ.get("RESOLVE_SCRIPT_API")
    module_dir = (
        os.path.join(module_dir, "Modules") if module_dir else _DEFAULT_MODULE_DIRS.get(platform.system())
    )
    if module_dir and module_dir not in sys.path:
        sys.path.append(module_dir)
    try:
        import DaVinciResolveScript  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ResolveError(
            "Не найден модуль DaVinciResolveScript — установлен ли DaVinci Resolve Studio? "
            "Путь к нему задаётся переменной RESOLVE_SCRIPT_API."
        ) from exc
    resolve = DaVinciResolveScript.scriptapp("Resolve")
    if resolve is None:
        raise ResolveError(
            "DaVinci Resolve не отвечает: он должен быть запущен (можно `Resolve -nogui`), "
            "и это должна быть версия Studio — бесплатная не разрешает внешнее управление."
        )
    return resolve


_CAPTION_LANGUAGE_CONSTANTS = {
    "auto": "AUTO_CAPTION_AUTO",
    "russian": "AUTO_CAPTION_RUSSIAN",
    "english": "AUTO_CAPTION_ENGLISH",
}


class ResolveRenderer:
    def __init__(self, resolve: Any, sleep: Callable[[float], None] = time.sleep) -> None:
        self._resolve = resolve
        self._sleep = sleep

    def _caption_settings(self, language: str, width: int, height: int) -> dict[Any, Any]:
        r = self._resolve
        missing = [
            name
            for name in ("SUBTITLE_LANGUAGE", "SUBTITLE_CHARS_PER_LINE", _CAPTION_LANGUAGE_CONSTANTS[language])
            if not hasattr(r, name)
        ]
        if missing:
            raise ResolveError(
                "Эта версия DaVinci не умеет автосубтитры из аудио (нужна Studio 18.5+)"
            )
        # A vertical frame fits far fewer characters per line.
        chars = 24 if height > width else 42
        return {
            r.SUBTITLE_LANGUAGE: getattr(r, _CAPTION_LANGUAGE_CONSTANTS[language]),
            r.SUBTITLE_CHARS_PER_LINE: chars,
        }

    def render(
        self,
        *,
        timeline_path: Path,
        project_name: str,
        width: int,
        height: int,
        fps: int,
        subtitles: bool,
        subtitles_language: str,
        output_dir: Path,
        output_name: str,
        on_progress: Callable[[int], None],
    ) -> Path:
        manager = self._resolve.GetProjectManager()
        project = manager.CreateProject(project_name)
        if not project:
            raise ResolveError(f"DaVinci не создал проект «{project_name}»")
        try:
            for key, value in (
                ("timelineResolutionWidth", str(width)),
                ("timelineResolutionHeight", str(height)),
                ("timelineFrameRate", str(fps)),
                ("timelinePlaybackFrameRate", str(fps)),
            ):
                if not project.SetSetting(key, value):
                    raise ResolveError(f"DaVinci не принял настройку проекта {key}={value}")

            timeline = project.GetMediaPool().ImportTimelineFromFile(
                str(timeline_path),
                {"timelineName": project_name, "importSourceClips": True},
            )
            if not timeline:
                raise ResolveError("DaVinci не смог импортировать монтажный лист (FCPXML)")
            project.SetCurrentTimeline(timeline)

            if subtitles:
                settings = self._caption_settings(subtitles_language, width, height)
                if not timeline.CreateSubtitlesFromAudio(settings):
                    raise ResolveError("DaVinci не смог распознать речь для субтитров")

            if not project.SetCurrentRenderFormatAndCodec("mp4", "H264"):
                raise ResolveError("DaVinci не поддерживает рендер в MP4/H.264 на этой машине")
            output_dir.mkdir(parents=True, exist_ok=True)
            render_settings = {
                "SelectAllFrames": True,
                "TargetDir": str(output_dir),
                "CustomName": output_name,
                "ExportVideo": True,
                "ExportAudio": True,
                "FormatWidth": width,
                "FormatHeight": height,
            }
            if subtitles:
                render_settings.update(ExportSubtitle=True, SubtitleFormat="BurnIn")
            if not project.SetRenderSettings(render_settings):
                raise ResolveError("DaVinci не принял настройки рендера")
            job_id = project.AddRenderJob()
            if not job_id:
                raise ResolveError("DaVinci не создал задачу рендера")
            if not project.StartRendering([job_id]):
                raise ResolveError("DaVinci не запустил рендер")

            while project.IsRenderingInProgress():
                status = project.GetRenderJobStatus(job_id) or {}
                on_progress(int(status.get("CompletionPercentage", 0)))
                self._sleep(2)
            status = project.GetRenderJobStatus(job_id) or {}
            if status.get("JobStatus") not in ("Complete", "Completed"):
                detail = status.get("Error") or status.get("JobStatus") or "неизвестно"
                raise ResolveError(f"Рендер в DaVinci не завершился: {detail}")
            on_progress(100)

            rendered = sorted(output_dir.glob(f"{output_name}*.mp4"))
            if not rendered:
                raise ResolveError("DaVinci сообщил о готовности, но файл рендера не найден")
            return rendered[0]
        finally:
            # One throwaway project per job: nothing piles up in the
            # Resolve database on a machine that runs unattended.
            manager.CloseProject(project)
            manager.DeleteProject(project_name)
