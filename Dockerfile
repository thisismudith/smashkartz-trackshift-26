# The model service. The frontend is a static export and is NOT in this image.
#
# Two things here are load-bearing rather than incidental:
#
#   requirements.lock.txt, never requirements.txt. The pass models are pickles,
#   and a pickle unpickles against the library versions it was written with. An
#   unpinned resolve installs a newer LightGBM or scikit-learn and the artifact
#   either fails to load -- which at least announces itself -- or loads into a
#   subtly different object and serves numbers nobody can reproduce.
#
#   libgomp1, the GNU OpenMP runtime. LightGBM links against it and is the
#   rank-1 family at all three checkpoints (artifacts/models/pass/v2/*/metrics.json,
#   selection_rank 1), so without it every /pass/predict falls through to the
#   synthetic stub. The slim base does not carry it.
FROM python:3.13-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Dependencies before source: they change far less often, so the layer is reused
# across every rebuild that only touches code.
COPY requirements.lock.txt .
RUN pip install --no-cache-dir -r requirements.lock.txt

COPY src/ ./src/
COPY config/ ./config/
COPY artifacts/ ./artifacts/

ENV PYTHONPATH=/app/src PYTHONUNBUFFERED=1

EXPOSE 8080

CMD ["uvicorn","trackshift.serve.app:app","--host","0.0.0.0","--port","8080"]
