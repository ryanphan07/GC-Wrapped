"""The Ask page's one operation (D123): a question through `make ask`'s pipeline.

The same guards, the same pipeline and so the same consent check and citation path: the database
must be one of ALLOWED_DATABASES, both model URLs local, and the model, retrieval method and prompt
variant the promoted ones. It reads messages, so it uses the pipeline's credentials rather than the
dashboard's reader role, in a session set read-only (D124). It writes nothing, logs nothing and
keeps nothing: the answer exists in the returned lines only.
"""

import psycopg

from gcwrapped.answers import commands as answers
from gcwrapped.answers import comparison
from gcwrapped.answers.pipeline import Answer, AnswerPipeline
from gcwrapped.retrieval import commands as retrieval_commands
from gcwrapped.retrieval import evaluation as retrieval
from gcwrapped.retrieval.search import Retriever
from gcwrapped.tagging import commands as tagging
from gcwrapped.tagging import evaluation as tag_evaluation
from gcwrapped.tagging import model
from gcwrapped.tagging.llamacpp import check_local_url

MAX_QUESTION_CHARS = 500
UNAVAILABLE = (
    "The question could not be answered. Check that the database and the local models are "
    "running (make dashboard-up), then ask again. Details are in the server logs."
)


def answer_question(question: str) -> Answer:
    url, _ = tagging.check_database(synthetic=False)
    check_local_url(retrieval_commands.embed_url())
    check_local_url(tagging.llm_url())
    model_id = tag_evaluation.promoted_model(url, model.MODEL_ID)
    answers.require_served_models(model_id)  # every question: a server can change under the page
    chat = answers.chat_model(model_id)
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("SET default_transaction_read_only = on")  # this page writes nothing
        retriever = Retriever(conn, retrieval_commands.local_embedder())
        pipeline = AnswerPipeline(
            conn,
            retriever,
            chat,
            retrieval.default_method(conn),
            variant=comparison.default_variant(conn),
        )
        return pipeline.answer(question)


def answer_lines_for(question: str) -> list[str]:
    """What the page shows, as `make ask` prints it: the answer and its cited messages, or "not in
    the chat". After an abstention the page shows no messages (D123)."""
    return answers.answer_lines(answer_question(question), show_context=False)
