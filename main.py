
import json
import time

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from pipeline.orchestrator import run_pipeline
from groq import (
    RateLimitError,
    APITimeoutError,
    APIConnectionError,
    APIStatusError
)

app = FastAPI(
    title="The Relay",
    description="Research Paper Question Answering System",
    version="1.0.0"
)


class QuestionRequest(BaseModel):

    question: str = Field(
        min_length=1,
        max_length=2000
    )


def safe_pipeline(question):

    try:
        return run_pipeline(question)

    except RateLimitError as error:

        print("\n========== GROQ RATE LIMIT ==========")

        print("Error:", str(error))
        print("Status:", error.status_code)
        print("Response headers:", dict(error.response.headers))

        print("=====================================\n")

        raise HTTPException(
            status_code=429,
            detail=str(error)
        )

    except APITimeoutError:
        raise HTTPException(
            status_code=504,
            detail="Groq request timed out."
        )

    except APIConnectionError:
        raise HTTPException(
            status_code=503,
            detail="Could not connect to Groq."
        )

    except APIStatusError as error:

        print("Groq API status:", error.status_code)

        raise HTTPException(
            status_code=502,
            detail="Groq returned an API error."
        )


@app.get("/")
def home():

    return {
        "project": "The Relay",
        "status": "running",
        "version": "1.0.0"
    }


@app.get("/health")
def health():

    return {
        "status": "healthy",
        "retrieval": "FAISS initialized"
    }


@app.post("/ask")
def ask_question(request: QuestionRequest):

    try:

        result = safe_pipeline(request.question)

        if result["status"] == "rate_limited":
            raise HTTPException(
                status_code=429,
                detail="Groq rate limit reached."
            )

        if result["status"] == "timeout":
            raise HTTPException(
                status_code=504,
                detail="Model request timed out."
            )

        return result

    except HTTPException:
        raise

    except Exception as error:

        print("Pipeline error:", repr(error))

        raise HTTPException(
            status_code=500,
            detail="An internal pipeline error occurred."
        )


@app.post("/ask/stream")
def ask_stream(request: QuestionRequest):

    # Run pipeline before starting the HTTP stream.
    # This allows proper HTTP error handling.

    result = safe_pipeline(request.question)

    def generate_events():

        import json

        answer = result.get("answer", "")

        words = answer.split()

        for i in range(0, len(words), 5):

            chunk = " ".join(words[i:i + 5])

            yield (
                "data: "
                + json.dumps({"chunk": chunk})
                + "\n\n"
            )

        yield (
            "data: "
            + json.dumps({
                "done": True,
                "status": result["status"],
                "sources": result.get("sources", []),
                "metrics": result.get("metrics", {})
            })
            + "\n\n"
        )

    return StreamingResponse(
        generate_events(),
        media_type="text/event-stream"
    )
