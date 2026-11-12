import argparse
import json
import re
import sys
from pathlib import Path

# Project root: supports both `python retrieval/chunking.py` and module execution.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from retrieval.pdf_loader import extract_papers


def create_chunks(documents, chunk_size=500, overlap=80):
    if chunk_size <= 0:
        raise ValueError("chunk_size must be greater than 0")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap must be >= 0 and smaller than chunk_size")

    chunks = []
    for document in documents:
        text = document["text"]
        words = list(re.finditer(r"\S+", text))
        start = 0
        chunk_number = 0

        while start < len(words):
            end = min(start + chunk_size, len(words))
            chunk_text = text[words[start].start():words[end - 1].end()]
            chunks.append({
                "paper": document["paper"],
                "page": document["page"],
                "chunk_id": chunk_number,
                "text": chunk_text,
                "chunk_size_words": chunk_size,
                "overlap_words": overlap,
            })
            chunk_number += 1
            if end == len(words):
                break
            start = end - overlap

    return chunks


def save_chunks(chunks, output_path):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as file:
        for chunk in chunks:
            file.write(json.dumps(chunk, ensure_ascii=False) + "\n")
    print(f"Saved {len(chunks)} chunks to {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Create page-aware paper chunks.")
    parser.add_argument("--chunk-size", type=int, default=500)
    parser.add_argument("--overlap", type=int, default=80)
    parser.add_argument("--output", type=Path, default=Path("data/processed/chunks.jsonl"))
    args = parser.parse_args()

    documents = extract_papers()
    chunks = create_chunks(documents, args.chunk_size, args.overlap)
    save_chunks(chunks, args.output)

    print(f"Chunk size: {args.chunk_size} words | Overlap: {args.overlap} words")
    if chunks:
        print("\nSample chunk:")
        print(chunks[0]["text"][:500])


if __name__ == "__main__":
    main()
