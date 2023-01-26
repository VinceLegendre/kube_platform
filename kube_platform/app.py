import hashlib
import logging
import pathlib
from typing import Optional
from logging.config import dictConfig

import uvicorn
from fastapi import FastAPI, HTTPException, UploadFile, BackgroundTasks

from kube_platform.dockerfile_processor import DockerfilesProcessor
from kube_platform.log_config import log_config

dictConfig(log_config)

dp = DockerfilesProcessor()
app = FastAPI()

logger = logging.getLogger('kube-platform')


def create_new_job(dockerfile_processor: DockerfilesProcessor, job_id: str, content: bytes) -> None:
    pathlib.Path(f"/data/{job_id}").mkdir(parents=True, exist_ok=True)
    with open(f"/data/{job_id}/Dockerfile", "wb") as f:
        f.write(content)
    dockerfile_processor.process_job(job_id)


@app.get("/")
async def root():
    return {"msg": "Container execution service up for work."}


@app.post("/upload_file/", status_code=202)
async def create_upload_file(file: UploadFile, background_tasks: BackgroundTasks):
    """
    Uploads a Dockerfile to the platform and creates a job from it.
    A job if composed of several sequential steps:
    1. Validation of the dockerfile, using hadolint
    2. Docker image building
    3. Creation of a pod dedicated to run the docker image, which must contain an entrypoint
    :param file:
    :param background_tasks:
    :return:
    """
    if not file.filename.endswith(".Dockerfile"):
        raise HTTPException(
            status_code=400,
            detail="Something is wrong with file extension."
                   "Please submit a valid Dockerfile either named "
                   "'Dockerfile' or with a *.Dockerfile extension"
        )
    content = await file.read()
    file_hash = hashlib.md5(content).hexdigest()
    job_id = f"{file_hash}"
    if job_id in dp.jobs:
        raise HTTPException(
            status_code=400,
            detail=f"Dockerfile already processed, with job_id={job_id}"
        )
    else:
        background_tasks.add_task(create_new_job, dp, job_id, content)
        return {"filename": file.filename, "job_id": job_id}


@app.get("/status/")
async def get_job_status(job_id: Optional[str] = None):
    """
    Returns the execution status of Dockerfiles processing jobs
    :param job_id:
    :return:
    """
    if job_id:
        status = dp.jobs.get(job_id)
        if status:
            return status
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Job {job_id} does not exist"
            )
    else:
        return dp.jobs


if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8000,
                reload=False, log_level="debug")
