"""The Ask page (D123): one question, answered by `make ask`'s pipeline, shown once.

Nothing is written, cached or logged: no st.cache, nothing put in session state, and the chat
input's value exists only in the run that submitted it. Everything from the chat or the model is
rendered as plain text, never as Markdown or HTML (a message could otherwise make the browser
fetch a remote image).
"""

import logging

import streamlit as st

from gcwrapped.dashboard import ask
from gcwrapped.labeling.terminal import safe_text

log = logging.getLogger("gcwrapped.dashboard")

st.title("Ask")
st.caption(
    "Answers come from a model on this computer, using only messages their senders consented to. "
    "This page shows chat text on screen and stores nothing."
)

question = st.chat_input("Ask a question about the chat", max_chars=ask.MAX_QUESTION_CHARS)
if question and question.strip():
    st.text(f"Question: {safe_text(question.strip())}")
    try:
        with st.spinner("Searching the chat and asking the local model..."):
            lines = ask.answer_lines_for(question.strip())
    except Exception as exc:  # settings, database or model server; never show its text
        log.error("ask unavailable (%s)", type(exc).__name__)
        st.error(ask.UNAVAILABLE)
        st.stop()
    with st.container(border=True):
        for line in lines:
            st.text(line)
