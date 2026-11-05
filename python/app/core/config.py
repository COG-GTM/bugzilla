import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    db_host: str = os.environ.get("BZ_DB_HOST", "127.0.0.1")
    db_port: int = int(os.environ.get("BZ_DB_PORT", "3306"))
    db_user: str = os.environ.get("BZ_DB_USER", "bugs")
    db_pass: str = os.environ.get("BZ_DB_PASS", "bugzilla")
    db_name: str = os.environ.get("BZ_DB_NAME", "bugs")
    # Perl's data/params.json; mounted read-only from the bugzilla5-data-dir volume.
    params_path: str = os.environ.get("BZ_PARAMS_PATH", "/var/www/html/data/params.json")
    rest_prefix: str = os.environ.get("BZ_REST_PREFIX", "/rest.cgi")
    version: str = os.environ.get("BZ_VERSION", "5.2+")

    @property
    def db_url(self) -> str:
        return (f"mysql+pymysql://{self.db_user}:{self.db_pass}@{self.db_host}:{self.db_port}/{self.db_name}"
                "?charset=utf8mb4")


settings = Settings()
