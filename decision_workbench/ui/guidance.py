"""Short, contextual reader guidance linked to the complete browser manual."""

import streamlit as st

GUIDE = (
    "https://github.com/BatuhanYilmaz26/decision-model-workbench/blob/main/"
    "docs/app-guide.md"
)

HINTS = {
    "project": (
        "create-a-project",
        "Define what the model may decide and the evidence it can use. A project starts empty; adding questions does not generate examples or train a model.",
    ),
    "data": (
        "add-and-review-examples",
        "Download this project's CSV template, enter examples or upload a file. Missing labels are allowed at intake. Review and approve records before reserving data; hypothetical examples cannot become real evaluation gold.",
    ),
    "prepare": (
        "reserve-data-and-generate-candidates",
        "Reserve independent real evaluation records before generating extra training data. Select one current revision per collection. When adding synthetic candidates, preserve the original snapshot's holdouts.",
    ),
    "criteria": (
        "set-acceptance-criteria",
        "Set quality, coverage and domain error limits before the first model study. The first training or hosted evaluation freezes them. Use a successor project for a changed policy or criteria.",
    ),
    "training": (
        "train-or-evaluate-a-model",
        "Choose one path: train weights on this computer, evaluate an existing hosted decision API, or prepare a cloud GPU study. Start with the CPU baseline to check your data workflow; it needs no downloaded model weights.",
    ),
    "hosted": (
        "evaluate-a-hosted-decision-api",
        "Use an operator-configured provider profile and your own provider key. Evaluation sends approved heldout evidence to that service and fits local calibration; it does not fine-tune or download the provider's weights.",
    ),
    "cloud": (
        "train-on-a-cloud-gpu",
        "Preparing or downloading a study does not start paid compute. Managed runs require a configured profile plus separate data and compute approval. Stop and observe the provider job explicitly when needed.",
    ),
    "jobs": (
        "follow-jobs-and-operational-alerts",
        "Use Refresh workspace to see updated progress and results. Closing this browser does not cancel worker jobs. A failed or timed-out external call may still have run upstream; inspect its saved outcome before retrying.",
    ),
    "health": (
        "follow-jobs-and-operational-alerts",
        "A fresh worker heartbeat means the worker is available, not that a GPU or provider is ready. Acknowledging an alert records that it was seen; it does not repair its cause.",
    ),
    "models": (
        "inspect-results-release-and-roll-back",
        "A completed study registers an inactive version. Inspect heldout quality, class coverage and export details, then ask an owner to activate an eligible version. Select an earlier eligible version to roll back.",
    ),
    "decisions": (
        "make-decisions-and-export-audit-history",
        "Predictions use the active release, not whichever model is selected elsewhere. Inspect every answer's abstained flag and use your fallback when the model declines or fails; increasing the cutoff does not retrain it.",
    ),
    "applicability": (
        "prepare-vega-applicability-data",
        "This additional workflow is for Vega. Review real cases where a question should not apply, pair their reservation with a positive snapshot and enable the owner's Vega acceptance criteria before training.",
    ),
    "generation": (
        "generate-and-review-training-candidates",
        "A separate generative teacher proposes candidates. Preview the exact policy and training seeds before approval. Review the saved candidate dataset in Data and review, then extend the original reservation; generation alone does not update a trained model.",
    ),
    "schema": (
        "optional-teacher-suggested-questions",
        "The teacher drafts a schema from the approved domain brief only. Review its assumptions and outcomes before creating the project. This action does not label data or train a decision model.",
    ),
    "reservations": (
        "choose-source-groups-or-date-cutoffs",
        "Inspect the eligible groups first. Assign all four roles or enter time boundaries, preview the result and approve it before reserving. Synthetic and previously exposed families cannot be moved into heldouts.",
    ),
    "semantic": (
        "review-semantic-relatives",
        "Similarity proposes related records; it is not a duplicate verdict. Review each proposed relationship and give a reason. A required audit must have no pending pairs before reservation.",
    ),
    "taxonomy": (
        "discover-or-change-categories",
        "Use a manual proposal or an approved TypeLLM request, then review every category as add, alias or reject. Owner confirmation creates a successor project; it does not relabel or replace the current project.",
    ),
    "successor": (
        "change-an-existing-policy",
        "A successor gets a new project and endpoint. It starts without datasets, models or an active release. Review its copied policy and any selected criteria, then prepare fresh data for that version.",
    ),
    "audit": (
        "capture-and-download-audit-windows",
        "Capture freezes an event window. Prepare its verified download, then download the file and retain the manifest checksum separately. Audit exports contain event identities and notes; they are not workspace backups.",
    ),
}


def render_hint(section):
    anchor, text = HINTS[section]
    st.caption(text + f" [Step-by-step guide]({GUIDE}#{anchor}).")


def render_help_navigation():
    with st.sidebar:
        st.markdown(f"[Complete app user guide]({GUIDE})")
        st.markdown(f"[First project walkthrough]({GUIDE}#first-project-walkthrough)")
        st.markdown(
            f"[Why is a control unavailable?]({GUIDE}#when-a-control-is-unavailable)"
        )


def render_role_help(role):
    descriptions = {
        "admin": "Owner access: configure acceptance, approve external work, review data and release models.",
        "editor": "Editor access: create projects, add data and queue permitted preparation/training jobs. An owner handles external approvals and release; authorized reviewers approve labels.",
        "reviewer": "Reviewer access: review assigned examples and permitted relationships/categories. An owner or editor prepares data and model jobs.",
        "inference": "Inference access: submit evidence to an active release. Ask an owner if the project has no released model.",
    }
    st.caption(descriptions[role])
