#!/bin/bash
# CodeBuild entrypoint — reads config from environment and runs the worker
python -m src.worker.run
