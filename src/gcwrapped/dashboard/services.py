"""`gcw dashboard-services`, the first half of `make dashboard-up` (D123): start the db, llm and
embed services the dashboard needs, the llm one serving the promoted model, as `make ask` does,
and create or update the Ask page's read-only role (D127, `ask_role`). The Makefile then builds and
starts the dashboard itself, with compose output on screen.

The guards are the other commands', the other-stack and served-model checks of D126 included; a
failure prints `dashboard-up: failed at <step> (<exception type>)`.
"""

from gcwrapped.answers import commands as answers
from gcwrapped.answers import report
from gcwrapped.dashboard import ask_role
from gcwrapped.docker import require_no_other_stack
from gcwrapped.retrieval import commands as retrieval_commands
from gcwrapped.tagging import commands as tagging
from gcwrapped.tagging import evaluation as tag_evaluation
from gcwrapped.tagging import model
from gcwrapped.tagging.llamacpp import check_local_url


def run(services: answers.Services | None = None) -> int:
    services = services or answers.Services()
    step = answers.STEP_DATABASE
    try:
        url, _ = tagging.check_database(synthetic=False)
        step = answers.STEP_EMBED_URL
        check_local_url(retrieval_commands.embed_url())
        step = answers.STEP_LLM_URL
        check_local_url(tagging.llm_url())
        step = answers.STEP_DOCKER
        require_no_other_stack()
        step = answers.STEP_ASK_PASSWORD
        password = ask_role.require_password()
        step = answers.STEP_DOCKER
        if services.db is not None:
            services.db()
        step = answers.STEP_ASK_ROLE
        ask_role.ensure(url, password)  # fails on a warehouse that is not built yet
        step = answers.STEP_MODEL
        model_id = tag_evaluation.promoted_model(url, model.MODEL_ID)
        step = answers.STEP_DOCKER
        if services.llm is not None:
            services.llm(model_id)
        if services.embed is not None:
            services.embed()
        step = answers.STEP_MODEL
        answers.require_served_models(model_id)
    except Exception as exc:  # type name only: driver and OS messages can echo values
        print(report.failure_line("dashboard-up", step, exc))
        return 1
    return 0
