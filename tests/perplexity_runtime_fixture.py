"""Real framed runtime transport with a fictional model; no model packages."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from decision_workbench.training import runtime
from decision_workbench.data import options


class Engine:
    def predict(self, state, questions):
        result = {}
        for name, question in questions.items():
            selected = (
                ("continue" if state["ready"] else "review")
                if question["type"] == "choice"
                else ("true" if state["ready"] else "false")
            )
            result[name] = {
                "probabilities": {
                    key: 0.8 if key == selected else 0.2 for key in options(question)
                }
            }
        return result


def load(backend, checkpoint):
    if backend != "perplexity-native":
        raise ValueError("Fixture backend differs")
    return Engine()


runtime.load_engine = load
if __name__ == "__main__":
    runtime.main()
