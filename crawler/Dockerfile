# Data Refinery scraper node: Crawl4AI behind a small token-protected API (Hugging Face Space, free CPU).
FROM python:3.11-slim
RUN pip install --no-cache-dir crawl4ai fastapi uvicorn && \
    python -m playwright install --with-deps chromium
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user PATH=/home/user/.local/bin:$PATH
WORKDIR /home/user/app
COPY --chown=user app.py .
EXPOSE 7860
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7860"]
