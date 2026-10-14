# Tools and integrations

[All guides](README.md) · [Project overview](../README.md)

Start with manual examples or a CSV/XLSX upload. Add integrations when you need a private synthesis endpoint, model registry, experiment tracking or controlled access to enterprise records.

| Need | Optional route | Boundary |
|---|---|---|
| Training | Unsloth environment/notebooks | Separate GPU dependencies |
| Encoder experiments | Laya native tools | Pin source/checkpoint |
| Private synthesis | Local chat endpoint or approved enterprise API | Review teacher/data usage terms |
| Distribution | Existing private model registry or Hugging Face | Bundle head/processor and pin revision |
| Tracking | Existing MLflow/W&B equivalent | Avoid raw-record upload by default |
| Agent composition | LangChain/LangGraph if already used | Evaluate complete workflows |
| Source control | GitHub repository and pull requests | Keep data and secrets private |

Follow the upstream setup guides for [Unsloth](https://unsloth.ai/docs/basics/train-your-own-decision-model-with-unsloth), [Laya](https://github.com/NandhaKishorM/laya) or [LangChain](https://www.langchain.com/blog/building-a-harness-with-jev) when selecting those integrations.



An authorized Drive/SharePoint or policy-system connector can later retrieve selected enterprise examples. It is optional; a file upload is enough to start. A connector does not replace provenance or permission checks.
