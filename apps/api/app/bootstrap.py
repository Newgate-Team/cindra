import logging

from app.content_pipeline.image_generator import nano_banana_image_generator
from app.content_pipeline.registry import register_generator
from app.content_pipeline.text_generator import gemini_text_generator
from app.content_pipeline.video_dispatch import dispatch_video_generator
from app.models import GenerationContentType, SocialPlatform
from app.scheduler.registry import register_publisher
from app.social_integrations import facebook, instagram, telegram, tiktok


def bootstrap() -> None:
    """Register every generator/publisher.

    Must run in *every* process that executes a GenerationJob or Post
    -- the FastAPI app (main.py) and the Celery worker
    (`celery -A app.celery_app worker`) are separate processes, and
    the worker never imports main.py, so this can't live only there.
    celery_app.py calls this at import time, and main.py imports
    celery_app (via content_pipeline.tasks), so both processes end up
    covered from a single call site.
    """
    register_generator(GenerationContentType.text, gemini_text_generator)
    register_generator(GenerationContentType.image, nano_banana_image_generator)
    # CIN-144: a dispatcher, not a single provider -- routes to
    # Seedance or Veo by the job's input_payload["provider"].
    register_generator(GenerationContentType.video, dispatch_video_generator)
    register_publisher(SocialPlatform.telegram, telegram.publish)
    register_publisher(SocialPlatform.instagram, instagram.publish)
    register_publisher(SocialPlatform.facebook, facebook.publish)
    register_publisher(SocialPlatform.tiktok, tiktok.publish)

    # CIN-121: the Celery worker runs with --loglevel=info (railway.toml),
    # which sets the root logger to INFO -- httpx's own request logger
    # inherits that and logs every outbound request's full URL, including
    # query-string credentials (Graph API's access_token, Gemini's key
    # param). Explicitly capping it to WARNING here keeps that off the
    # logs without touching the app's own INFO-level logging.
    logging.getLogger("httpx").setLevel(logging.WARNING)
