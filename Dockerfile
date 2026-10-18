FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN groupadd --gid 10001 navigation && useradd --uid 10001 --gid navigation --no-create-home navigation && mkdir /data && chown navigation:navigation /data
COPY --chown=navigation:navigation app.py /app/app.py
COPY --chown=navigation:navigation site_metadata.py site_ai.py site_search.py /app/
COPY --chown=navigation:navigation web /app/web
USER navigation
EXPOSE 8765
CMD ["python", "app.py", "serve", "--bind", "0.0.0.0", "--data-dir", "/data"]
