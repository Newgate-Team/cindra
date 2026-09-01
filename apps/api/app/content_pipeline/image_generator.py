import base64
import logging
from typing import Any

import httpx

from app.config import get_settings
from app.content_pipeline.attachments import build_attachment_context
from app.content_pipeline.errors import TransientGenerationError
from app.content_pipeline.media_storage import upload_bytes
from app.content_pipeline.prompt_enhancer import enhance_image_prompt
from app.content_pipeline.text_generator import generate_caption
from app.image_templates import IMAGE_TEMPLATES

logger = logging.getLogger(__name__)

_INTERACTIONS_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"

# 5xx and 429 are worth retrying (transient); anything else (400 bad
# key, 403 permission denied) won't fix itself on retry.
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class ImageGenerationFailedError(Exception):
    """Raised when the Interactions API responds 200 with genuinely no
    image anywhere in the response (neither output_image nor a steps
    entry) -- e.g. the model judged the request unsafe, or gave a
    text-only reply instead. Not retryable: the same prompt would very
    likely get the same non-answer again, so this is treated as a
    permanent failure like VideoGenerationFailedError in
    video_generator.py."""


def _extract_image_from_steps(body: dict[str, Any]) -> dict[str, Any] | None:
    """output_image is documented as a convenience field for "the last
    image generated... in response to the CURRENT request" -- but a
    real production response (CIN-118, confirmed via CIN-110's
    logging) had status=completed and a real generated image, with no
    top-level output_image at all: the image only existed nested in
    steps[].content[]. Every prior "Gemini declined to generate"
    diagnosis (CIN-105/110/117) was chasing the wrong cause -- this was
    a response-parsing gap, not the model refusing.

    Searches steps in order and returns the LAST image content block
    found (matching output_image's own "last image" semantics), or
    None if there genuinely isn't one anywhere in the response.
    """
    found: dict[str, Any] | None = None
    for step in body.get("steps", []):
        for item in step.get("content", []):
            mime_type = item.get("mime_type", "")
            if mime_type.startswith("image/") and "data" in item:
                found = {"data": item["data"], "mime_type": mime_type}
    return found


def _build_image_prompt(payload: dict[str, Any], attachment_texts: list[str] | None = None) -> str:
    # No hard-coded "no text on the image" instruction (CIN-117, removed
    # after it was proven to be the actual cause of real generation
    # failures -- users legitimately ask for logos/text in the image,
    # e.g. "показать наш логотип на экране ноутбука", and the same
    # request succeeds in AI Studio without this instruction). Whether
    # text belongs on the image is now entirely up to the user's own
    # topic/brand_guide.
    # CIN-137: studio illustrations (blocks/cartoon styles) are drawn
    # assets, not photos -- the photorealistic lead and the "natural
    # moment, not a stock pose" composition line would fight the
    # illustration prompt, so they get their own minimal wrapper.
    is_illustration = payload.get("image_kind") == "illustration"
    if is_illustration:
        lines = [f"Иллюстрация: {payload['topic']}."]
    else:
        lines = [f"Фотореалистичное изображение на тему: {payload['topic']}."]
    # CIN-125: image models are structurally unreliable at rendering
    # long text correctly (confirmed in production -- real generated
    # photos with a full Russian sentence baked in came back with
    # actual spelling/grammar errors, e.g. "хочошо"/"рабюто" instead of
    # "хочу"/"работать"). Not asking to omit text (CIN-117 already
    # covered why that's wrong) -- just nudging toward the length/
    # correctness regime these models handle far more reliably: short
    # phrases, checked before rendering.
    lines.append(
        "Если по смыслу на изображении должен быть текст (надпись, лозунг, текст на "
        "баннере/экране и т.п.) -- используй короткую фразу, не длиннее 4-6 слов, и "
        "напиши её без орфографических и грамматических ошибок."
    )
    # CIN-132: grounded in the run-social-content skill's
    # create-social-image-posts reference (prompt-contracts.md's
    # exclusion list + visual-formats.md's "natural moment over generic
    # pose"). Negative space near an edge keeps the frame usable if the
    # user overlays a caption in their own editor. Worded to exclude
    # only accidental artifacts (garbled background writing), not text
    # in general -- CIN-117's blanket "no text" prohibition must not
    # creep back in.
    if is_illustration:
        lines.append(
            "Чистая композиция с одним главным объектом. Без случайного текста и "
            "нечитаемых надписей."
        )
    else:
        lines.append(
            "Кадр должен быть композиционно чистым и правдоподобным: естественный момент, "
            "а не постановочная стоковая поза. Оставь немного свободного пространства у "
            "одного из краёв на случай подписи поверх фото. Без искажённых лиц, лишних "
            "пальцев, поддельных интерфейсов и нечитаемых случайных надписей на заднем "
            "плане."
        )
    # CIN-143: the template's art direction must survive the enhancer
    # falling back -- appended here in English as-is (the image model
    # follows English direction fine inside a Russian prompt).
    if not is_illustration:
        template = IMAGE_TEMPLATES.get(payload.get("image_template") or "")
        if template:
            lines.append(template["directive"])
    brand_guide = payload.get("brand_guide")
    if brand_guide:
        lines.append(f"Стиль и бренд-гайд (соблюдать): {brand_guide}")
    for i, text in enumerate(attachment_texts or [], start=1):
        label = "Контекст из прикреплённого документа" if len(attachment_texts) == 1 else f"Контекст из документа {i}"
        lines.append(f"{label}: {text}")
    return "\n".join(lines)


def nano_banana_image_generator(
    payload: dict[str, Any], client: httpx.Client | None = None
) -> dict[str, Any]:
    """Real Google Gemini image-generation call via the Interactions API.

    `payload` is a GenerationJob.input_payload built by the /content
    router: {"topic", "platform", "content_kind", "brand_guide"}.
    Without GEMINI_API_KEY configured this still reaches the real
    endpoint and fails with a real 400/401 -- proving the request is
    shaped correctly, not mocking the call away. `client` is only for
    tests to inject an httpx.MockTransport -- production always uses
    the default real client.

    Replaces the old Imagen 4 `:predict` endpoint (deprecated by
    Google, shut down 2026-08-17 -- see CIN-58) with the Interactions
    API (`POST /v1beta/interactions`), auth via `x-goog-api-key`
    header rather than a `key` query param. Request/response shape
    cross-checked against Google's official Interactions API reference
    (ai.google.dev/api/interactions-api) across three independent
    fetches: the image-generation guide's curl example (endpoint,
    headers, `model`/`input` fields), the REST schema reference
    (`output_image.data`/`mime_type`), and explicit confirmation that
    a non-streaming request (the default -- no `stream` field sent)
    returns synchronously with `status: "completed"` and `output_image`
    already populated, so no polling loop is needed here (unlike Veo's
    predictLongRunning in video_generator.py).

    The Interactions API has no hosted URL for the generated image,
    only base64 -- this uploads the decoded bytes to R2 (CIN-56/CIN-78)
    and returns a real public `image_url`, directly usable as
    Post.image_url.
    """
    settings = get_settings()

    # Optional context files (CIN-97, up to 5 total since CIN-107, no
    # per-attachment cap on images specifically): each document's
    # extracted text folds into the prompt; each attached image becomes
    # a reference image via the Interactions API's array `input` form
    # (image-to-image/edit,
    # ai.google.dev/gemini-api/docs/image-generation). Video/audio
    # attachments aren't usable here -- the Interactions API only
    # documents image reference input, not video/audio -- so they're
    # silently not applied when content_type is "image".
    attachment_texts: list[str] = []
    reference_images: list[dict[str, Any]] = []
    for attachment in payload.get("attachments", []):
        context = build_attachment_context(
            attachment["url"], attachment["attachment_type"], client=client
        )
        if context["kind"] == "text":
            attachment_texts.append(context["text"])
        elif attachment["attachment_type"] == "image":
            reference_images.append(
                {
                    "type": "image",
                    "mime_type": context["mime_type"],
                    "data": base64.b64encode(context["data"]).decode("ascii"),
                }
            )

    prompt = _build_image_prompt(payload, attachment_texts=attachment_texts)
    # CIN-142: for user-facing images the raw topic goes through a
    # prompt-engineering rewrite first (detailed English prompt) -- the
    # wrapper above stays as the fallback when the enhancer hiccups, so
    # generation never fails because of the improver. Studio
    # illustrations skip it: their prompts are already model-written
    # from the brief (CIN-137).
    if payload.get("image_kind") != "illustration":
        enhanced = enhance_image_prompt(
            payload, attachment_texts=attachment_texts, client=client
        )
        if enhanced:
            prompt = enhanced

    input_field: Any
    if reference_images:
        input_field = [{"type": "text", "text": prompt}, *reference_images]
    else:
        input_field = prompt

    request_kwargs: dict[str, Any] = {
        "headers": {
            "x-goog-api-key": settings.gemini_api_key,
            "content-type": "application/json",
        },
        "json": {"model": settings.image_model, "input": input_field},
        "timeout": 60.0,
    }
    try:
        response = (
            client.post(_INTERACTIONS_URL, **request_kwargs)
            if client is not None
            else httpx.post(_INTERACTIONS_URL, **request_kwargs)
        )
    except httpx.TransportError as exc:
        # Network-level failure (timeout, connection reset, DNS) --
        # distinct from an HTTP error response, and just as transient.
        raise TransientGenerationError(f"Gemini Interactions API network error: {exc}") from exc

    if response.status_code in _RETRYABLE_STATUS_CODES:
        raise TransientGenerationError(
            f"Gemini Interactions API {response.status_code}: {response.text[:500]}"
        )
    response.raise_for_status()

    body = response.json()
    output_image = body.get("output_image") or _extract_image_from_steps(body)
    if output_image is None:
        # The user-facing message below stays generic (no raw API
        # internals) -- this is the diagnostic trail for us: without it,
        # there's no way to tell *why* Gemini declined (safety, a
        # prompt/reference-image conflict, etc.) after the fact, only
        # that it did (CIN-110, following the same real case CIN-105
        # left unconfirmed).
        logger.warning(
            "Gemini Interactions API returned no image anywhere in the response (status=%s): %s",
            body.get("status"),
            response.text[:2000],
        )
        raise ImageGenerationFailedError(
            f"Gemini не сгенерировал изображение по этому запросу (status={body.get('status')}). "
            "Возможно, запрос был отклонён как небезопасный, либо модель не смогла "
            "выполнить какую-то часть запроса. Попробуйте переформулировать запрос."
        )
    image_bytes = base64.b64decode(output_image["data"])
    mime_type = output_image["mime_type"]
    extension = mime_type.split("/")[-1]
    image_url = upload_bytes(image_bytes, mime_type, extension)

    result: dict[str, Any] = {"image_url": image_url, "prompt": prompt}
    # CIN-114: a real caption, not just the raw topic, for the "Подпись"
    # field on the review-and-publish screen -- best-effort, never
    # fails the (already successful, already paid-for) image itself.
    # Studio illustrations (CIN-137) are never published as posts, so
    # the extra paid caption call is skipped for them.
    if payload.get("image_kind") != "illustration":
        caption = generate_caption(payload, client=client)
        if caption:
            result["text"] = caption
    return result
