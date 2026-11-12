
from pipeline.orchestrator import run_pipeline


questions = [
    "How does the Transformer use self-attention?",

    "What is multi-head attention?",

    "What is the role of positional encoding?",

    "What does the Moshi paper describe about "
    "real-time speech-text dialogue?",

    "What does the Dynamic Chunking paper say "
    "about hierarchical sequence modeling?",

    "What is the population of India?"
]


for i, question in enumerate(questions, start=1):

    print("\n" + "=" * 70)

    print(f"QUESTION {i}: {question}")

    result = run_pipeline(question)

    print("\nSTATUS:", result["status"])

    print("\nANSWER:")
    print(result["answer"])

    print("\nSOURCES:")
    print(result.get("sources", []))

    print("\nMETRICS:")
    print(result.get("metrics", {}))

    print("=" * 70)



