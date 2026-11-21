FROM python:3.13-slim
WORKDIR /app
COPY server.py index.html admin.html ./
ENV PYTHONUNBUFFERED=1 CIRCLE_HOST=0.0.0.0 PORT=4174 CIRCLE_DB_PATH=/data/circle.sqlite3
RUN mkdir /data
EXPOSE 4174
CMD ["python", "server.py"]
