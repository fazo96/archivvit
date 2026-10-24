FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright \
    ARCHIVVIT_DATA=/data

WORKDIR /src
COPY pyproject.toml ./
COPY archivvit ./archivvit
# Only the headless shell: the browser is used just to pass Reddit's bot check.
RUN pip install . \
    && playwright install --with-deps --only-shell chromium \
    && rm -rf /var/lib/apt/lists/* /src

RUN useradd --create-home --uid 1000 archivvit \
    && mkdir /data && chown archivvit:archivvit /data
USER archivvit
WORKDIR /data
VOLUME /data
EXPOSE 8080

# The UI has no login: publish the port on localhost or behind a private proxy.
ENTRYPOINT ["archivvit"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8080"]
