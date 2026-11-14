
import json
import re
import math
from pathlib import Path
from collections import Counter

import faiss
import numpy as np
from sentence_transformers import SentenceTransformer


INDEX_FILE = Path("data/processed/paper_index.faiss")
METADATA_FILE = Path("data/processed/metadata.json")
MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"

# Reciprocal Rank Fusion constant
RRF_K = 60


class PaperRetriever:

    def __init__(self):
        if not INDEX_FILE.exists():
            raise FileNotFoundError(
                "FAISS index missing. Run embeddings.py first."
            )

        if not METADATA_FILE.exists():
            raise FileNotFoundError(
                "Metadata missing. Run embeddings.py first."
            )

        self.model = SentenceTransformer(MODEL_NAME)
        self.index = faiss.read_index(str(INDEX_FILE))

        with open(METADATA_FILE, "r", encoding="utf-8") as file:
            self.metadata = json.load(file)

        if len(self.metadata) != self.index.ntotal:
            raise ValueError(
                f"Metadata/index mismatch: {len(self.metadata)} chunks "
                f"but {self.index.ntotal} vectors."
            )

        # Build the BM25 corpus once, not for every query.
        self.tokenized_corpus = [
            self.tokenize(item["text"])
            for item in self.metadata
        ]

        self.doc_lengths = [
            len(tokens) for tokens in self.tokenized_corpus
        ]

        self.avg_doc_length = (
            sum(self.doc_lengths) / len(self.doc_lengths)
            if self.doc_lengths else 0
        )

        self.doc_frequency = Counter()

        for tokens in self.tokenized_corpus:
            self.doc_frequency.update(set(tokens))

        self.total_docs = len(self.tokenized_corpus)

        print(f"Loaded {self.total_docs} chunks.")
        print("FAISS + BM25 indexes ready.")

        # Load the cross-encoder only when R5 is requested.
        self.reranker = None
        self.reranker_model_name = "cross-encoder/ms-marco-MiniLM-L-6-v2"

    # --------------------------------------------------
    # Text processing
    # --------------------------------------------------

    @staticmethod
    def tokenize(text):
        return re.findall(r"[a-z0-9]+", text.lower())

    # --------------------------------------------------
    # Query embedding
    # --------------------------------------------------

    def encode_query(self, query):
        vector = self.model.encode(
            [query],
            normalize_embeddings=True,
            convert_to_numpy=True
        )

        return vector.astype("float32")

    # --------------------------------------------------
    # Convert metadata index to result
    # --------------------------------------------------

    def make_result(self, idx, score):
        chunk = self.metadata[idx]

        return {
            "paper": chunk["paper"],
            "page": chunk["page"],
            "text": chunk["text"],
            "score": float(score),
            "chunk_id": int(idx)
        }

    # --------------------------------------------------
    # R1: Raw FAISS semantic search
    # --------------------------------------------------

    def raw_search(self, query, top_k=5):
        query_vector = self.encode_query(query)

        scores, indices = self.index.search(
            query_vector,
            min(top_k, self.index.ntotal)
        )

        results = []

        for score, idx in zip(scores[0], indices[0]):
            if idx == -1:
                continue

            results.append(
                self.make_result(int(idx), float(score))
            )

        return results

    # --------------------------------------------------
    # Reference / bibliography filtering
    # --------------------------------------------------

    @staticmethod
    def is_reference_chunk(text):
        lines = [
            line.strip()
            for line in text.splitlines()
            if line.strip()
        ]

        if not lines:
            return True

        heading_pattern = re.compile(
            r"^(references|bibliography|works cited)$",
            re.IGNORECASE
        )

        if any(heading_pattern.match(line) for line in lines[:5]):
            return True

        citation_pattern = re.compile(
            r"^\s*\[\d+\]\s+|"
            r"^\s*\(\d{4}\)\s+|"
            r"\barxiv:\d{4}\.\d{4,5}\b",
            re.IGNORECASE
        )

        citation_count = sum(
            bool(citation_pattern.search(line))
            for line in lines
        )

        if len(lines) >= 3:
            if citation_count / len(lines) >= 0.5:
                return True

        return False

    # --------------------------------------------------
    # R2: FAISS + reference filtering
    # --------------------------------------------------

    def filtered_search(
        self,
        query,
        top_k=5,
        candidate_k=30
    ):
        candidates = self.raw_search(
            query,
            top_k=min(candidate_k, self.index.ntotal)
        )

        candidates = [
            item for item in candidates
            if not self.is_reference_chunk(item["text"])
        ]

        return candidates[:top_k]

    # --------------------------------------------------
    # BM25 keyword retrieval
    # --------------------------------------------------

    def bm25_search(
        self,
        query,
        top_k=10,
        k1=1.5,
        b=0.75
    ):
        query_tokens = self.tokenize(query)

        if not query_tokens:
            return []

        scores = []

        for idx, document_tokens in enumerate(
            self.tokenized_corpus
        ):
            term_counts = Counter(document_tokens)
            doc_length = self.doc_lengths[idx]

            score = 0.0

            for term in query_tokens:
                frequency = term_counts.get(term, 0)

                if frequency == 0:
                    continue

                document_frequency = self.doc_frequency.get(
                    term, 0
                )

                idf = math.log(
                    1 + (
                        self.total_docs - document_frequency + 0.5
                    ) / (document_frequency + 0.5)
                )

                denominator = frequency + k1 * (
                    1 - b + b * (
                        doc_length / max(self.avg_doc_length, 1)
                    )
                )

                term_score = (
                    idf
                    * frequency
                    * (k1 + 1)
                    / denominator
                )

                score += term_score

            if score > 0:
                scores.append((idx, score))

        scores.sort(key=lambda x: x[1], reverse=True)

        results = []

        for idx, score in scores[:top_k]:
            results.append(
                self.make_result(idx, score)
            )

        return results

    # --------------------------------------------------
    # R4: Hybrid retrieval using Reciprocal Rank Fusion
    # --------------------------------------------------

    def hybrid_search(
        self,
        query,
        top_k=5,
        candidate_k=50,
        rrf_k=RRF_K
    ):
        """
        Combines:
        1. FAISS semantic ranking
        2. BM25 keyword ranking

        RRF score = sum(1 / (rrf_k + rank))

        A chunk ranked highly by both retrievers
        receives a stronger combined ranking.
        """

        semantic_results = self.raw_search(
            query,
            top_k=min(candidate_k, self.index.ntotal)
        )

        keyword_results = self.bm25_search(
            query,
            top_k=min(candidate_k, self.total_docs)
        )

        fused = {}

        # Add semantic ranks.
        for rank, item in enumerate(semantic_results, start=1):
            chunk_id = item["chunk_id"]

            fused[chunk_id] = {
                **item,
                "semantic_rank": rank,
                "bm25_rank": None,
                "rrf_score": 1.0 / (rrf_k + rank)
            }

        # Add BM25 ranks.
        for rank, item in enumerate(keyword_results, start=1):
            chunk_id = item["chunk_id"]

            if chunk_id not in fused:
                fused[chunk_id] = {
                    **item,
                    "semantic_rank": None,
                    "bm25_rank": rank,
                    "rrf_score": 0.0
                }

            else:
                fused[chunk_id]["bm25_rank"] = rank

            fused[chunk_id]["rrf_score"] += (
                1.0 / (rrf_k + rank)
            )

        # Remove bibliography/reference chunks.
        results = [
            item for item in fused.values()
            if not self.is_reference_chunk(item["text"])
        ]

        results.sort(
            key=lambda item: item["rrf_score"],
            reverse=True
        )

        # Confidence gating must use one comparable scale, including BM25-only hits.
        query_vector = self.encode_query(query)[0]
        for item in results:
            item["score"] = self.cosine_similarity(query_vector, self.index.reconstruct(item["chunk_id"]))
            item["rrf_score"] = float(item["rrf_score"])

        return results[:top_k]

    # --------------------------------------------------
    # MMR: Maximal Marginal Relevance
    # --------------------------------------------------

    @staticmethod
    def cosine_similarity(a, b):
        denominator = (
            np.linalg.norm(a) * np.linalg.norm(b)
        )

        if denominator == 0:
            return 0.0

        return float(np.dot(a, b) / denominator)

    def mmr_search(
        self,
        query,
        top_k=5,
        candidate_k=30,
        lambda_value=0.75
    ):
        if not 0 <= lambda_value <= 1:
            raise ValueError(
                "lambda_value must be between 0 and 1"
            )

        query_vector = self.encode_query(query)[0]

        candidates = self.raw_search(
            query,
            top_k=min(candidate_k, self.index.ntotal)
        )

        candidates = [
            item for item in candidates
            if not self.is_reference_chunk(item["text"])
        ]

        if not candidates:
            return []

        vectors = [
            np.asarray(
                self.index.reconstruct(item["chunk_id"]),
                dtype="float32"
            )
            for item in candidates
        ]

        selected = []
        selected_scores = []
        
        remaining = list(range(len(candidates)))

        while remaining and len(selected) < top_k:
            best_index = None
            best_score = float("-inf")

            for candidate_index in remaining:
                relevance = self.cosine_similarity(
                    query_vector,
                    vectors[candidate_index]
                )

                if not selected:
                    diversity_penalty = 0.0
                else:
                    diversity_penalty = max(
                        self.cosine_similarity(
                            vectors[candidate_index],
                            vectors[selected_index]
                        )
                        for selected_index in selected
                    )

                mmr_score = (
                    lambda_value * relevance
                    - (1 - lambda_value) * diversity_penalty
                )

                if mmr_score > best_score:
                    best_score = mmr_score
                    best_index = candidate_index

            selected.append(best_index)
            remaining.remove(best_index)

            selected_scores.append(best_score)

        results = []

        for index, mmr_score in zip(selected, selected_scores):
            item = candidates[index].copy()

            item["mmr_score"] = float(mmr_score)
            item["score"] = self.cosine_similarity(
                query_vector,
                vectors[index]
            )

            results.append(item)

        return results

    # --------------------------------------------------
    # R5: R4 hybrid retrieval + Cross-Encoder reranking
    # --------------------------------------------------

    def load_reranker(self):
        """Load the cross-encoder once, only when R5 is used."""
        if self.reranker is None:
            try:
                from sentence_transformers import CrossEncoder
            except ImportError as exc:
                raise ImportError(
                    "R5 requires CrossEncoder support. Run: "
                    "pip install -U sentence-transformers"
                ) from exc

            print(f"Loading reranker: {self.reranker_model_name}")
            self.reranker = CrossEncoder(self.reranker_model_name)
        return self.reranker

    def reranked_search(
        self,
        query,
        top_k=8,
        candidate_k=50,
        rrf_k=RRF_K,
        batch_size=16,
    ):
        """
        Retrieve candidate_k passages with R4, score query-passage pairs
        with a Cross-Encoder, and return the best top_k passages.
        """
        if top_k <= 0 or candidate_k <= 0:
            raise ValueError("top_k and candidate_k must be greater than 0")

        candidates = self.hybrid_search(
            query,
            top_k=min(candidate_k, self.total_docs),
            candidate_k=min(candidate_k, self.total_docs),
            rrf_k=rrf_k,
        )
        if not candidates:
            return []

        reranker = self.load_reranker()
        pairs = [(query, item["text"]) for item in candidates]
        scores = reranker.predict(
            pairs,
            batch_size=batch_size,
            show_progress_bar=False,
        )

        for item, score in zip(candidates, scores):
            item["rerank_score"] = float(score)

        candidates.sort(key=lambda item: item["rerank_score"], reverse=True)
        return candidates[:top_k]

    # --------------------------------------------------
    # Public search interface
    # --------------------------------------------------

    def search(
        self,
        query,
        top_k=5,
        setting="R1",
        candidate_k=50,
        lambda_value=0.75,
        rrf_k=RRF_K,
        **kwargs
    ):

        if setting == "R1":
            return self.raw_search(query, top_k)

        elif setting == "R2":
            return self.filtered_search(
                query,
                top_k=top_k,
                candidate_k=candidate_k
            )

        elif setting == "R3":
            return self.mmr_search(
                query,
                top_k=top_k,
                candidate_k=candidate_k,
                lambda_value=lambda_value
            )

        elif setting == "R4":
            return self.hybrid_search(
                query,
                top_k=top_k,
                candidate_k=candidate_k,
                rrf_k=rrf_k
            )

        elif setting == "R5":
            return self.reranked_search(
                query,
                top_k=top_k,
                candidate_k=candidate_k,
                rrf_k=rrf_k,
            )

        else:
            raise ValueError(
                "Unknown setting. Choose R1, R2, R3, R4, or R5."
            )


if __name__ == "__main__":

    retriever = PaperRetriever()

    question = input(
        "\nAsk a question about the papers: "
    )

    for setting in ["R1", "R2", "R3", "R4", "R5"]:

        print(f"\n{'=' * 20} {setting} {'=' * 20}")

        results = retriever.search(
            question,
            top_k=5,
            setting=setting
        )

        for rank, result in enumerate(results, start=1):
            print(
                f"\n{rank}. {result['paper']} "
                f"| Page {result['page']} "
                f"| Score: {result['score']:.4f}"
            )

            if "rrf_score" in result:
                print(
                    "RRF score:",
                    round(result["rrf_score"], 5)
                )

            if "rerank_score" in result:
                print(
                    "Cross-Encoder score:",
                    round(result["rerank_score"], 5)
                )

            print(result["text"][:400])
