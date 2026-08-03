from app.content_pipeline.image_generator import imagen_image_generator
from app.content_pipeline.registry import register_generator
from app.content_pipeline.text_generator import gemini_text_generator
from app.content_pipeline.video_generator import veo_video_generator
from app.models import GenerationContentType, SocialPlatform
from app.scheduler.registry import register_publisher
from app.social_integrations import facebook, instagram, telegram


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
    register_generator(GenerationContentType.image, imagen_image_generator)
    register_generator(GenerationContentType.video, veo_video_generator)
    register_publisher(SocialPlatform.telegram, telegram.publish)
    register_publisher(SocialPlatform.instagram, instagram.publish)
    register_publisher(SocialPlatform.facebook, facebook.publish)
