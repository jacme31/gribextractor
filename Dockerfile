ARG PYTHON_VERSION=3.12.16
FROM python:${PYTHON_VERSION}-slim AS base

# Prevents Python from writing pyc files.
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Default values for PUID/PGID (can be overridden in docker-compose)
ENV PUID=1045
ENV PGID=1045

WORKDIR /app

# Install required packages
RUN apt-get update && \
    apt-get install -y curl gosu git && \
    rm -rf /var/lib/apt/lists/*


# Create initial user/group (will be modified dynamically at runtime)
RUN groupadd -g 1045 appgroup && \
    useradd -u 1045 -g 1045 -m -s /bin/bash appuser

# Install Python dependencies
RUN --mount=type=cache,target=/root/.cache/pip \
    --mount=type=bind,source=requirements.txt,target=requirements.txt \
    pip install -r requirements.txt

# Copy application files
COPY . .

# Copy the entrypoint
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 5959

ENTRYPOINT ["/entrypoint.sh"]
CMD ["python3", "gribextractor.py"]

