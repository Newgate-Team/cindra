import subprocess
import sys


def test_importing_celery_app_alone_registers_generators_and_publishers() -> None:
    """Regression test for a real bug: the Celery worker process is started
    as `celery -A app.celery_app worker` and never imports app.main, so a
    registration that only happened in main.py silently left the worker
    with empty registries -- jobs queued fine but every one failed with
    "no generator registered", discovered by actually running a worker
    against the live queue, not by the (same-process) pytest suite.

    Runs in a subprocess importing *only* app.celery_app, mirroring what
    the real worker process does, so this fails again if the registration
    call ever moves back to somewhere only main.py reaches.
    """
    script = (
        "import app.celery_app\n"
        "from app.content_pipeline.registry import _REGISTRY as g\n"
        "from app.scheduler.registry import _REGISTRY as p\n"
        "from app.models import GenerationContentType, SocialPlatform\n"
        "assert GenerationContentType.text in g, g\n"
        "assert SocialPlatform.telegram in p, p\n"
        "assert SocialPlatform.instagram in p, p\n"
        "print('OK')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout
