import json
import time
import logging
from typing import Dict, Tuple

from kubernetes import client, config
from kubernetes.client import ApiException

logger = logging.getLogger("kube-platform")


class DockerfilesProcessor:
    namespace = "default"
    pv_name = "task-pv-volume"
    pvc_name = "task-pv-claim"
    image_pull_policy = "IfNotPresent"
    restart_policy = "Never"
    docker_repo = "kubelearner/kube-platform"
    docker_secret_name = "regcred"
    active_deadline_seconds = 60

    def __init__(self):
        self.config = config.load_incluster_config()
        self.volume = None
        self.core_api = client.CoreV1Api()
        self.busy = False
        self.jobs = {}

    def process_job(self, job_id) -> None:
        if job_id not in self.jobs:
            logger.info(f"Processing job {job_id}.")
            self.busy = True
            self.jobs[job_id] = {"status": "Running", "completed_stages": []}
            job = self.jobs[job_id]
            try:
                self.kaniko_build(job_id)
                self.grype_scan(job_id)
                self.execute_container(job_id)
                job["status"] = "Completed"
            except JobException:
                job["status"] = "Failed"
            self.busy = False
        else:
            logger.info("Dockerfile already processed.")

    def kaniko_build(self, job_id) -> None:
        name = f"kaniko-{job_id}"
        body = {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {
                "name": name,
                "namespace": self.namespace,
                "labels": {
                    "job_id": job_id
                }
            },
            "spec": {
                "restartPolicy": "Never",
                "containers": [
                    {
                        "args": [
                            f"--context=dir:///data/{job_id}",
                            f"--destination={self.docker_repo}:{job_id}"
                        ],
                        "image": "gcr.io/kaniko-project/executor:latest",
                        "imagePullPolicy": self.image_pull_policy,
                        "name": "kaniko",
                        "volumeMounts": [
                            {
                                "name": self.pv_name,
                                "mountPath": "/data"
                            },
                            {
                                "name": "kaniko-secret",
                                "mountPath": "/kaniko/.docker"
                            }
                        ]
                    }
                ],
                "volumes": [
                    {
                        "name": self.pv_name,
                        "persistentVolumeClaim": {
                            "claimName": self.pvc_name
                        }
                    },
                    {
                        "name": "kaniko-secret",
                        "secret": {
                            "items": [
                                {
                                    "key": ".dockerconfigjson",
                                    "path": "config.json"
                                }
                            ],
                            "secretName": self.docker_secret_name
                        }
                    }
                ]
            }
        }
        (phase, logs) = self.run_pod_to_completion(body)
        job = self.jobs[job_id]
        if phase != "Succeeded":
            job["status"] = "Failed"
            job["error_detail"] = logs
            raise JobException("Failed to run build docker image")
        job["completed_stages"].append("build_docker_image")

    def grype_scan(self, job_id) -> None:
        name = f"grype-{job_id}"
        body = {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {
                "name": name,
                "namespace": self.namespace,
                "labels": {
                    "job_id": job_id
                }
            },
            "spec": {
                "restartPolicy": "Never",
                "containers": [
                    {
                        "args": [
                            f"{self.docker_repo}:{job_id}",
                            "--scope=all-layers",
                            "--fail-on=critical",
                            "-o=json"
                        ],
                        "image": "anchore/grype:latest",
                        "imagePullPolicy": self.image_pull_policy,
                        "name": "grype"
                    }
                ]
            }
        }
        (phase, logs) = self.run_pod_to_completion(body)
        job = self.jobs[job_id]
        if phase != "Succeeded":
            job["status"] = "Failed"
            job["error_detail"] = logs
            raise JobException("Failed to run image scan")
        job["completed_stages"].append("vulnerability_scan")

    def execute_container(self, job_id: str) -> None:
        name = f"execute-{job_id}"
        body = {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {
                "name": name,
                "namespace": self.namespace,
                "labels": {
                    "job_id": job_id
                }
            },
            "spec": {
                "restartPolicy": "Never",
                "activeDeadlineSeconds": self.active_deadline_seconds,
                "containers": [
                    {
                        "image": f"{self.docker_repo}:{job_id}",
                        "imagePullPolicy": self.image_pull_policy,
                        "name": job_id,
                        "volumeMounts": [
                            {
                                "mountPath": "/data",
                                "name": self.pv_name,
                                "subPath": job_id
                            }
                        ]
                    }
                ],
                "volumes": [
                    {
                        "name": self.pv_name,
                        "persistentVolumeClaim": {
                            "claimName": self.pvc_name
                        }
                    }
                ]
            }
        }
        (phase, logs) = self.run_pod_to_completion(body)
        job = self.jobs[job_id]
        if phase != "Succeeded":
            job["status"] = "Failed"
            job["error_detail"] = logs
            raise JobException(f"Failed to execute container from image {self.docker_repo}:{job_id}.")
        job["completed_stages"].append("execute_container")
        with open(f"/data/{job_id}/perf.json", "r") as f:
            perf = json.load(f)
            job["performance"] = perf["perf"]

    def run_pod_to_completion(self, pod_body: Dict) -> Tuple[str, str]:
        pod = None
        name = pod_body["metadata"]["name"]
        namespace = pod_body["metadata"]["namespace"]
        try:
            pod = self.core_api.read_namespaced_pod(name=name, namespace=namespace)
        except ApiException as e:
            if e.status != 404:
                print("Unknown error: %s" % e)
                exit(1)
        if not pod:
            self.core_api.create_namespaced_pod(body=pod_body, namespace=namespace)
        while True:
            pod = self.core_api.read_namespaced_pod(name=name, namespace=namespace)
            if pod.status.phase not in ["Pending", "Running"]:
                break
            time.sleep(1)
        logs = self.core_api.read_namespaced_pod_log(name=name, namespace=namespace)
        self.core_api.delete_namespaced_pod(name=name, namespace=namespace)
        return pod.status.phase, logs


class JobException(Exception):
    pass
