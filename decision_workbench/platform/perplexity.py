"""Text/JSON subset of Perplexity's documented Decisions API contract."""

# This describes a reviewed interface, never an attestation of remote weights.
CONTRACT = "perplexity-decisions-2026-10-18"
MODELS = {
    "pplx-decider-v1.2": "pplx-decider-v1.2",
    "pplx-decider-flash-v1.2": "pplx-decider-flash-v1.2",
    "pplx-decider-latest": "pplx-decider-v1.2",
    "pplx-decider-flash-latest": "pplx-decider-flash-v1.2",
    "perplexity/pplx-decider-latest": "pplx-decider-v1.2",
    "perplexity/pplx-decider-flash-latest": "pplx-decider-flash-v1.2",
    "pplx-decider-v1.1-27b": "pplx-decider-v1.2",
    "pplx-decider-v1-27b": "pplx-decider-v1.2",
}


def validate_profile(profile):
    if profile["model"] not in MODELS or profile["response_model"] != profile["model"]:
        raise ValueError(
            "Use a documented Perplexity Decisions model; response_model must echo model"
        )


def binding(profile):
    validate_profile(profile)
    return {
        "contract": CONTRACT,
        "model_identity": "request-echo-only",
        "documented_route": MODELS[profile["model"]],
        "question_context": "joint-request",
    }


def validate_request(state, questions):
    # The common schema already limits questions to 64, choice to 2..255,
    # and score to 2..10: a deliberate subset of the provider's interface.
    for question in questions.values():
        if set(question) - {"type", "instructions", "criteria"}:
            raise ValueError("Unsupported Perplexity question fields")
        if question["type"] == "noul" and "criteria" in question:
            criteria = question["criteria"]
            if (
                not isinstance(criteria, dict)
                or set(criteria) != {"false", "true"}
                or any(
                    not isinstance(v, str) or not v.strip() for v in criteria.values()
                )
            ):
                raise ValueError(
                    "Perplexity noul criteria need false/true descriptions"
                )
    pending = [state]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            if value.get("type") == "image_url":
                raise ValueError(
                    "This Perplexity adapter supports text/JSON; image parts need a separate media workflow"
                )
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
