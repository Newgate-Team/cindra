from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://cindra:cindra@localhost:5433/cindra"
    redis_url: str = "redis://localhost:6380/0"
    jwt_secret: str = "dev-only-insecure-secret-change-in-.env"
    social_token_encryption_key: str = "miLbnE1KsbWEH0uvZOPC03XFYh_NydEqOpPk0KVAn18="
    # CIN-53 resolved 2026-08-04: real key obtained, gemini-2.5-flash-lite
    # is deprecated for new accounts (404 "no longer available to new
    # users") -- gemini-3.5-flash-lite confirmed as a real, working
    # model with a real generateContent response (candidates/usageMetadata
    # present, modelVersion echoed back). Note: generativelanguage
    # .googleapis.com enforces a "User location is not supported" check
    # per-request based on the caller's IP -- confirmed to fail from
    # Kazakhstan-based networks (both a sandboxed dev environment and a
    # real residential Mac) but succeed from at least one other network.
    # This is a request-origin check, not an account/key restriction --
    # Railway's US/EU-hosted servers are expected to be unaffected, but
    # local development from a KZ network may need a VPN to reach this
    # API directly. Same key covers Imagen (CIN-54) and Veo (CIN-55).
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash-lite"
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
    # Must be a JSON number in the request body, not a string -- Veo's
    # predictLongRunning rejects a string durationSeconds with a 400
    # (confirmed against a real, paid production call, CIN-112).
    veo_duration_seconds: int = 8
    veo_resolution: str = "1080p"
    # fal.ai API key (CIN-144): one key unlocks Seedance and the rest
    # of fal's model catalog. Empty until the owner creates a fal.ai
    # account (gate, like the other keys above) -- while empty the
    # video studio's «Полное авто» keeps using Veo.
    fal_key: str = ""
    seedance_model: str = "bytedance/seedance-2.5/text-to-video"
    # Strings by fal's schema (unlike Veo's numeric durationSeconds,
    # CIN-112): duration "auto"|"4".."30", resolution "480p"|"720p"
    # (720p is Seedance 2.5's ceiling). 30s is the whole point -- an
    # entire short in one generation.
    seedance_duration: str = "30"
    seedance_resolution: str = "720p"
    # OAuth 2.0 Web client ID from Google Cloud Console (CIN-133).
    # Empty until the client is created there (requires browser access
    # to console.cloud.google.com) -- POST /auth/google returns 503
    # until configured. The same value goes to the frontend as
    # NEXT_PUBLIC_GOOGLE_CLIENT_ID.
    google_client_id: str = ""
    # Empty until CIN-51 is resolved -- see that gate ticket.
    telegram_bot_token: str = ""
    # Empty until CIN-52 is resolved -- see that gate ticket.
    meta_app_id: str = ""
    meta_app_secret: str = ""
    meta_redirect_uri: str = "https://app.cindra.example/oauth/instagram/callback"
    # TikTok Login Kit + Content Posting API. The redirect URI must be
    # registered verbatim in the TikTok developer app.
    tiktok_client_key: str = ""
    tiktok_client_secret: str = ""
    tiktok_redirect_uri: str = "https://cindra-chi.vercel.app/oauth/tiktok/callback"
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
    # Empty until a webhook is registered for a real public URL (see
    # CIN-86) -- Apps & Credentials -> app -> Add Webhook in the PayPal
    # Developer Dashboard. Required for verify_webhook_signature; the
    # webhook handler fails closed (rejects) without it, it does not
    # silently skip verification.
    paypal_webhook_id: str = ""
    # PayPal Plan IDs for each paid tier -- created via PayPal
    # Dashboard/API (see CIN-87), not something this code can invent.
    # Empty until CIN-87 creates the real Products/Plans.
    paypal_pro_plan_id: str = ""
    paypal_business_plan_id: str = ""

    model_config = {"env_file": ".env"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
