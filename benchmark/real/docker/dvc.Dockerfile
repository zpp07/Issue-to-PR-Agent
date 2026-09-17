FROM m.daocloud.io/docker.io/library/python:3.8-slim

ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

RUN apt-get update \
    && apt-get install --yes --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# The selected snapshots are DVC 1.1.0 and 1.1.7. Installing the later patch
# release supplies their shared runtime dependencies; PYTHONPATH points at the
# mounted exact snapshot, so the released package's source is never tested.
RUN python -m pip install --no-cache-dir --index-url "${PIP_INDEX_URL}" \
      Cython==0.29.36 wheel==0.41.3 \
    && python -m pip install --no-cache-dir --index-url "${PIP_INDEX_URL}" \
      dvc==1.1.7 pytest==6.2.5 pytest-mock==1.11.2

# DVC 1.1 imports funcy.py3, which was removed by later funcy releases even
# though the historical requirement only specified a lower bound.
RUN python -m pip install --no-cache-dir --index-url "${PIP_INDEX_URL}" funcy==1.14

USER 65532:65532
WORKDIR /workspace

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/workspace \
    DVC_TEST=true \
    DVC_IGNORE_ISATTY=true

ENTRYPOINT []
