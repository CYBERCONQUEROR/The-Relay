
import os
from dotenv import load_dotenv
from groq import Groq

load_dotenv()

client = Groq(
    api_key=os.getenv("GROQ_API_KEY")
)

model = os.getenv("GROQ_MODEL")

try:
    print("Testing Groq API...")
    print("Model:", model)

    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "user",
                "content": "Reply with: The Relay API is working."
            }
        ],
        temperature=0,
        max_completion_tokens=100
    )

    print("\nResponse:")
    print(response.choices[0].message.content)

    print("\nToken usage:")
    print("Input:", response.usage.prompt_tokens)
    print("Output:", response.usage.completion_tokens)

    print("\nGroq connection successful!")

except Exception as error:
    print("\nGroq API error:")
    print(type(error).__name__, error)
