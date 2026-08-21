import io
import json
import uuid
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.content_pipeline.errors import TransientGenerationError
from app.content_pipeline.video_studio import _split_brief_files, generate_brief_files
from app.models import (
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
