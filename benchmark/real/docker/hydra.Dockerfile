FROM m.daocloud.io/docker.io/library/python:3.8-slim

ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

# Hydra 1.0/1.1 tasks share this small runtime. The source tree is mounted at
# execution time, so no benchmark answer or repository snapshot enters the
# reusable image.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends default-jre-headless \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --no-cache-dir --index-url "${PIP_INDEX_URL}" \
      pytest==6.2.5 \
      omegaconf==2.1.2 \
      antlr4-python3-runtime==4.8 \
      importlib-resources==5.13.0 \
      packaging==21.3

USER 65532:65532
WORKDIR /workspace

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/workspace

ENTRYPOINT []
