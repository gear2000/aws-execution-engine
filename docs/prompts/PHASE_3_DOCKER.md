# Phase 3: Docker

Build the single Docker image used by all Lambda functions and CodeBuild. Depends on Phase 1 and 2 being complete.

---

## P3.1 — Dockerfile + requirements.txt

```
Read CLAUDE.md and docs/REPO_STRUCTURE.md (Docker Image Strategy section).

Create requirements.txt at repo root:
- boto3
- requests
- pyyaml

Create docker/Dockerfile:
- Base: public.ecr.aws/lambda/python:3.14
- Install system deps: jq, curl, git (use dnf or yum depending on AL2023 availability)
- Install sops binary (download from Mozilla SOPS GitHub releases, latest stable)
- Install age binary (download from FiloSottile/age GitHub releases)
- Copy requirements.txt and pip install to ${LAMBDA_TASK_ROOT}
- Copy src/ to ${LAMBDA_TASK_ROOT}/src/
- Default CMD: ["src.worker.handler.handler"]

Verify the image builds:
  docker build -f docker/Dockerfile -t iac-ci .

Verify each entrypoint resolves (expect import to succeed, not a runtime crash):
  docker run --entrypoint python iac-ci -c "from src.init_job.handler import handler; print('OK')"
  docker run --entrypoint python iac-ci -c "from src.orchestrator.handler import handler; print('OK')"
  docker run --entrypoint python iac-ci -c "from src.watchdog_check.handler import handler; print('OK')"
  docker run --entrypoint python iac-ci -c "from src.worker.handler import handler; print('OK')"
```

## P3.2 — Update GitHub Actions Test Workflow (Phase 3)

```
Update .github/workflows/test.yml to add Phase 3 Docker build test.

Add a new job that depends on phase2-lambdas:

  phase3-docker:
    needs: phase2-lambdas
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Build Docker image
        run: docker build -f docker/Dockerfile -t iac-ci .
      - name: Verify init_job entrypoint
        run: |
          docker run --entrypoint python iac-ci -c \
            "from src.init_job.handler import handler; print('OK')"
      - name: Verify orchestrator entrypoint
        run: |
          docker run --entrypoint python iac-ci -c \
            "from src.orchestrator.handler import handler; print('OK')"
      - name: Verify watchdog_check entrypoint
        run: |
          docker run --entrypoint python iac-ci -c \
            "from src.watchdog_check.handler import handler; print('OK')"
      - name: Verify worker entrypoint
        run: |
          docker run --entrypoint python iac-ci -c \
            "from src.worker.handler import handler; print('OK')"
      - name: Verify sops installed
        run: docker run --entrypoint sops iac-ci --version
      - name: Verify age installed
        run: docker run --entrypoint age-keygen iac-ci --version
```
