import os
import re
from enum import Enum
from typing import Optional

from dotenv import find_dotenv, load_dotenv
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv(find_dotenv(), override=False)


def env_first(*names: str, default: str = "") -> str:
    """Return the first present environment variable value among names, else default.
    Always returns a string.
    """
    for n in names:
        v = os.getenv(n)
        if v is not None:
            return v
    return default


def bool_from_str(s: str) -> bool:
    try:
        return s.strip().lower() in {"1", "true", "yes"}
    except Exception:
        return False


class Profile(str, Enum):
    dev = "dev"
    staging = "staging"
    prod = "prod"


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _find_default_rules_file() -> str | None:
    """Return a default rules file path if one exists under the repo, else None.
    Looks for config/rules.yaml | config/rules.yml | config/rules.json at project root.
    """
    candidates = [
        os.path.join(PROJECT_ROOT, "config", "rules.yaml"),
        os.path.join(PROJECT_ROOT, "config", "rules.yml"),
        os.path.join(PROJECT_ROOT, "config", "rules.json"),
    ]
    for p in candidates:
        try:
            if os.path.exists(p):
                return p
        except Exception:
            continue
    return None


class AppSettings(BaseSettings):
    # Profiles
    PROFILE: Profile = Field(default=Profile.dev, validation_alias="PROFILE")

    # API Configuration
    API_VERSION: str = Field(
        default_factory=lambda: env_first(
            "GOOGLE_ADS_API_VERSION",
            "ADS_API_VERSION",
            default="v18",
        )
    )
    API_VERSION_CANARY: str = Field(
        default_factory=lambda: env_first(
            "GOOGLE_ADS_API_VERSION_CANARY",
            "ADS_API_VERSION_CANARY",
            default="",
        )
    )

    # IDs and tokens with aliases
    CUSTOMER_ID: str = Field(
        default_factory=lambda: env_first(
            "GOOGLE_ADS_CUSTOMER_ID",
            "ADS_CUSTOMER_ID",
            default="",
        )
    )
    CAMPAIGN_ID: str = Field(
        default_factory=lambda: env_first(
            "GOOGLE_ADS_CAMPAIGN_ID",
            "ADS_CAMPAIGN_ID",
            default="",
        )
    )
    DEVELOPER_TOKEN: str = Field(
        default_factory=lambda: env_first(
            "GOOGLE_ADS_DEVELOPER_TOKEN",
            "ADS_DEV_TOKEN",
            default="",
        )
    )
    LOGIN_CUSTOMER_ID: str = Field(
        default_factory=lambda: env_first(
            "GOOGLE_ADS_LOGIN_CUSTOMER_ID",
            "ADS_LOGIN_ID",
            default="",
        )
    )

    # Local Services Ads (LSA) reporting API config
    # Default derives from CUSTOMER_ID as accounts/{CID}; can be overridden via LSA_ACCOUNT
    # Explicit default ensures detailed leads work and avoids using manager CUSTOMER_ID.
    # Override with LSA_ACCOUNT in the environment or .env as needed.
    LSA_ACCOUNT: str = Field(default_factory=lambda: (os.getenv("LSA_ACCOUNT") or "accounts/4373068411"))

    # Flags
    VALIDATE_ONLY: bool = Field(
        default_factory=lambda: bool_from_str(env_first("VALIDATE_ONLY", "GOOGLE_ADS_VALIDATE_ONLY", default="false"))
    )
    DRY_RUN: bool = Field(default_factory=lambda: bool_from_str(env_first("DRY_RUN", default="false")))
    REQUIRE_LOCAL_SERVICES_ONLY: bool = Field(
        default_factory=lambda: bool_from_str(
            env_first("REQUIRE_LOCAL_SERVICES_ONLY", "REQUIRE_LSA_ONLY", default="false")
        )
    )
    # Control whether we attempt to pause/enable LSA via Ads campaign.status
    # Some LSA serving states are controlled in the Local Services UI and can diverge from Ads status.
    LSA_MUTATE_VIA_ADS_STATUS: bool = Field(
        default_factory=lambda: bool_from_str(
            env_first("LSA_MUTATE_VIA_ADS_STATUS", "ADS_MUTATE_LSA_STATUS", default="true")
        )
    )

    # Test account creation
    CREATE_TEST_ACCOUNT: bool = Field(
        default_factory=lambda: (os.getenv("CREATE_TEST_ACCOUNT", "false").lower() in {"1", "true", "yes"})
    )
    TEST_ACCOUNT_NAME: str = "API Test Account"
    TEST_ACCOUNT_CURRENCY: str = "USD"
    TEST_ACCOUNT_TIME_ZONE: str = "America/New_York"
    TEST_ACCOUNT_TRACKING_URL_TEMPLATE: str = "{lpurl}?device={device}"

    # Weather testing
    FORCE_ALERT: bool = Field(default_factory=lambda: bool_from_str(env_first("FORCE_ALERT", default="false")))
    FORCE_EVENT: str = Field(default_factory=lambda: os.getenv("FORCE_EVENT", "Tornado Warning"))

    # Weather config
    STORM_HOLD_TIME_HOURS: int = Field(
        default_factory=lambda: int(env_first("HOLD_HOURS", "STORM_HOLD_TIME_HOURS", default="24"))
    )
    CENTER_LAT: Optional[str] = Field(default_factory=lambda: os.getenv("CENTER_LAT"))
    CENTER_LON: Optional[str] = Field(default_factory=lambda: os.getenv("CENTER_LON"))
    MAX_DISTANCE_MI: float = Field(
        default_factory=lambda: float(env_first("RADIUS_MI", "MAX_DISTANCE_MI", default="0"))
    )
    NWS_TIMEOUT_SECONDS: int = Field(
        default_factory=lambda: int(env_first("TIMEOUT_SECONDS", "NWS_TIMEOUT_SECONDS", default="10"))
    )
    # Rules engine
    RULES_FILE: str | None = Field(default_factory=lambda: (os.getenv("RULES_FILE") or _find_default_rules_file()))

    # Notifications
    ENABLE_NOTIFICATIONS: bool = Field(
        default_factory=lambda: bool_from_str(env_first("ENABLE_NOTIFICATIONS", default="false"))
    )
    ENABLE_EMAIL: bool = Field(default_factory=lambda: bool_from_str(env_first("ENABLE_EMAIL", default="true")))

    SMTP_HOST: str = Field(default_factory=lambda: os.getenv("SMTP_HOST", ""))
    SMTP_PORT: int = Field(default_factory=lambda: int(env_first("SMTP_PORT", default="587")))
    SMTP_USER: str = Field(default_factory=lambda: os.getenv("SMTP_USER", ""))
    SMTP_PASSWORD: str = Field(default_factory=lambda: os.getenv("SMTP_PASSWORD", ""))
    EMAIL_FROM: str = Field(default_factory=lambda: os.getenv("EMAIL_FROM", os.getenv("SMTP_USER", "")))
    EMAIL_TO: str = Field(default_factory=lambda: os.getenv("EMAIL_TO", "zach@911treeremovals.com"))

    # Resilience
    ADS_BACKOFF_MAX_ATTEMPTS: int = 5
    ADS_BACKOFF_BASE_SECONDS: float = 0.5
    ADS_BACKOFF_MAX_SLEEP: float = 10.0
    ADS_BREAKER_THRESHOLD: int = 3
    ADS_BREAKER_COOLDOWN_MIN: int = 10
    ADS_BREAKER_NOTIFY_COOLDOWN_MIN: int = 60
    # HTTP timeouts for Ads API calls (seconds)
    ADS_HTTP_TIMEOUT_SECONDS: float = Field(
        default_factory=lambda: float(env_first("ADS_HTTP_TIMEOUT_SECONDS", default="20"))
    )
    # Rate limiting
    ADS_QPS: float = Field(default_factory=lambda: float(env_first("ADS_QPS", default="2")))
    ADS_BURST: int = Field(default_factory=lambda: int(env_first("ADS_BURST", default="5")))

    # Observability
    METRICS_PORT: int = Field(default_factory=lambda: int(env_first("METRICS_PORT", default="0")))
    HEALTH_PORT: int = Field(default_factory=lambda: int(env_first("HEALTH_PORT", default="0")))
    SENTRY_DSN: str = Field(default_factory=lambda: os.getenv("SENTRY_DSN", ""))
    SENTRY_ENV: str = Field(default_factory=lambda: os.getenv("SENTRY_ENV") or os.getenv("ENVIRONMENT", ""))
    SENTRY_TRACES: float = Field(default_factory=lambda: float(env_first("SENTRY_TRACES", default="0")))

    # Feature flags / kill switches
    SAFE_MODE: bool = Field(default_factory=lambda: bool_from_str(env_first("SAFE_MODE", default="false")))
    KILL_SWITCH: bool = Field(default_factory=lambda: bool_from_str(env_first("KILL_SWITCH", default="false")))
    MAX_MUTATIONS_PER_DAY: int = Field(
        default_factory=lambda: int(env_first("MAX_MUTATIONS_PER_DAY", default="0"))
    )  # 0 = unlimited
    QUIET_HOURS: str = Field(default_factory=lambda: env_first("QUIET_HOURS", default=""))  # e.g. "22:00-07:00"
    CANARY_COUNTIES: list[str] = Field(
        default_factory=lambda: [s.strip() for s in os.getenv("CANARY_COUNTIES", "").split(",") if s.strip()]
    )
    ALERT_SUPPRESSION_MINUTES: int = Field(
        default_factory=lambda: int(env_first("ALERT_SUPPRESSION_MINUTES", default="0"))
    )
    # Validate-only first-pass gate (two-phase mutate)
    VALIDATE_GATE: bool = Field(default_factory=lambda: bool_from_str(env_first("VALIDATE_GATE", default="true")))
    # Safer targeting: only control campaigns that have one of these labels
    REQUIRED_CAMPAIGN_LABELS: list[str] = Field(
        default_factory=lambda: [s.strip() for s in os.getenv("REQUIRED_CAMPAIGN_LABELS", "").split(",") if s.strip()]
    )

    model_config = SettingsConfigDict(env_file=find_dotenv(), env_file_encoding="utf-8", extra="ignore")

    @field_validator("CUSTOMER_ID", "CAMPAIGN_ID", "LOGIN_CUSTOMER_ID")
    @classmethod
    def only_digits_or_empty(cls, v: str):
        if not v:
            return v
        if not re.fullmatch(r"\d{6,}", v):
            raise ValueError("must be digits only, no dashes")
        return v

    @field_validator("PROFILE")
    @classmethod
    def profile_logic(cls, v):
        # Accept common aliases
        if isinstance(v, str):
            t = v.lower()
            if t in {"production", "prod"}:
                return Profile.prod
            if t in {"stage", "staging"}:
                return Profile.staging
            if t in {"dev", "development"}:
                return Profile.dev
        return v

    def assert_profile_constraints(self):
        # Refuse to start with conflicting flags in prod
        if self.PROFILE == Profile.prod:
            conflicts = []
            if self.DRY_RUN:
                conflicts.append("DRY_RUN=true")
            if self.FORCE_ALERT:
                conflicts.append("FORCE_ALERT=true")
            if self.CREATE_TEST_ACCOUNT:
                conflicts.append("CREATE_TEST_ACCOUNT=true")
            if conflicts:
                raise ValueError(f"Invalid prod configuration: {', '.join(conflicts)}")


# Instantiate and expose settings
settings = AppSettings()
settings.assert_profile_constraints()

# Back-compat module-level names used across the codebase
API_VERSION = settings.API_VERSION
API_ENDPOINT = f"https://googleads.googleapis.com/{API_VERSION}"
API_VERSION_CANARY = settings.API_VERSION_CANARY
CUSTOMER_ID = settings.CUSTOMER_ID
CAMPAIGN_ID = settings.CAMPAIGN_ID
DEVELOPER_TOKEN = settings.DEVELOPER_TOKEN
LOGIN_CUSTOMER_ID = settings.LOGIN_CUSTOMER_ID
HAS_CUSTOMER_ID = bool(CUSTOMER_ID)
HAS_CAMPAIGN_ID = bool(CAMPAIGN_ID)
VALIDATE_ONLY = settings.VALIDATE_ONLY or settings.SAFE_MODE
DRY_RUN = settings.DRY_RUN or settings.SAFE_MODE
REQUIRE_LOCAL_SERVICES_ONLY = settings.REQUIRE_LOCAL_SERVICES_ONLY
CREATE_TEST_ACCOUNT = settings.CREATE_TEST_ACCOUNT
TEST_ACCOUNT_NAME = settings.TEST_ACCOUNT_NAME
TEST_ACCOUNT_CURRENCY = settings.TEST_ACCOUNT_CURRENCY
TEST_ACCOUNT_TIME_ZONE = settings.TEST_ACCOUNT_TIME_ZONE
TEST_ACCOUNT_TRACKING_URL_TEMPLATE = settings.TEST_ACCOUNT_TRACKING_URL_TEMPLATE
FORCE_ALERT = settings.FORCE_ALERT
FORCE_EVENT = settings.FORCE_EVENT

# Feature flags / kill switches (module-level exports)
SAFE_MODE = settings.SAFE_MODE
KILL_SWITCH = settings.KILL_SWITCH
MAX_MUTATIONS_PER_DAY = settings.MAX_MUTATIONS_PER_DAY
QUIET_HOURS = settings.QUIET_HOURS
CANARY_COUNTIES = settings.CANARY_COUNTIES
ALERT_SUPPRESSION_MINUTES = settings.ALERT_SUPPRESSION_MINUTES
VALIDATE_GATE = settings.VALIDATE_GATE
REQUIRED_CAMPAIGN_LABELS = settings.REQUIRED_CAMPAIGN_LABELS
LSA_MUTATE_VIA_ADS_STATUS = settings.LSA_MUTATE_VIA_ADS_STATUS
LSA_ACCOUNT = settings.LSA_ACCOUNT
ADS_HTTP_TIMEOUT_SECONDS = settings.ADS_HTTP_TIMEOUT_SECONDS

# Weather Configuration
STATE_CODES = ["IN", "IL", "KY"]
STORM_HOLD_TIME_HOURS = int(env_first("HOLD_HOURS", "STORM_HOLD_TIME_HOURS", default="24"))

# County targeting
# Primary: FIPS county codes (5-digit). These are more reliable than string matching.
# Example set for Evansville tri-state. Edit to your service area.
TARGET_COUNTY_FIPS = {
    # Indiana (state FIPS 18)
    "18163",  # Vanderburgh
    "18173",  # Warrick
    "18129",  # Posey
    "18051",  # Gibson
    "18147",  # Spencer
    "18125",  # Pike
    "18037",  # Dubois
    "18123",  # Perry
    "18083",  # Knox
    "18027",  # Daviess (IN)
    "18101",  # Martin
    "18055",  # Greene
    "18153",  # Sullivan
    "18117",  # Orange
    "18025",  # Crawford (IN)
    # Kentucky (state FIPS 21)
    "21101",  # Henderson
    "21059",  # Daviess (KY)
    "21091",  # Hancock
    "21149",  # McLean
    "21183",  # Ohio
    "21233",  # Webster
    "21225",  # Union
    "21107",  # Hopkins
    "21177",  # Muhlenberg
    "21055",  # Crittenden
    "21033",  # Caldwell
    "21139",  # Livingston
    "21027",  # Breckinridge
    # Illinois (state FIPS 17)
    "17185",  # Wabash
    "17193",  # White
    "17047",  # Edwards
    "17101",  # Lawrence
    "17159",  # Richland
    "17191",  # Wayne
    "17059",  # Gallatin
    "17165",  # Saline
    "17065",  # Hamilton
    "17069",  # Hardin
}

# Fallback: county name contains checks against areaDesc
TARGET_COUNTIES = {
    # Indiana
    "Vanderburgh County",
    "Warrick County",
    "Posey County",
    "Gibson County",
    "Spencer County",
    "Pike County",
    "Dubois County",
    "Perry County",
    "Knox County",
    "Daviess County",
    "Martin County",
    "Greene County",
    "Sullivan County",
    "Orange County",
    "Crawford County",
    # Kentucky
    "Henderson County",
    "Daviess County",
    "Hancock County",
    "McLean County",
    "Ohio County",
    "Webster County",
    "Union County",
    "Hopkins County",
    "Muhlenberg County",
    "Crittenden County",
    "Caldwell County",
    "Livingston County",
    "Breckinridge County",
    # Illinois
    "Wabash County",
    "White County",
    "Edwards County",
    "Lawrence County",
    "Richland County",
    "Wayne County",
    "Gallatin County",
    "Saline County",
    "Hamilton County",
    "Hardin County",
}

# Weather Events (exact matches to properties.event from api.weather.gov)
TRIGGER_EVENTS = {
    "Severe Thunderstorm Warning",
    "Tornado Warning",
    "High Wind Warning",
    "Ice Storm Warning",
    "Winter Storm Warning",
    "Blizzard Warning",
    "Snow Squall Warning",
    # Add or remove as needed; e.g., uncomment to react to flooding
    # "Flash Flood Warning",
    # "Flood Warning",
}

# Additional alert filters (from alert.properties)
# Only alerts with these values will trigger (leave sets empty to disable filtering)
ALLOWED_SEVERITIES = {"Severe", "Extreme"}
ALLOWED_URGENCY = {"Immediate", "Expected"}
ALLOWED_CERTAINTY = {"Observed", "Likely"}

# Optional geographic filter: keep alerts within radius of a center point
# Set CENTER_LAT/CENTER_LON to enable. Example: Evansville, IN ~ (37.9716, -87.5711)
CENTER_LAT = os.getenv("CENTER_LAT")
CENTER_LON = os.getenv("CENTER_LON")
MAX_DISTANCE_MI = float(env_first("RADIUS_MI", "MAX_DISTANCE_MI", default="0"))  # 0 disables radius filtering

# NWS request timeout (seconds)
NWS_TIMEOUT_SECONDS = int(env_first("TIMEOUT_SECONDS", "NWS_TIMEOUT_SECONDS", default="10"))

# Notifications
ENABLE_NOTIFICATIONS = os.getenv("ENABLE_NOTIFICATIONS", "false").lower() in {"1", "true", "yes"}
ENABLE_EMAIL = os.getenv("ENABLE_EMAIL", "true").lower() in {"1", "true", "yes"}

# Email settings (SMTP)
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
EMAIL_FROM = os.getenv("EMAIL_FROM", SMTP_USER or "")
EMAIL_TO = os.getenv("EMAIL_TO", "zach@911treeremovals.com")

# Resilience settings for Ads calls
ADS_BACKOFF_MAX_ATTEMPTS = int(os.getenv("ADS_BACKOFF_MAX_ATTEMPTS", "5"))
ADS_BACKOFF_BASE_SECONDS = float(os.getenv("ADS_BACKOFF_BASE_SECONDS", "0.5"))
ADS_BACKOFF_MAX_SLEEP = float(os.getenv("ADS_BACKOFF_MAX_SLEEP", "10.0"))
ADS_BREAKER_THRESHOLD = int(os.getenv("ADS_BREAKER_THRESHOLD", "3"))
ADS_BREAKER_COOLDOWN_MIN = int(os.getenv("ADS_BREAKER_COOLDOWN_MIN", "10"))
ADS_BREAKER_NOTIFY_COOLDOWN_MIN = int(os.getenv("ADS_BREAKER_NOTIFY_COOLDOWN_MIN", "60"))
ADS_QPS = settings.ADS_QPS
ADS_BURST = settings.ADS_BURST

# Optional rules file path
RULES_FILE = settings.RULES_FILE

# Metrics/observability
METRICS_PORT = int(os.getenv("METRICS_PORT", "0"))  # 0 disables
HEALTH_PORT = int(os.getenv("HEALTH_PORT", "0"))  # 0 disables
# Sentry (optional)
SENTRY_DSN = os.getenv("SENTRY_DSN", "")
SENTRY_ENV = os.getenv("SENTRY_ENV") or os.getenv("ENVIRONMENT", "")
SENTRY_TRACES = float(os.getenv("SENTRY_TRACES", "0"))
