"""Owner-facing optional critical-error criteria and policy-cost report summaries."""

import json
import streamlit as st
from decision_workbench.data import options


def example_policy(questions):
    name = next(iter(questions))
    labels = options(questions[name])
    definition = {
        "critical_labels": {
            labels[-1]: {
                "max_false_negative_rate": 0.2,
                "max_false_positive_rate": 0.2,
                "min_positive_rows": 30,
                "min_negative_rows": 30,
                "rate_bound": "wilson_95_upper",
            }
        }
    }
    if len(labels) <= 10:
        definition.update(
            cost_unit="error_cost_units",
            costs={
                actual: {pred: int(actual != pred) for pred in labels}
                for actual in labels
            },
            max_mean_cost=0.15,
        )
    return {"format": "domain-error-policy-v1", "questions": {name: definition}}


def render_criteria(
    existing, questions, disabled, mode="Guided tables", key="domain-errors"
):
    selected = existing.get("error_policy")
    enabled = st.checkbox(
        "Add domain error-cost and critical-label gates",
        value=selected is not None,
        disabled=disabled,
        key=key + "-enabled",
    )
    st.caption(
        "Define costly mistakes before training. The example is a starting format; choose costs, critical labels and data requirements for your domain. All outcomes are scored, including model abstentions. Provider charges and manual-review costs are separate."
    )
    if mode == "Advanced JSON":
        body = st.text_area(
            "Error-cost and critical-label policy (JSON)",
            json.dumps(selected or example_policy(questions), indent=2),
            disabled=disabled,
            height=240,
            key=key + "-json",
        )
    else:
        from .error_policy_editor import editor_rows

        tables = editor_rows(questions, selected)
        st.caption(
            "Use these exact outcome IDs in the tables. For yes/no, true means yes and false means no. Rating IDs start at 0 in the declared order."
        )
        st.dataframe(
            [
                {
                    "Question": name,
                    "Outcome ID": label,
                    "Meaning": (
                        question.get("criteria", {}).get(label)
                        if question["type"] == "choice"
                        else (
                            question["criteria"][int(label)]
                            if question["type"] == "score"
                            else "Yes" if label == "true" else "No"
                        )
                    ),
                }
                for name, question in questions.items()
                for label in options(question)
            ],
            hide_index=True,
        )
        st.write("Question cost rules")
        st.caption(
            "Tick Include costs for each question you want to evaluate. Default error cost applies to every incorrect outcome; correct predictions always cost zero. Choose a unit and maximum mean loss. Unchecked rows are omitted."
        )
        rules = st.data_editor(
            tables["costs"],
            hide_index=True,
            num_rows="fixed",
            disabled=True if disabled else ["question"],
            key=key + "-costs",
            column_config={
                "question": st.column_config.TextColumn("Question"),
                "include_costs": st.column_config.CheckboxColumn(
                    "Include costs", required=True
                ),
                "cost_unit": st.column_config.TextColumn("Cost unit", required=True),
                "default_error_cost": st.column_config.NumberColumn(
                    "Default error cost", min_value=0.0, max_value=1e12, required=True
                ),
                "max_mean_cost": st.column_config.NumberColumn(
                    "Maximum mean cost", min_value=0.0, max_value=1e12, required=True
                ),
            },
        )
        question_column = st.column_config.SelectboxColumn(
            "Question", options=list(questions), required=True
        )
        st.write("Specific error costs (optional)")
        st.caption(
            "Add exceptions to the default cost, such as true=false and predicted=true for an incorrect approval. Use different allowed outcome IDs. Each pair can appear once; remove the row to restore its default cost. Overrides for unchecked cost rules are omitted."
        )
        overrides = st.data_editor(
            tables["overrides"],
            hide_index=True,
            num_rows="dynamic",
            disabled=disabled,
            key=key + "-overrides",
            column_config={
                "question": question_column,
                "actual": st.column_config.TextColumn("True outcome ID", required=True),
                "predicted": st.column_config.TextColumn(
                    "Predicted outcome ID", required=True
                ),
                "cost": st.column_config.NumberColumn(
                    "Error cost",
                    min_value=0.0,
                    max_value=1e12,
                    required=True,
                    default=0.0,
                ),
            },
        )
        st.write("Critical outcome limits (optional)")
        st.caption(
            "Add each important outcome once. A false negative misses that outcome; a false positive selects it incorrectly. Choose both error-rate limits and both test-support minimums. Wilson upper bounds account for sample uncertainty. Blank placeholder rows are ignored; all completed rows require both IDs."
        )
        critical = st.data_editor(
            tables["critical"],
            hide_index=True,
            num_rows="dynamic",
            disabled=disabled,
            key=key + "-critical",
            column_config={
                "question": question_column,
                "outcome": st.column_config.TextColumn(
                    "Critical outcome ID", required=True
                ),
                "max_false_negative_rate": st.column_config.NumberColumn(
                    "Maximum missed-outcome rate",
                    min_value=0.0,
                    max_value=1.0,
                    required=True,
                    default=0.2,
                ),
                "max_false_positive_rate": st.column_config.NumberColumn(
                    "Maximum incorrect-selection rate",
                    min_value=0.0,
                    max_value=1.0,
                    required=True,
                    default=0.2,
                ),
                "min_positive_rows": st.column_config.NumberColumn(
                    "Minimum positive test rows",
                    min_value=1,
                    max_value=20000,
                    step=1,
                    required=True,
                    default=30,
                ),
                "min_negative_rows": st.column_config.NumberColumn(
                    "Minimum negative test rows",
                    min_value=1,
                    max_value=20000,
                    step=1,
                    required=True,
                    default=30,
                ),
                "rate_bound": st.column_config.SelectboxColumn(
                    "Rate bound",
                    options=["wilson_95_upper", "observed"],
                    required=True,
                    default="wilson_95_upper",
                ),
            },
        )
        body = {"costs": rules, "overrides": overrides, "critical": critical}
    return body if enabled else None


def render_report(report):
    evaluation = report.get("error_evaluation")
    if evaluation is None:
        return
    with st.expander("Domain error costs and critical outcomes"):
        st.caption(
            "Forced argmax across all examples. Cost values are owner-defined loss estimates, not billing or observed operational losses. Critical intervals are nominal per-label intervals."
        )
        costs, critical = [], []
        for role, questions in evaluation["splits"].items():
            for name, result in questions.items():
                if "mean_policy_cost" in result:
                    costs.append(
                        {
                            "split": role,
                            "question": name,
                            "mean cost": result["mean_policy_cost"],
                            "unit": result["cost_unit"],
                            "soft labels": result["soft_gold_rows"],
                        }
                    )
                for label, item in result.get("critical_labels", {}).items():
                    critical.append(
                        {
                            "split": role,
                            "question": name,
                            "critical outcome": label,
                            "positive rows": item["positive_rows"],
                            "negative rows": item["negative_rows"],
                            "false negatives": item["false_negative"],
                            "false positives": item["false_positive"],
                            "FN rate": item["false_negative_rate"],
                            "FP rate": item["false_positive_rate"],
                            "soft labels": result["soft_gold_rows"],
                        }
                    )
        if costs:
            st.dataframe(costs, hide_index=True)
        if critical:
            st.dataframe(critical, hide_index=True)
        st.json(evaluation, expanded=False)
