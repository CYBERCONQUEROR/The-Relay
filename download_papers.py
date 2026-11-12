
import urllib.request
from pathlib import Path

PAPER_DIR = Path("data/papers")
PAPER_DIR.mkdir(parents=True, exist_ok=True)

PAPERS = {
    "attention_is_all_you_need_v7.pdf":
        "https://arxiv.org/pdf/1706.03762v7",

    "moshi_v1.pdf":
        "https://arxiv.org/pdf/2410.00037v1",

    "dynamic_chunking_v2.pdf":
        "https://arxiv.org/pdf/2507.07955v2",
}


def download_papers():
    for filename, url in PAPERS.items():

        destination = PAPER_DIR / filename

        if destination.exists():
            print(f"Already downloaded: {filename}")
            continue

        print(f"Downloading: {filename}")

        try:
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "TheRelayAssignment/1.0"}
            )

            with urllib.request.urlopen(
                request, timeout=60
            ) as response:

                content = response.read()

            if not content.startswith(b"%PDF"):
                raise ValueError("Downloaded file is not a PDF")

            destination.write_bytes(content)

            print(f"Successfully saved: {destination}")

        except Exception as error:
            print(f"Failed: {filename} - {error}")


if __name__ == "__main__":
    download_papers()
