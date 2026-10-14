"""Canonical acceptance criteria shared by policy editing and successor creation."""

import copy
import math


def normalize_criteria(
    questions,
    min_accuracy,
    min_macro_f1,
    max_ece,
    min_test_rows,
    min_training_examples_per_class=5,
    min_calibration_examples_per_class=1,
    vega=None,
    error_policy=None,
):
    if (
        any(
            not math.isfinite(v) or not 0 <= v <= 1
            for v in (min_accuracy, min_macro_f1, max_ece)
        )
        or type(min_test_rows) is not int
        or min_test_rows < 10
    ):
        raise ValueError(
            "Acceptance metrics must be in [0,1], with at least 10 real test rows"
        )
    for value in (min_training_examples_per_class, min_calibration_examples_per_class):
        if type(value) is not int or not 0 <= value <= 100000:
            raise ValueError(
                "Data class-count thresholds must be integers in [0,100000]"
            )
    criteria = {
        "min_training_examples_per_class": min_training_examples_per_class,
        "min_calibration_examples_per_class": min_calibration_examples_per_class,
        "min_accuracy": min_accuracy,
        "min_macro_f1": min_macro_f1,
        "max_ece": max_ece,
        "min_test_rows": min_test_rows,
    }
    if vega is not None:
        from .vega_training import validate_policy

        criteria["vega"] = copy.deepcopy(validate_policy(vega))
    if error_policy is not None:
        from decision_workbench.training.error_policy import normalize_policy

        criteria["error_policy"] = normalize_policy(error_policy, questions)
    return criteria
