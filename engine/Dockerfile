# The conversion engine. Every converter binary is baked in, so the same image
# runs identically on a laptop and on a VM with nothing installed on the host.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/tmp

RUN apt-get update && apt-get install -y --no-install-recommends \
      # media
      ffmpeg \
      # office documents
      libreoffice-writer libreoffice-calc libreoffice-impress \
      # markup
      pandoc \
      # images and PDF tooling
      imagemagick ghostscript \
      # SVG: ImageMagick delegates SVG rendering to this binary, and without it
      # every SVG conversion fails at the delegate call.
      librsvg2-bin \
      # raster -> SVG tracing
      potrace \
      # archives
      p7zip-full bzip2 xz-utils \
      # OCR with the language packs the picker offers
      tesseract-ocr \
      tesseract-ocr-eng tesseract-ocr-chi-sim tesseract-ocr-chi-tra \
      tesseract-ocr-msa tesseract-ocr-fra tesseract-ocr-deu tesseract-ocr-spa \
      tesseract-ocr-por tesseract-ocr-ita tesseract-ocr-nld tesseract-ocr-jpn \
      tesseract-ocr-kor tesseract-ocr-ara tesseract-ocr-hin tesseract-ocr-rus \
      tesseract-ocr-tha tesseract-ocr-vie \
      # WeasyPrint's rendering stack
      libpango-1.0-0 libpangoft2-1.0-0 libcairo2 libgdk-pixbuf-2.0-0 \
      libffi8 shared-mime-info \
      # fonts, so generated PDFs are not full of tofu
      fonts-dejavu fonts-liberation fonts-noto-core fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

# Debian's ImageMagick policy blocks PDF and PS by default. The engine never
# routes PDFs through ImageMagick (PyMuPDF does that), so leave the policy alone.

WORKDIR /engine
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

ENV CONVERTOR_WORKSPACE=/data
RUN mkdir -p /data/inbox /data/output /data/tmp

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/health')"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
