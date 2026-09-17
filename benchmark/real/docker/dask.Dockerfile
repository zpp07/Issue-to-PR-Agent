FROM m.daocloud.io/docker.io/library/python:3.8-slim

ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

# Install the release matching the selected snapshot only to obtain its
# dependency graph. The mounted exact commit overrides dask's Python source.
RUN python -m pip install --no-cache-dir --index-url "${PIP_INDEX_URL}" \
      dask[array]==2021.7.0 \
      numpy==1.21.6 \
      pytest==6.2.5

USER 65532:65532
WORKDIR /workspace

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/workspace

ENTRYPOINT []
