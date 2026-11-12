
def analyze_query(question):

    if not isinstance(question, str):
        return {
            "valid": False,
            "reason": "Question must be text."
        }

    question = question.strip()

    if not question:
        return {
            "valid": False,
            "reason": "Question cannot be empty."
        }

    if len(question) > 2000:
        return {
            "valid": False,
            "reason": "Question is too long. Maximum 2000 characters."
        }

    return {
        "valid": True,
        "question": question,
        "intent": "research_question"
    }
