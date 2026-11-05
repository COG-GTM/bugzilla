FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TZ=Etc/UTC
WORKDIR /srv
COPY pyproject.toml ./
RUN pip install --no-cache-dir "fastapi>=0.115,<1" "uvicorn[standard]>=0.30,<1" "sqlalchemy>=2.0,<3" \
        "pymysql>=1.1,<2" "cryptography>=42" "pydantic>=2,<3"
COPY app ./app
# gid 33 = www-data in the Perl image: data/params.json on the shared volume is root:www-data 0660
RUN groupadd -g 33 www-data 2>/dev/null || true && useradd -r -u 10001 -g 33 bugzilla
USER bugzilla
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
