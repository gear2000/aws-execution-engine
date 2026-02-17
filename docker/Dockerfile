FROM public.ecr.aws/lambda/python:3.14

# Install system dependencies
RUN dnf install -y jq curl git tar gzip && dnf clean all

# Install sops
RUN curl -Lo /usr/local/bin/sops \
    https://github.com/getsops/sops/releases/download/v3.9.4/sops-v3.9.4.linux.amd64 && \
    chmod +x /usr/local/bin/sops

# Install age
RUN curl -Lo /tmp/age.tar.gz \
    https://github.com/FiloSottile/age/releases/download/v1.2.1/age-v1.2.1-linux-amd64.tar.gz && \
    tar -xzf /tmp/age.tar.gz -C /tmp && \
    mv /tmp/age/age /usr/local/bin/age && \
    mv /tmp/age/age-keygen /usr/local/bin/age-keygen && \
    chmod +x /usr/local/bin/age /usr/local/bin/age-keygen && \
    rm -rf /tmp/age /tmp/age.tar.gz

# Install Python dependencies
COPY requirements.txt ./
RUN python3 -m pip install -r requirements.txt --target ${LAMBDA_TASK_ROOT}

# Copy source
COPY src/ ${LAMBDA_TASK_ROOT}/src/

# Copy CodeBuild entrypoint
COPY src/worker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Default handler (overridden per Lambda function via image_config.command)
CMD ["src.worker.handler.handler"]
