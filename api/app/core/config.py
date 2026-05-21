from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables.

    Mirrors the BZ_* env vars used in docker-compose.yml for seamless
    integration with the existing Bugzilla infrastructure.
    """

    bz_db_host: str = "localhost"
    bz_db_port: int = 3306
    bz_db_user: str = "bugs"
    bz_db_pass: str = ""
    bz_db_name: str = "bugs"
    bz_db_driver: str = "pymysql"

    api_token_secret: str = "change-me-in-production"
    api_token_expire_minutes: int = 1440  # 24 hours

    model_config = {"env_prefix": "", "case_sensitive": False}

    @property
    def database_url(self) -> str:
        from urllib.parse import quote_plus

        return (
            f"mysql+{self.bz_db_driver}://{quote_plus(self.bz_db_user)}:{quote_plus(self.bz_db_pass)}"
            f"@{self.bz_db_host}:{self.bz_db_port}/{self.bz_db_name}"
        )


settings = Settings()
