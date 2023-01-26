import pytest
from pathlib import Path
from fastapi.testclient import TestClient
from pytest_mock import MockerFixture


class FakeDockerfileProcessor:
    def __init__(self):
        self.jobs = {}

    def process_job(self, job_id: str) -> None:
        self.jobs[job_id] = "bar"


def fake_create_new_job(dp: FakeDockerfileProcessor, job_id: str, content: bytes) -> None:
    dp.process_job(job_id)


@pytest.fixture
def client(mocker: MockerFixture):
    mocker.patch(
        'kube_platform.dockerfile_processor.DockerfilesProcessor',
        FakeDockerfileProcessor
    )
    mocker.patch('kube_platform.app.create_new_job', fake_create_new_job)
    from kube_platform.app import app
    return TestClient(app)


@pytest.mark.parametrize(
    "dockerfile, expected_status_code",
    [
        ("test1.Dockerfile", 202),
        ("test2.Dockerfile", 202),
        ("test1.sh", 400),
        ("test1.txt", 400),
    ]

)
def test_upload_file(client, dockerfile: str, expected_status_code: int):
    path = f'{str(Path(__file__).parent.resolve())}/test_dockerfiles/{dockerfile}'
    files = {'file': open(path, 'rb')}
    response = client.post("/upload_file/", files=files)
    assert response.status_code == expected_status_code

    if dockerfile == "test1.Dockerfile":
        # Try same request again, to ensure it returns an error
        response = client.post("/upload_file/", files=files)
        assert response.status_code == 400
