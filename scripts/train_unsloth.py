"""Maintenance entry point; platform jobs invoke the shared training adapter."""
from decision_workbench.training.unsloth_runner import main

if __name__ == '__main__':
    main()
