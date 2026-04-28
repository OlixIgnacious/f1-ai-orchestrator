"""
Phase 4 RAG ingestion: parses FIA PDF regulations with Document AI Layout Parser,
generates embeddings with Vertex AI text-embedding-005, and inserts into AlloyDB
f1_regulations table.

Run AFTER:
  1. Document AI Layout Parser processor created in GCP Console
  2. FIA PDFs uploaded to GCS bucket (gs://YOUR_PROJECT-f1-regulations/pdfs/)
  3. DOCUMENT_AI_PROCESSOR_ID and GCS_REGULATIONS_BUCKET set in .env

Usage:
    python scripts/ingest_regulations.py [--dry-run] [--year 2025]

Naming convention for PDFs in GCS:
    fia_{year}_f1_{sporting|technical|financial}_regulations.pdf
    e.g. fia_2025_f1_sporting_regulations.pdf
"""

import os
import re
import sys
import argparse
import psycopg2
from dotenv import load_dotenv

load_dotenv()

PROJECT_ID          = os.getenv("GOOGLE_CLOUD_PROJECT")
LOCATION            = "us"           # Document AI multi-region endpoint
PROCESSOR_ID        = os.getenv("DOCUMENT_AI_PROCESSOR_ID")
REGULATIONS_BUCKET  = os.getenv("GCS_REGULATIONS_BUCKET")
VERTEX_LOCATION     = os.getenv("VERTEX_AI_LOCATION", "us-central1")


def get_conn():
    return psycopg2.connect(
        host=os.getenv("ALLOYDB_HOST", "127.0.0.1"),
        port=os.getenv("ALLOYDB_PORT", "5433"),
        database=os.getenv("ALLOYDB_DATABASE", "f1db"),
        user=os.getenv("ALLOYDB_USER", "postgres"),
        password=os.getenv("ALLOYDB_PASSWORD")
    )


def get_embed_model():
    import vertexai
    from vertexai.language_models import TextEmbeddingModel
    vertexai.init(project=PROJECT_ID, location=VERTEX_LOCATION)
    return TextEmbeddingModel.from_pretrained("text-embedding-005")


def embed_text(model, text: str) -> list[float]:
    """Generate a 768-dim embedding. Caps input at 3000 chars to stay within token limit."""
    result = model.get_embeddings([text[:3000]])
    return result[0].values


def parse_pdf_to_chunks(gcs_uri: str) -> list[dict]:
    """
    Download PDF from GCS and extract text locally using pypdf.
    Chunks by article heading — no Document AI needed, no page limit.
    Returns a list of {article_number, article_title, content} dicts.
    """
    import io
    import pypdf
    from google.cloud import storage

    # Download PDF bytes from GCS
    storage_client = storage.Client()
    bucket_name    = gcs_uri.replace("gs://", "").split("/")[0]
    blob_path      = "/".join(gcs_uri.replace("gs://", "").split("/")[1:])
    blob           = storage_client.bucket(bucket_name).blob(blob_path)
    pdf_bytes      = blob.download_as_bytes()

    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    print(f"  {len(reader.pages)} pages", end=" ", flush=True)

    # Extract all text and join pages
    full_text = "\n".join(page.extract_text() or "" for page in reader.pages)

    # Strategy 1: split on "ARTICLE X:" or "XX. TITLE" headings
    #   Old format: "39.", "39.1 TITLE" at start of line
    #   2026 format: "ARTICLE A1: TITLE", "ARTICLE B2: TITLE"
    heading = re.compile(
        r'(?:^ARTICLE\s+[A-Z]?\d+(?:\.\d+)*\s*[:\s]|^\d{1,3}(?:\.\d+)*\s+[A-Z])',
        re.MULTILINE
    )
    splits = [m.start() for m in heading.finditer(full_text)]

    if len(splits) >= 5:
        # Good article structure found — chunk by article
        chunks = []
        for i, start in enumerate(splits):
            end     = splits[i + 1] if i + 1 < len(splits) else len(full_text)
            segment = full_text[start:end].strip()
            if len(segment) < 40:
                continue
            # Extract article number and title from first line
            first_line = segment.splitlines()[0].strip()
            num_match  = re.match(r'(?:ARTICLE\s+)?([A-Z]?\d+(?:\.\d+)*)\s*[:\s]\s*(.*)', first_line)
            art_num    = num_match.group(1) if num_match else str(i)
            art_title  = (num_match.group(2) or "")[:100] if num_match else ""
            chunks.append({
                "article_number": art_num,
                "article_title":  art_title.strip(),
                "content":        segment[:4000]
            })
        return chunks

    # Strategy 2: paragraph-based chunking (for dense single-line PDFs)
    paragraphs = re.split(r'\n{2,}|\.\s{2,}', full_text)
    chunks     = []
    buffer     = ""
    art_idx    = 0
    for para in paragraphs:
        para = para.strip()
        if not para or len(para) < 30:
            continue
        buffer += para + " "
        if len(buffer) >= 1500:
            chunks.append({
                "article_number": str(art_idx),
                "article_title":  "",
                "content":        buffer.strip()[:4000]
            })
            buffer  = ""
            art_idx += 1
    if buffer.strip():
        chunks.append({"article_number": str(art_idx), "article_title": "", "content": buffer.strip()[:4000]})
    return chunks


def ingest_pdf(conn, embed_model, gcs_uri: str,
               year: int, reg_type: str, dry_run: bool):
    print(f"\nProcessing: {gcs_uri}")
    chunks = parse_pdf_to_chunks(gcs_uri)
    print(f"  → {len(chunks)} chunks extracted")

    if dry_run:
        for c in chunks[:3]:
            preview = c['content'][:80].replace('\n', ' ')
            print(f"  [DRY RUN] Art.{c['article_number']} — {preview}...")
        return len(chunks)

    cur      = conn.cursor()
    inserted = 0
    for i, chunk in enumerate(chunks):
        content = chunk["content"].strip()
        if len(content) < 50:
            continue
        try:
            embedding = embed_text(embed_model, content)
            cur.execute("""
                INSERT INTO f1_regulations
                (year, reg_type, article_number, article_title,
                 content, embedding, source_url)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
            """, (
                year, reg_type,
                chunk["article_number"], chunk["article_title"],
                content, embedding, gcs_uri
            ))
            inserted += 1
            if (i + 1) % 20 == 0:
                conn.commit()
                print(f"  ... {i+1}/{len(chunks)} chunks committed")
        except Exception as e:
            print(f"  ⚠ chunk {i} error: {e}")

    conn.commit()
    cur.close()
    print(f"  ✓ {inserted} chunks inserted for {year} {reg_type}")
    return inserted


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Parse PDFs and show chunk previews without inserting")
    parser.add_argument("--year", type=int, default=None,
                        help="Process only a specific year e.g. --year 2025")
    args = parser.parse_args()

    # Validate required env vars
    missing = [v for v in ["DOCUMENT_AI_PROCESSOR_ID", "GCS_REGULATIONS_BUCKET",
                            "GOOGLE_CLOUD_PROJECT"]
               if not os.getenv(v)]
    if missing:
        print(f"Missing env vars: {', '.join(missing)}")
        print("Set these in .env before running ingestion.")
        sys.exit(1)

    from google.cloud import storage
    storage_client = storage.Client()
    bucket         = storage_client.bucket(REGULATIONS_BUCKET)

    conn        = None if args.dry_run else get_conn()
    embed_model = None if args.dry_run else get_embed_model()
    total       = 0

    for blob in bucket.list_blobs(prefix="pdfs/"):
        if not blob.name.endswith(".pdf"):
            continue

        # Matches both old format:  fia_2025_f1_sporting_regulations.pdf
        # and new section format:  fia_2026_f1_general/operational_regulations.pdf
        match = re.search(
            r'fia_(\d{4})_f1_(sporting|technical|financial|general|operational)', blob.name
        )
        if not match:
            print(f"Skipping {blob.name} — unrecognised filename format")
            continue

        year     = int(match.group(1))
        reg_type = match.group(2).capitalize()  # Sporting, Technical, Financial, General, Operational

        if args.year and year != args.year:
            continue

        gcs_uri = f"gs://{REGULATIONS_BUCKET}/{blob.name}"
        total  += ingest_pdf(conn, embed_model, gcs_uri,
                             year, reg_type, args.dry_run)

    if conn:
        conn.close()

    print(f"\nIngestion complete — {total} total chunks processed")
    if not args.dry_run:
        print("Next: run 'python scripts/run_migrations.py --indexes' to create ScaNN indexes")


if __name__ == "__main__":
    main()
