"""Streamlit entry point: the three pages in views/ (D123).

Activity and Wrapped read marts through the read-only role. Ask reads chat messages, the way
`make ask` does, and shows them on this machine's screen.
"""

import streamlit as st

st.set_page_config(page_title="GC Wrapped", layout="wide")
st.navigation(
    [
        st.Page("views/activity.py", title="Activity", default=True),
        st.Page("views/wrapped.py", title="Wrapped", url_path="wrapped"),
        st.Page("views/ask.py", title="Ask", url_path="ask"),
    ]
).run()
