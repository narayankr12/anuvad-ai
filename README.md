# English -> Hindi PDF Book Translator

Streamlit app: upload an English PDF, translate chapter by chapter with Claude,
download Hindi PDFs. If one API key runs out, paste another and click Continue.

## Run locally (VS Code terminal)
    python -m venv venv
    source venv/bin/activate          # Windows: venv\Scripts\activate
    pip install -r requirements.txt
    streamlit run app.py

## Deploy free (Streamlit Community Cloud)
1. Push this folder to a new GitHub repo (includes fonts/ folder).
2. Go to https://share.streamlit.io -> Create app -> pick repo, branch main, file app.py.
3. Deploy. Paste your API keys in the sidebar each session (do not store them in the repo).

Note: on Streamlit Cloud the .cache folder is wiped on restart, so download each
chapter PDF as soon as it finishes.
