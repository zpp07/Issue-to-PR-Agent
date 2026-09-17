FROM m.daocloud.io/docker.io/library/python:3.11-slim

ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

# Runtime versions are taken from the selected commit's pyproject.toml. The
# mounted snapshot supplies pydantic's Python source via PYTHONPATH.
RUN python -m pip install --no-cache-dir --index-url "${PIP_INDEX_URL}" \
      pytest==8.0.2 \
      pytest-mock==3.12.0 \
      pydantic-core==2.16.3 \
      annotated-types==0.6.0 \
      typing-extensions==4.10.0 \
      dirty-equals==0.7.1

USER 65532:65532
WORKDIR /workspace

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/workspace

ENTRYPOINT []
