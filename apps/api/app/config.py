from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://cindra:cindra@localhost:5433/cindra"
    redis_url: str = "redis://localhost:6380/0"
    jwt_secret: str = "dev-only-insecure-secret-change-in-.env"
    social_token_encryption_key: str = "miLbnE1KsbWEH0uvZOPC03XFYh_NydEqOpPk0KVAn18="
    # Empty until CIN-53 is resolved -- see that ticket. The client
    # below is real (real endpoint, real request shape); only the
    # credential is missing. Same key covers Imagen (CIN-54) and Veo
    # (CIN-55) -- one Google AI Studio key for the whole stack.
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash-lite"
    # Imagen 4 (old imagen_model setting) was deprecated by Google,
    # shut down 2026-08-17 -- migrated in CIN-58 to the Interactions
    # API's Nano Banana family. gemini-2.5-flash-image (not
    # gemini-3.1-flash-image/"Nano Banana 2") chosen: still officially
    # supported (no deprecation date, unlike Imagen 4), and priced
    # close to the old $0.04/image budget ($0.039/image vs 3.1's
    # ~$0.045/image) -- see CIN-58 for the price comparison.
    image_model: str = "gemini-2.5-flash-image"
    # Was "veo-3.0-fast-generate-001" (does not exist) until CIN-57
    # caught it -- see that ticket for how the bug slipped through
    # the earlier manual verification.
    veo_model: str = "veo-3.1-fast-generate-preview"
    veo_duration_seconds: str = "8"
    veo_resolution: str = "1080p"
    # Empty until CIN-51 is resolved -- see that gate ticket.
    telegram_bot_token: str = ""
    # Empty until CIN-52 is resolved -- see that gate ticket.
    meta_app_id: str = ""
    meta_app_secret: str = ""
    meta_redirect_uri: str = "https://app.cindra.example/oauth/instagram/callback"
    # Comma-separated. Without CORS the browser blocks every request
    # from the web app (blocked at the OPTIONS preflight, not
    # something curl-based manual testing ever exercises) -- found
    # while verifying CIN-60's frontend changes against a real
    # browser for the first time.
    cors_origins: str = "http://localhost:3000"
    # Empty until CIN-56/CIN-78 gate ticket is resolved (Cloudflare
    # account + bucket not created yet). Generated images/video need a
    # real public URL -- Imagen/Veo return base64/a temporary
    # authenticated Google URI, neither usable as Post.image_url
    # (Instagram Content Publishing API in particular requires a
    # public URL, no direct-upload alternative exists there).
    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket_name: str = "cindra-media"
    # Public base URL for the bucket (r2.dev subdomain or custom
    # domain) -- uploaded keys are appended to this to build the URL
    # returned to callers.
    r2_public_url_base: str = ""
    # CIN-18 (2026-08-04): provider switched from CloudPayments to
    # PayPal -- see that ticket for the full reasoning (CloudPayments
    # approval takes ~2 weeks; PayPal Business can be opened as a sole
    # proprietor immediately, and its webhook-verification mechanism is
    # actually publicly documented, unlike CloudPayments' -- see CIN-79
    # history). client_credentials grant verified live against
    # api-m.sandbox.paypal.com (see CIN-85) -- only real values are
    # missing before this reaches production.
    paypal_client_id: str = ""
    paypal_client_secret: str = ""
    paypal_mode: str = "sandbox"  # "sandbox" or "live"
    # PayPal Plan IDs for each paid tier -- created via PayPal
    # Dashboard/API (see CIN-87), not something this code can invent.
    # Empty until CIN-87 creates the real Products/Plans.
    paypal_pro_plan_id: str = ""
    paypal_business_plan_id: str = ""

    model_config = {"env_file": ".env"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
