"""Show Vega applicability limits separately from ordinary decision accuracy."""

from decision_workbench.platform.vega_training import release_gates, validate_policy


def applicability_rows(report, limits=None):
    if limits is not None:
        validate_policy(limits)
    rows = []
    for name, value in report["applicability"]["test"].items():
        status = "Policy unavailable"
        if limits is not None:
            subset = {
                **report,
                "test": {name: report["test"][name]},
                "applicability": {"test": {name: value}},
            }
            try:
                release_gates(subset, {"vega": limits}, {name: {}})
                status = "Pass"
            except ValueError:
                status = "Fail"
        rows.append(
            {
                "Question": name,
                "Applicable test examples": value["positive"]["count"],
                "Positive gate open rate": value["positive"]["open_rate"],
                "Positive accepted fraction": value["positive_accepted_fraction"],
                "Minimum positive acceptance": (
                    limits["min_positive_accepted_fraction"] if limits else None
                ),
                "Inapplicable test examples": value["negative"]["count"],
                "Negative gate false-open rate": value["negative"]["error_rate"],
                "Maximum negative false-open rate": (
                    limits["max_negative_false_open_rate"] if limits else None
                ),
                "Minimum negative test examples": (
                    limits["min_negative_test_rows"] if limits else None
                ),
                "Applicability gates": status,
            }
        )
    return rows


def render_applicability(report, model, policy):
    import streamlit as st

    configuration = model["payload"]["configuration"]
    selected = policy["policy"]
    bound = (
        selected is not None
        and selected["id"] == configuration.get("release_policy_id")
        and selected["payload"]["sha256"] == configuration.get("release_policy_sha256")
    )
    limits = selected["payload"]["criteria"].get("vega") if bound else None
    st.subheader("Applicability and model abstention")
    st.dataframe(
        applicability_rows(report, limits),
        hide_index=True,
        key="vega-applicability-" + model["id"],
    )
    st.caption(
        "Positive accepted fraction includes gate, unbound and conformal abstention. Negative false-open rate measures only the applicability gate. Rates use the separate counts shown for each question. These applicability gates supplement accuracy, F1, calibration and class coverage; the server checks all frozen criteria at activation."
    )
    if limits is None:
        st.warning(
            "The displayed owner policy does not bind this model's Vega run. Applicability eligibility is unavailable; inspect its frozen provenance before release."
        )
    elif any(
        row["Applicability gates"] != "Pass"
        for row in applicability_rows(report, limits)
    ):
        st.warning(
            "At least one question fails the frozen applicability criteria. Owner activation will refuse this version."
        )
