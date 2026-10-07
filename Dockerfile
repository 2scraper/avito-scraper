# Builds the Playwright engine (the one the README recommends) into a
# container with its own Chromium — for a scheduled job or a CI canary.
# Not required for local development, where `pip install` directly is simpler.
#
#   docker build -t avito-scraper .
#   docker run --rm -v "$PWD/out:/out" --env-file .env avito-scraper \
#     --mode listing --url https://www.avito.ru/moskva/telefony/mobilnye_telefony/apple-ASgBAgICAkS0wA3OqzmwwQ2I_Dc \
#     --pages 3 --out /out/iphones
#
# Credentials come in through --env-file or a mounted /app/.env. NOTHING here
# bakes one in: a .env baked into an image is a credential published to
# everyone who can pull it.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt requirements-playwright.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-playwright.txt \
    # Playwright's own apt-get for Chromium's shared-library dependencies —
    # not pip packages, so this has to be a separate, explicit step.
    && playwright install --with-deps chromium

# Every module the entrypoint imports, transitively, plus diff_runs.py as a
# companion. smoke_test.py checks this list against the real import graph:
# this family has shipped an image that died with ModuleNotFoundError on
# every invocation, --help included, because nothing ever built it.
COPY browser_bridge.py captcha_solver.py diff_runs.py env_config.py \
     fingerprint_client.py output_writer.py page_flow.py playwright_scraper.py \
     product_parser.py proxy_forwarder.py proxy_pool.py robots_snapshot.py ./

ENTRYPOINT ["python3", "playwright_scraper.py"]
CMD ["--help"]
