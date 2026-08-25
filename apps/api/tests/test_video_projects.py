import io
import json
import uuid
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.content_pipeline.errors import TransientGenerationError
from app.content_pipeline.video_studio import (
    VideoStudioFailedError,
    _split_brief_files,
    extract_illustration_prompts,
    generate_brief_files,
)
from app.models import (
    GenerationContentType,
    GenerationJob,
    GenerationStatus,
    Subscription,
    SubscriptionTier,
    User,
    VideoProject,
)


def _auth_headers(client: TestClient, email: str = "studio@cindra.dev") -> dict[str, str]:
    client.post("/auth/register", json={"email": email, "password": "supersecret1"})
    token = client.post(
        "/auth/login", json={"email": email, "password": "supersecret1"}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _create_project(client: TestClient, headers: dict[str, str], topic: str = "запуск Cindra") -> dict:
    response = client.post("/video-projects", json={"topic": topic}, headers=headers)
    assert response.status_code == 201
    return response.json()


def test_create_project_starts_as_draft(client: TestClient) -> None:
    headers = _auth_headers(client)
    project = _create_project(client, headers)
    assert project["status"] == "draft"
    assert project["script"] is None
    assert project["brief_files"] is None


def test_list_projects_scoped_to_owner(client: TestClient) -> None:
    mine = _auth_headers(client, "mine@cindra.dev")
    other = _auth_headers(client, "other@cindra.dev")
    _create_project(client, mine, topic="мой проект")
    _create_project(client, other, topic="чужой проект")
    listed = client.get("/video-projects", headers=mine).json()
    assert [p["topic"] for p in listed] == ["мой проект"]


def test_get_someone_elses_project_returns_404(client: TestClient) -> None:
    mine = _auth_headers(client, "mine@cindra.dev")
    other = _auth_headers(client, "other@cindra.dev")
    project = _create_project(client, mine)
    response = client.get(f"/video-projects/{project['id']}", headers=other)
    assert response.status_code == 404


def test_get_project_with_garbage_id_returns_404(client: TestClient) -> None:
    headers = _auth_headers(client)
    response = client.get("/video-projects/not-a-uuid", headers=headers)
    assert response.status_code == 404


def test_requires_auth(client: TestClient) -> None:
    assert client.get("/video-projects").status_code in (401, 403)
    assert client.post("/video-projects", json={"topic": "x"}).status_code in (401, 403)


def test_styles_catalog_includes_blocks_and_veo(client: TestClient) -> None:
    headers = _auth_headers(client)
    styles = {s["id"]: s for s in client.get("/video-projects/styles", headers=headers).json()}
    assert styles["blocks"]["produces"] == "brief"
    assert styles["veo_auto"]["produces"] == "clip"
    assert len(styles) >= 5


def test_generate_script_stores_script_and_advances_status(client: TestClient) -> None:
    headers = _auth_headers(client)
    project = _create_project(client, headers)
    with patch(
        "app.routers.video_projects.generate_script_text",
        return_value="Хук: 3 ошибки в постах. Дальше по кадрам...",
    ):
        response = client.post(f"/video-projects/{project['id']}/script", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["script"].startswith("Хук")
    assert body["status"] == "script_ready"


def test_generate_script_gemini_downtime_returns_503(client: TestClient) -> None:
    headers = _auth_headers(client)
    project = _create_project(client, headers)
    with patch(
        "app.routers.video_projects.generate_script_text",
        side_effect=TransientGenerationError("Gemini API 503"),
    ):
        response = client.post(f"/video-projects/{project['id']}/script", headers=headers)
    assert response.status_code == 503


def test_update_script_and_style(client: TestClient) -> None:
    headers = _auth_headers(client)
    project = _create_project(client, headers)
    response = client.patch(
        f"/video-projects/{project['id']}",
        json={"script": "мой отредактированный сценарий", "style": "blocks"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["script"] == "мой отредактированный сценарий"
    assert response.json()["style"] == "blocks"


def test_unknown_style_returns_400(client: TestClient) -> None:
    headers = _auth_headers(client)
    project = _create_project(client, headers)
    response = client.patch(
        f"/video-projects/{project['id']}", json={"style": "vhs-retro"}, headers=headers
    )
    assert response.status_code == 400


def test_brief_requires_script_and_style(client: TestClient) -> None:
    headers = _auth_headers(client)
    project = _create_project(client, headers)
    assert (
        client.post(f"/video-projects/{project['id']}/brief", headers=headers).status_code
        == 400
    )
    client.patch(f"/video-projects/{project['id']}", json={"script": "сценарий"}, headers=headers)
    assert (
        client.post(f"/video-projects/{project['id']}/brief", headers=headers).status_code
        == 400
    )


def test_brief_rejected_for_veo_auto_style(client: TestClient) -> None:
    headers = _auth_headers(client)
    project = _create_project(client, headers)
    client.patch(
        f"/video-projects/{project['id']}",
        json={"script": "сценарий", "style": "veo_auto"},
        headers=headers,
    )
    response = client.post(f"/video-projects/{project['id']}/brief", headers=headers)
    assert response.status_code == 400
    assert "ролик" in response.json()["detail"]


def test_generate_brief_stores_files_and_advances_status(client: TestClient) -> None:
    headers = _auth_headers(client)
    project = _create_project(client, headers)
    client.patch(
        f"/video-projects/{project['id']}",
        json={"script": "сценарий", "style": "blocks"},
        headers=headers,
    )
    files = [
        {"filename": "voiceover.md", "title": "Аудио", "content": "текст"},
        {"filename": "production.md", "title": "Продакшн", "content": "иллюстрации"},
        {"filename": "edit.md", "title": "Монтаж", "content": "таймлайн"},
    ]
    with patch("app.routers.video_projects.generate_brief_files", return_value=files):
        response = client.post(f"/video-projects/{project['id']}/brief", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert [f["filename"] for f in body["brief_files"]] == [
        "voiceover.md",
        "production.md",
        "edit.md",
    ]
    assert body["status"] == "brief_ready"


def test_video_generation_only_for_veo_auto(client: TestClient) -> None:
    headers = _auth_headers(client)
    project = _create_project(client, headers)
    client.patch(
        f"/video-projects/{project['id']}",
        json={"script": "сценарий", "style": "blocks"},
        headers=headers,
    )
    response = client.post(
        f"/video-projects/{project['id']}/video-generation", headers=headers
    )
    assert response.status_code == 400


def test_video_generation_links_job_and_surfaces_result(
    client: TestClient, db: Session
) -> None:
    headers = _auth_headers(client)
    # free tier has 0 video generations -- bump to pro like test_content
    db.execute(
        update(Subscription)
        .where(Subscription.user_id == select(User.id).where(User.email == "studio@cindra.dev").scalar_subquery())
        .values(tier=SubscriptionTier.pro)
    )
    db.commit()
    project = _create_project(client, headers)
    client.patch(
        f"/video-projects/{project['id']}",
        json={"script": "сценарий", "style": "veo_auto"},
        headers=headers,
    )
    # task_always_eager runs the job synchronously; the registered real
    # Veo generator fails without credentials, which is fine -- the
    # linked job and its terminal state must surface on the project.
    response = client.post(
        f"/video-projects/{project['id']}/video-generation", headers=headers
    )
    assert response.status_code == 200
    body = client.get(f"/video-projects/{project['id']}", headers=headers).json()
    assert body["video_status"] in ("queued", "processing", "completed", "failed")

    user_id = db.scalar(select(User.id).where(User.email == "studio@cindra.dev"))
    job_id = db.scalar(select(GenerationJob.id).where(GenerationJob.user_id == user_id))
    assert job_id is not None


def test_completed_veo_job_marks_project_video_ready(
    client: TestClient, db: Session
) -> None:
    headers = _auth_headers(client)
    project = _create_project(client, headers)
    user = db.query(User).filter(User.email == "studio@cindra.dev").one()
    job = GenerationJob(
        user_id=user.id,
        content_type="video",
        status=GenerationStatus.completed,
        input_payload={"topic": "x"},
        output_payload={"video_url": "https://media.cindra.example/final.mp4"},
    )
    db.add(job)
    db.commit()
    client.patch(
        f"/video-projects/{project['id']}",
        json={"script": "сценарий", "style": "veo_auto"},
        headers=headers,
    )
    db_project = db.get(VideoProject, uuid.UUID(project["id"]))
    db_project.video_generation_job_id = job.id
    db.commit()
    body = client.get(f"/video-projects/{project['id']}", headers=headers).json()
    assert body["video_url"] == "https://media.cindra.example/final.mp4"
    assert body["status"] == "video_ready"


def test_upload_video_stores_url_and_marks_ready(client: TestClient) -> None:
    headers = _auth_headers(client)
    project = _create_project(client, headers)
    with patch(
        "app.routers.video_projects.upload_bytes",
        return_value="https://media.cindra.example/upload.mp4",
    ) as upload:
        response = client.post(
            f"/video-projects/{project['id']}/video",
            files={"file": ("final.mp4", io.BytesIO(b"fake-mp4-bytes"), "video/mp4")},
            headers=headers,
        )
    assert response.status_code == 200
    assert upload.call_args.args[1] == "video/mp4"
    body = response.json()
    assert body["video_url"] == "https://media.cindra.example/upload.mp4"
    assert body["status"] == "video_ready"


def test_upload_rejects_non_video_file(client: TestClient) -> None:
    headers = _auth_headers(client)
    project = _create_project(client, headers)
    response = client.post(
        f"/video-projects/{project['id']}/video",
        files={"file": ("notes.txt", io.BytesIO(b"text"), "text/plain")},
        headers=headers,
    )
    assert response.status_code == 400


def test_upload_rejects_empty_file(client: TestClient) -> None:
    headers = _auth_headers(client)
    project = _create_project(client, headers)
    response = client.post(
        f"/video-projects/{project['id']}/video",
        files={"file": ("final.mp4", io.BytesIO(b""), "video/mp4")},
        headers=headers,
    )
    assert response.status_code == 400


def test_delete_project(client: TestClient) -> None:
    headers = _auth_headers(client)
    project = _create_project(client, headers)
    assert (
        client.delete(f"/video-projects/{project['id']}", headers=headers).status_code == 204
    )
    assert client.get(f"/video-projects/{project['id']}", headers=headers).status_code == 404


def test_split_brief_files_parses_markers() -> None:
    raw = (
        "=== FILE: voiceover.md | Аудио: текст для записи ===\n"
        "строка 1\nстрока 2\n"
        "=== FILE: production.md | Продакшн ===\n"
        "иллюстрация 1\n"
        "=== FILE: edit.md | Монтаж ===\n"
        "таймлайн\n"
    )
    files = _split_brief_files(raw)
    assert [f["filename"] for f in files] == ["voiceover.md", "production.md", "edit.md"]
    assert files[0]["content"] == "строка 1\nстрока 2"
    assert files[1]["title"] == "Продакшн"


def test_split_brief_files_falls_back_to_single_file() -> None:
    files = _split_brief_files("просто сплошной текст без маркеров")
    assert len(files) == 1
    assert files[0]["filename"] == "brief.md"


def test_generate_brief_files_calls_gemini_with_style_guidance() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["prompt"] = json.loads(request.content)["contents"][0]["parts"][0]["text"]
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "text": (
                                        "=== FILE: voiceover.md | Аудио ===\nтекст\n"
                                        "=== FILE: production.md | Продакшн ===\nсписок\n"
                                        "=== FILE: edit.md | Монтаж ===\nплан\n"
                                    )
                                }
                            ]
                        }
                    }
                ]
            },
        )

    files = generate_brief_files(
        "тема",
        "сценарий",
        "blocks",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert len(files) == 3
    assert "по блокам" in captured["prompt"]
    assert "сценарий" in captured["prompt"]


def test_video_generation_free_tier_hits_402(client: TestClient) -> None:
    headers = _auth_headers(client)
    project = _create_project(client, headers)
    client.patch(
        f"/video-projects/{project['id']}",
        json={"script": "сценарий", "style": "veo_auto"},
        headers=headers,
    )
    response = client.post(
        f"/video-projects/{project['id']}/video-generation", headers=headers
    )
    assert response.status_code == 402


def _brief_ready_project(client: TestClient, headers: dict[str, str], style: str = "blocks") -> dict:
    project = _create_project(client, headers)
    client.patch(
        f"/video-projects/{project['id']}",
        json={"script": "сценарий", "style": style},
        headers=headers,
    )
    files = [
        {"filename": "voiceover.md", "title": "Аудио", "content": "текст"},
        {
            "filename": "production.md",
            "title": "Продакшн",
            "content": "1. ноутбук со стикерами\n2. тающие часы",
        },
        {"filename": "edit.md", "title": "Монтаж", "content": "план"},
    ]
    with patch("app.routers.video_projects.generate_brief_files", return_value=files):
        client.post(f"/video-projects/{project['id']}/brief", headers=headers)
    return project


def test_illustrations_rejected_for_filmed_styles(client: TestClient) -> None:
    headers = _auth_headers(client)
    project = _brief_ready_project(client, headers, style="cinematic")
    response = client.post(
        f"/video-projects/{project['id']}/illustrations", headers=headers
    )
    assert response.status_code == 400


def test_illustrations_require_brief(client: TestClient) -> None:
    headers = _auth_headers(client)
    project = _create_project(client, headers)
    client.patch(
        f"/video-projects/{project['id']}",
        json={"script": "сценарий", "style": "blocks"},
        headers=headers,
    )
    response = client.post(
        f"/video-projects/{project['id']}/illustrations", headers=headers
    )
    assert response.status_code == 400
    assert "бриф" in response.json()["detail"]


def test_illustrations_create_image_jobs_and_surface_on_project(
    client: TestClient, db: Session
) -> None:
    headers = _auth_headers(client)
    project = _brief_ready_project(client, headers)
    prompts = ["ноутбук со стикерами, тёмный фон", "тающие часы, тёмный фон"]
    with patch(
        "app.routers.video_projects.extract_illustration_prompts", return_value=prompts
    ):
        response = client.post(
            f"/video-projects/{project['id']}/illustrations", headers=headers
        )
    assert response.status_code == 200
    body = response.json()
    assert len(body["illustrations"]) == 2
    assert [i["prompt"] for i in body["illustrations"]] == prompts
    # task_always_eager ran the real image generator without
    # credentials -- jobs exist and reached a terminal/queued state.
    assert all(
        i["status"] in ("queued", "processing", "completed", "failed")
        for i in body["illustrations"]
    )
    jobs = db.scalars(
        select(GenerationJob).where(
            GenerationJob.content_type == GenerationContentType.image
        )
    ).all()
    assert len(jobs) == 2
    assert all(job.input_payload["image_kind"] == "illustration" for job in jobs)


def test_illustrations_free_tier_limit_is_atomic(client: TestClient) -> None:
    # free tier allows 3 images/month -- a 4-prompt brief must charge
    # nothing and start nothing.
    headers = _auth_headers(client)
    project = _brief_ready_project(client, headers)
    prompts = ["a", "b", "c", "d"]
    with patch(
        "app.routers.video_projects.extract_illustration_prompts", return_value=prompts
    ):
        response = client.post(
            f"/video-projects/{project['id']}/illustrations", headers=headers
        )
    assert response.status_code == 402
    body = client.get(f"/video-projects/{project['id']}", headers=headers).json()
    assert body["illustrations"] is None


def test_illustration_extraction_failure_does_not_charge(client: TestClient) -> None:
    headers = _auth_headers(client)
    project = _brief_ready_project(client, headers)
    with patch(
        "app.routers.video_projects.extract_illustration_prompts",
        side_effect=VideoStudioFailedError("не разобрали"),
    ):
        response = client.post(
            f"/video-projects/{project['id']}/illustrations", headers=headers
        )
    assert response.status_code == 502
    # image limit untouched: 3 real generations must still fit
    prompts = ["a", "b", "c"]
    with patch(
        "app.routers.video_projects.extract_illustration_prompts", return_value=prompts
    ):
        response = client.post(
            f"/video-projects/{project['id']}/illustrations", headers=headers
        )
    assert response.status_code == 200


def test_styles_expose_generates_illustrations_flag(client: TestClient) -> None:
    headers = _auth_headers(client)
    styles = {s["id"]: s for s in client.get("/video-projects/styles", headers=headers).json()}
    assert styles["blocks"]["generates_illustrations"] is True
    assert styles["cartoon"]["generates_illustrations"] is True
    assert styles["cinematic"]["generates_illustrations"] is False
    assert styles["veo_auto"]["generates_illustrations"] is False


def test_extract_illustration_prompts_parses_json_array() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {"content": {"parts": [{"text": '```json\n["промпт 1", "промпт 2"]\n```'}]}}
                ]
            },
        )

    prompts = extract_illustration_prompts(
        "1. промпт 1\n2. промпт 2", client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    assert prompts == ["промпт 1", "промпт 2"]


def test_extract_illustration_prompts_rejects_non_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"candidates": [{"content": {"parts": [{"text": "вот промпты: раз, два"}]}}]},
        )

    with pytest.raises(VideoStudioFailedError):
        extract_illustration_prompts(
            "план", client=httpx.Client(transport=httpx.MockTransport(handler))
        )


def test_extract_illustration_prompts_caps_at_ten() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {"content": {"parts": [{"text": json.dumps([f"p{i}" for i in range(15)])}]}}
                ]
            },
        )

    prompts = extract_illustration_prompts(
        "план", client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    assert len(prompts) == 10


def _usage_count(db: Session, content_type: GenerationContentType) -> int:
    from app.models import UsageEvent

    return len(
        db.scalars(
            select(UsageEvent).where(UsageEvent.content_type == content_type)
        ).all()
    )


def test_failed_script_generation_does_not_consume_quota(
    client: TestClient, db: Session
) -> None:
    # CIN-139: the studio knows within the request whether Gemini
    # answered -- a failure must not burn a text generation.
    headers = _auth_headers(client)
    project = _create_project(client, headers)
    with patch(
        "app.routers.video_projects.generate_script_text",
        side_effect=TransientGenerationError("Gemini API 503"),
    ):
        assert (
            client.post(f"/video-projects/{project['id']}/script", headers=headers).status_code
            == 503
        )
    assert _usage_count(db, GenerationContentType.text) == 0


def test_successful_script_generation_consumes_one_text_generation(
    client: TestClient, db: Session
) -> None:
    headers = _auth_headers(client)
    project = _create_project(client, headers)
    with patch(
        "app.routers.video_projects.generate_script_text", return_value="сценарий"
    ):
        client.post(f"/video-projects/{project['id']}/script", headers=headers)
    assert _usage_count(db, GenerationContentType.text) == 1


def test_failed_brief_generation_does_not_consume_quota(
    client: TestClient, db: Session
) -> None:
    headers = _auth_headers(client)
    project = _create_project(client, headers)
    client.patch(
        f"/video-projects/{project['id']}",
        json={"script": "сценарий", "style": "blocks"},
        headers=headers,
    )
    with patch(
        "app.routers.video_projects.generate_brief_files",
        side_effect=VideoStudioFailedError("Gemini 400"),
    ):
        assert (
            client.post(f"/video-projects/{project['id']}/brief", headers=headers).status_code
            == 502
        )
    assert _usage_count(db, GenerationContentType.text) == 0


def test_second_illustration_run_while_in_flight_returns_409(
    client: TestClient, db: Session
) -> None:
    # CIN-139: double-clicking must not charge the image quota twice
    # and orphan the running jobs.
    headers = _auth_headers(client)
    project = _brief_ready_project(client, headers)
    with patch(
        "app.routers.video_projects.extract_illustration_prompts", return_value=["a"]
    ):
        client.post(f"/video-projects/{project['id']}/illustrations", headers=headers)
    job = db.scalars(
        select(GenerationJob).where(
            GenerationJob.content_type == GenerationContentType.image
        )
    ).first()
    job.status = GenerationStatus.processing
    db.commit()
    with patch(
        "app.routers.video_projects.extract_illustration_prompts", return_value=["b"]
    ):
        response = client.post(
            f"/video-projects/{project['id']}/illustrations", headers=headers
        )
    assert response.status_code == 409
    assert _usage_count(db, GenerationContentType.image) == 1


def test_second_veo_run_while_in_flight_returns_409(client: TestClient, db: Session) -> None:
    headers = _auth_headers(client)
    db.execute(
        update(Subscription)
        .where(
            Subscription.user_id
            == select(User.id).where(User.email == "studio@cindra.dev").scalar_subquery()
        )
        .values(tier=SubscriptionTier.pro)
    )
    db.commit()
    project = _create_project(client, headers)
    client.patch(
        f"/video-projects/{project['id']}",
        json={"script": "сценарий", "style": "veo_auto"},
        headers=headers,
    )
    client.post(f"/video-projects/{project['id']}/video-generation", headers=headers)
    job = db.scalars(
        select(GenerationJob).where(
            GenerationJob.content_type == GenerationContentType.video
        )
    ).first()
    job.status = GenerationStatus.processing
    db.commit()
    response = client.post(
        f"/video-projects/{project['id']}/video-generation", headers=headers
    )
    assert response.status_code == 409
    assert _usage_count(db, GenerationContentType.video) == 1
