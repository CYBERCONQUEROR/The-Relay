
import json
from pathlib import Path

import faiss
import numpy as np
from sentence_transformers import SentenceTransformer


CHUNKS_FILE = Path("data/processed/chunks.jsonl")
INDEX_FILE = Path("data/processed/paper_index.faiss")
METADATA_FILE = Path("data/processed/metadata.json")

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"


def load_chunks():

    with open(CHUNKS_FILE, "r", encoding="utf-8") as file:
        return [json.loads(line) for line in file]


def build_index():

    chunks = load_chunks()

    if not chunks:
        raise RuntimeError(
            "chunks.jsonl is empty. Run chunking.py first to generate chunks."
        )

    texts = [chunk["text"] for chunk in chunks]

    print("Loading embedding model...")

    model = SentenceTransformer(MODEL_NAME)

    print("Generating embeddings...")

    embeddings = model.encode(
        texts,
        convert_to_numpy=True,
        normalize_embeddings=True,
        show_progress_bar=True
    )

    embeddings = embeddings.astype("float32")

    dimension = embeddings.shape[1]

    index = faiss.IndexFlatIP(dimension)

    index.add(embeddings)

    faiss.write_index(index, str(INDEX_FILE))

    with open(METADATA_FILE, "w", encoding="utf-8") as file:
        json.dump(chunks, file, ensure_ascii=False, indent=2)

    print("\nIndex successfully created!")
    print("Total vectors:", index.ntotal)
    print("Vector dimension:", dimension)


if __name__ == "__main__":
    build_index()
