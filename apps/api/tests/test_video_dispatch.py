from unittest.mock import patch

from app.content_pipeline.video_dispatch import dispatch_video_generator


def test_seedance_provider_routes_to_seedance() -> None:
    payload = {"topic": "x", "provider": "seedance"}
    with patch(
        "app.content_pipeline.video_dispatch.seedance_video_generator",
        return_value={"video_url": "u"},
    ) as seedance, patch(
        "app.content_pipeline.video_dispatch.veo_video_generator"
    ) as veo:
        dispatch_video_generator(payload)
    seedance.assert_called_once()
    veo.assert_not_called()


def test_default_and_veo_provider_route_to_veo() -> None:
    for payload in ({"topic": "x"}, {"topic": "x", "provider": "veo"}):
        with patch(
            "app.content_pipeline.video_dispatch.veo_video_generator",
            return_value={"video_url": "u"},
        ) as veo, patch(
            "app.content_pipeline.video_dispatch.seedance_video_generator"
        ) as seedance:
            dispatch_video_generator(payload)
        veo.assert_called_once()
        seedance.assert_not_called()
