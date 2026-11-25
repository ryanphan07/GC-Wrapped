"""The Wrapped page (D123): pick a member, see their card. Aggregates only, read-only role.

Not cached: the mart is a view, so a revoked consent removes the card on the next reload.
Member names are chat data and are never rendered as Markdown.
"""

import logging

import streamlit as st

from gcwrapped.dashboard import queries
from gcwrapped.dashboard.wrapped import CARD_NOTE, NO_PROFILES, card_lines, member_labels
from gcwrapped.labeling.terminal import safe_text

log = logging.getLogger("gcwrapped.dashboard")

_UNAVAILABLE = (
    "The profiles could not be loaded. Check the database settings, that the database is "
    "running and that dbt has been built, then reload. Details are in the server logs."
)

st.title("Wrapped")
try:
    profiles = queries.wrapped_profiles().to_dict("records")
except Exception as exc:  # settings validation or connection failure; never show its text
    log.error("wrapped profiles unavailable (%s)", type(exc).__name__)
    st.error(_UNAVAILABLE)
    st.stop()

if not profiles:
    st.info(NO_PROFILES)
    st.stop()

labels = member_labels(profiles)
chosen = st.selectbox("Member", range(len(profiles)), format_func=labels.__getitem__)
profile = profiles[chosen]
with st.container(border=True):
    st.text(safe_text(str(profile["member_name"])))
    st.text("\n".join(card_lines(profile)))
st.caption(CARD_NOTE)
