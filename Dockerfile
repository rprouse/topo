# Build environment: official QGIS LTR image, pinned by digest so GDAL, PROJ,
# Python and QGIS versions never drift. QGIS 3.44.15 / GDAL 3.8.4 / Python 3.12.
# Keep QGIS desktop on the same version (3.44.x) for styling and labels.
FROM qgis/qgis:3.44.15-noble@sha256:907fb2cc44adc47a8eba64316c345f0675c226d11cddea47b16ef03097caea7e

ENV DEBIAN_FRONTEND=noninteractive \
    QT_QPA_PLATFORM=offscreen \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    PATH=/opt/venv/bin:$PATH

# Fonts used by the map styles (docs/symbol-spec.md). Install the same family
# (Noto Sans) on the desktop where the QGIS project is edited, or labels move.
# Ghostscript converts the proofs to greyscale (and later CMYK). It is not
# version-pinned: Ubuntu replaces its security builds, so a pin would break
# fresh builds. Its version is recorded in build/manifest.json instead.
RUN apt-get update \
 && apt-get install -y --no-install-recommends fonts-noto-core=20201225-2 ghostscript \
 && rm -rf /var/lib/apt/lists/* \
 && fc-cache -f

# venv with system site packages so PyQGIS and the GDAL bindings stay visible.
COPY requirements.txt /tmp/requirements.txt
RUN python3 -m venv --system-site-packages /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir --disable-pip-version-check -r /tmp/requirements.txt

WORKDIR /work
ENTRYPOINT ["python", "-m", "pipeline"]
CMD ["--help"]
