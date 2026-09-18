ARG PYTHON_IMAGE=mirror.gcr.io/library/python:3.12-slim
FROM ${PYTHON_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv/paperlib

COPY pyproject.toml ./
COPY app/__init__.py ./app/__init__.py
RUN pip install '.[dev]'

COPY app ./app
COPY static ./static
COPY tests ./tests

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
