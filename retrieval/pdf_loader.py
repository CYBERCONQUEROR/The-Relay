
import pymupdf
from pathlib import Path


PAPER_DIR = Path("data/papers")


def extract_papers():

    documents = []

    for pdf_path in PAPER_DIR.glob("*.pdf"):

        print(f"\nProcessing: {pdf_path.name}")

        pdf = pymupdf.open(pdf_path)

        for page_number, page in enumerate(pdf, start=1):

            text = page.get_text("text").strip()

            if not text:
                continue

            documents.append({
                "paper": pdf_path.name,
                "page": page_number,
                "text": text
            })

        pdf.close()

    print(f"\nTotal extracted pages: {len(documents)}")

    return documents


if __name__ == "__main__":

    documents = extract_papers()

    print("\nSample extracted content:")

    for doc in documents[:3]:
        print("\nPaper:", doc["paper"])
        print("Page:", doc["page"])
        print(doc["text"][:500])
