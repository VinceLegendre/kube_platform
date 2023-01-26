FROM python:3.10.9

RUN mkdir -p /app

COPY kube_platform /app/kube_platform

COPY requirements.txt /tmp/requirements.txt

RUN pip install -r /tmp/requirements.txt --no-cache-dir

ENV PYTHONPATH "/app"

ENTRYPOINT ["uvicorn"]

CMD ["kube_platform.app:app", "--app-dir", "/app/src", "--workers", "1", "--host" ,"0.0.0.0", "--port", "8000"]
