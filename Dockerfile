FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN useradd --create-home --uid 10001 lite && mkdir /app/data && chown lite:lite /app/data
COPY lite_app ./lite_app
USER lite
ENV LITE_DB_PATH=/app/data/lite.db LITE_MLLP_HOST=0.0.0.0 LITE_MLLP_PORT=5200
EXPOSE 8091 5200
CMD ["uvicorn", "lite_app.main:app", "--host", "0.0.0.0", "--port", "8091", "--workers", "1", "--no-proxy-headers"]
