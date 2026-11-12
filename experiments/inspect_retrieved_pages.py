
from retrieval.retriever import PaperRetriever
import pymupdf
from pathlib import Path

PAPER_DIR = Path("data/papers")

retriever = PaperRetriever()

questions = [
    "How does self-attention work in the Transformer?",
    "How does the Transformer decoder prevent access to future tokens?",
    "How does Moshi support real-time dialogue?",
    "What is the role of the speech-text model in Moshi?",
    "What is dynamic chunking?",
    "How does hierarchical sequence modeling work?"

]

for question in questions:

    print("\n" + "=" * 90)
    print("QUESTION:", question)

    results = retriever.search(
        question,
        top_k=10,
        setting="R1"
    )

    for i, result in enumerate(results, start=1):

        print(f"\nCandidate {i}")
        print("Paper:", result["paper"])
        print("PDF page:", result["page"])
        print("Similarity:", round(result["score"], 4))
        print("\nExtracted passage:")
        print(result["text"][:1200])

        pdf_path = PAPER_DIR / result["paper"]

        if pdf_path.exists():
            doc = pymupdf.open(pdf_path)

            # Metadata page numbers are normally 1-based.
            page_index = int(result["page"]) - 1

            if 0 <= page_index < len(doc):
                print("\nPDF page text preview:")
                print(doc[page_index].get_text()[:1500])

            doc.close()

        print("-" * 80)
