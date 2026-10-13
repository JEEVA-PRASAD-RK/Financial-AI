import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().with_name(".env"), override=False)


def _required_setting(name: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        raise RuntimeError(f"Required environment variable {name} is not set.")
    return value


def _boolean_setting(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Environment variable {name} must be a boolean value.")


def _integer_setting(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"Environment variable {name} must be an integer.") from exc


def get_database_config(*, autocommit: bool = True) -> dict[str, Any]:
    return {
        "host": _required_setting("FIN_AI_DB_HOST"),
        "user": _required_setting("FIN_AI_DB_USER"),
        "password": _required_setting("FIN_AI_DB_PASSWORD"),
        "database": _required_setting("FIN_AI_DB_NAME"),
        "autocommit": autocommit,
    }


def get_jwt_secret_key() -> str:
    return _required_setting("FIN_AI_JWT_SECRET_KEY")


def get_openrouter_api_key() -> str:
    return _required_setting("FIN_AI_OPENROUTER_API_KEY")


def get_mail_config() -> dict[str, Any] | None:
    required = (
        "FIN_AI_MAIL_USERNAME",
        "FIN_AI_MAIL_PASSWORD",
        "FIN_AI_MAIL_FROM",
    )
    if any(not os.getenv(name, "").strip() for name in required):
        return None

    return {
        "MAIL_USERNAME": os.environ["FIN_AI_MAIL_USERNAME"],
        "MAIL_PASSWORD": os.environ["FIN_AI_MAIL_PASSWORD"],
        "MAIL_FROM": os.environ["FIN_AI_MAIL_FROM"],
        "MAIL_PORT": _integer_setting("FIN_AI_MAIL_PORT", 587),
        "MAIL_SERVER": os.getenv("FIN_AI_MAIL_SERVER", "smtp.gmail.com"),
        "MAIL_STARTTLS": _boolean_setting("FIN_AI_MAIL_STARTTLS", True),
        "MAIL_SSL_TLS": _boolean_setting("FIN_AI_MAIL_SSL_TLS", False),
        "USE_CREDENTIALS": True,
        "VALIDATE_CERTS": True,
    }
