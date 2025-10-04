#!/usr/bin/env python3
"""
app.py - Legal Bot (Web Version)
- Uses Flask to serve a web-based chat interface.
- Manages conversation state between requests.
- All core logic for model interaction and data parsing is preserved.
"""

# ---------- Suppress noisy gRPC/Abseil warnings ----------
import os
os.environ["GRPC_VERBOSITY"] = "NONE"
os.environ["GLOG_minloglevel"] = "2"
os.environ["ABSL_LOGGING_MIN_LOG_LEVEL"] = "2"

import re
import sys
import google.generativeai as genai
from dotenv import load_dotenv
from flask import Flask, render_template, request, jsonify

# ---------- Flask App Initialization ----------
app = Flask(__name__)

# ---------- Config ----------
# Using the model list you provided.
PREFERRED_MODELS = [
    "models/gemini-2.5-pro",
    "models/gemini-pro-latest",
    "models/gemini-2.5-flash",
    "models/gemini-flash-latest"
]
CRIME_DATA_PATH = "data/crimes_explained.txt"
FALLBACK_CRIME_DATA_PATH = "data/crimes.txt"
# ----------------------------


def choose_model(preferred=None):
    """Finds the best available model from a preferred list."""
    try:
        available = [m.name for m in genai.list_models() if "generateContent" in m.supported_generation_methods]
    except Exception as e:
        print(f"Error listing models: {e}")
        return None

    if not available:
        return None
    if preferred:
        for p in preferred:
            if p in available:
                return p
    return available[0]

def init_api_from_env():
    """Loads API key from .env and configures the genai client."""
    load_dotenv()
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("Error: GEMINI_API_KEY not found. Put it in .env or environment.")
        return False
    try:
        genai.configure(api_key=api_key)
        return True
    except Exception as e:
        print(f"Error configuring API client: {e}")
        return False

def load_crime_data(primary=CRIME_DATA_PATH, fallback=FALLBACK_CRIME_DATA_PATH):
    """Loads crime data text from a primary or fallback file path."""
    for path in (primary, fallback):
        if not path:
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read(), path
        except FileNotFoundError:
            continue
        except Exception as e:
            print(f"Error reading '{path}': {e}")
            continue
    return None, None

# ---------- Normalization and Parsing Functions ----------

def normalize_article_key_raw(label: str, number: str) -> str:
    """Creates a standardized key (e.g., 'IPC 420') from parts."""
    lab = (label or "").strip().upper()
    lab = re.sub(r'^(SEC\.?|SEC)$', 'SECTION', lab, flags=re.IGNORECASE)
    lab = re.sub(r'^(IT\s*ACT|ITACT)$', 'IT ACT', lab, flags=re.IGNORECASE)
    lab = re.sub(r'^(CRPC)$', 'CRPC', lab, flags=re.IGNORECASE)
    lab = re.sub(r'^(IPC)$', 'IPC', lab, flags=re.IGNORECASE)
    lab = re.sub(r'^(ACT)$', 'ACT', lab, flags=re.IGNORECASE)
    num = (number or "").strip().upper()
    return f"{lab} {num}".strip()

def build_article_summary_map(crime_data_text: str):
    """Parses crime data text into a dictionary of {article_key: summary}."""
    article_map = {}
    pattern = re.compile(
        r"\b(?:(IT\s*Act)\s+)?(IPC|CrPC|Crpc|Section|Sec\.?|Act|ACT)\s*\.?\s*([0-9]{1,4}[A-Z]?)\s*[:\-—]\s*(.+)",
        flags=re.IGNORECASE
    )
    for line in crime_data_text.splitlines():
        m = pattern.search(line)
        if not m:
            continue
        prefix, core_label, number, summary = m.group(1), m.group(2), m.group(3), m.group(4).strip()
        label = "IT ACT" if prefix else ("SECTION" if core_label.lower().startswith("sec") else core_label)
        key = normalize_article_key_raw(label, number)
        article_map[key] = " ".join(summary.splitlines()).strip()
    return article_map

def extract_article_tokens_from_text(text: str):
    """Extracts all unique legal article tokens (e.g., 'IPC 379') from a string."""
    tokens = []
    pattern = re.compile(
        r"\b(?:(IT\s*Act)\s+)?(IPC|CrPC|Crpc|Section|Sec\.?|Act|ACT)\s*\.?\s*([0-9]{1,4}[A-Z]?)\b",
        flags=re.IGNORECASE
    )
    for m in pattern.finditer(text):
        prefix, core, num = m.group(1), m.group(2), m.group(3)
        label = "IT ACT" if prefix else ("SECTION" if core.lower().startswith("sec") else core)
        tokens.append(normalize_article_key_raw(label, num))
    if not tokens: # Fallback for bare numbers
        for n in re.findall(r"\b([0-9]{2,4}[A-Z]?)\b", text):
            tokens.append(normalize_article_key_raw("SECTION", n))
    return list(dict.fromkeys(tokens)) # unique but preserve order

def find_local_summary(article_token: str, article_map: dict):
    """Finds a summary from the local DB, with fuzzy matching for the number."""
    if article_token in article_map:
        return [(article_token, article_map[article_token])]
    results = []
    mnum = re.search(r"([0-9]{1,4}[A-Z]?)$", article_token)
    if not mnum: return []
    num = mnum.group(1)
    for k, v in article_map.items():
        if re.search(rf"\b{re.escape(num)}\b", k):
            results.append((k, v))
    return results

# ---------- Model Interaction Functions ----------

def ask_model_for_articles(model, user_scenario, crime_data_text):
    """Asks the model to identify relevant articles from a list for a given scenario."""
    prompt = f'Your ONLY task is to be a data retriever.\n1. Read the user\'s scenario: "{user_scenario}".\n2. From the Data block below, decide which legal article codes are relevant.\n3. Output ONLY a comma-separated list of article codes (e.g., "IPC 379, CrPC 154"), with no other text.\n\nData:\n---\n{crime_data_text}\n---'
    resp = model.generate_content(prompt)
    model_text = getattr(resp, "text", "")
    return model_text, extract_article_tokens_from_text(model_text)

def ask_model_generate_short_summary(model, article_token):
    """Asks the model for a concise, one-sentence summary of a legal article."""
    prompt = f'You are a concise assistant. Produce a one-sentence summary (8-25 words) describing what the legal article "{article_token}" typically covers under Indian law.\nOutput ONLY the single-sentence summary (no extra commentary).'
    resp = model.generate_content(prompt)
    return " ".join(getattr(resp, "text", "").splitlines()).strip()

def make_prompt_template(crime_data: str) -> str:
    """Creates the main prompt template for the initial user query."""
    return f"""You are 'legal_bot', a helpful assistant for legal information in India. Your task is to analyze a user's scenario based ONLY on the provided crime data. First, provide a clear, step-by-step list of actions the user should take (label that section "**Necessary Steps:**"). Then, ask the user: "Would you like to see the relevant legal articles for this case?" Do NOT provide the articles until the user replies 'yes'. Here is the crime data you MUST use:\n---\n{crime_data}\n---\nIf the scenario is not covered in the provided data, respond exactly with: "I'm sorry, but that scenario is not covered in my knowledge base. It is advisable to consult with a legal professional for guidance." Be polite and empathetic.\n\nUser's Scenario: {{user_scenario}}"""

# ---------- Global Variables for the App ----------
# These are loaded once when the server starts to avoid reloading on each request
model = None
crime_data_text = ""
article_summary_map = {}
prompt_template = ""

# ---------- Core Web App Logic (Flask Routes) ----------

@app.route('/')
def index():
    """Serves the main HTML page."""
    return render_template('index.html')

@app.route('/chat', methods=['POST'])
def chat():
    """Handles chat messages from the user with proper error handling."""
    data = request.json
    user_message = data.get('message')
    state = data.get('state', {})
    
    bot_response = ""
    
    # This try...except block is crucial to prevent the server from crashing.
    try:
        # This part checks for confirmation keywords. It's a necessary step
        # for a two-part conversation and not for the user's initial problem.
        if state.get('awaitingArticleConfirmation') and user_message.lower() in ["yes", "y", "ok", "show me"]:
            user_scenario = state.get('scenario')
            if user_scenario:
                _, article_tokens = ask_model_for_articles(model, user_scenario, crime_data_text)
                
                if not article_tokens:
                    bot_response = "I couldn't identify relevant legal article codes automatically."
                else:
                    output_items = []
                    for tok in article_tokens:
                        local_matches = find_local_summary(tok, article_summary_map)
                        if local_matches:
                            for k, summ in local_matches:
                                output_items.append(f"* **{k}** — {summ}")
                        else:
                            gen_sum = ask_model_generate_short_summary(model, tok)
                            output_items.append(f"* **{tok}** — {gen_sum} *(model-generated)*")
                    
                    final_output = list(dict.fromkeys(output_items))
                    bot_response = "Here are the relevant articles with short summaries:\n\n" + "\n".join(final_output)

            # Reset state after showing articles
            state['awaitingArticleConfirmation'] = False
            state['scenario'] = None

        # This handles the user's initial problem description.
        else:
            state['scenario'] = user_message
            full_prompt = prompt_template.format(user_scenario=user_message)
            response = model.generate_content(full_prompt)
            text = getattr(response, "text", "Sorry, an error occurred.")
            bot_response = text
            
            if "relevant legal articles" in text.lower():
                state['awaitingArticleConfirmation'] = True
            else:
                state['awaitingArticleConfirmation'] = False
                state['scenario'] = None

    except Exception as e:
        # If any error happens above, this will catch it, print it to your
        # terminal, and send a clean message to the user.
        print(f"An error occurred in /chat route: {e}")
        bot_response = "I'm sorry, I encountered an error while processing your request. Please try again."
        state['awaitingArticleConfirmation'] = False
        state['scenario'] = None

    return jsonify({'response_text': bot_response, 'state': state})

def start_app():
    """Initializes all necessary components for the application."""
    global model, crime_data_text, article_summary_map, prompt_template
    
    print("Initializing Legal Bot Server...")
    
    if not init_api_from_env():
        sys.exit(1)
        
    model_name = choose_model(preferred=PREFERRED_MODELS)
    if not model_name:
        print("No model supporting 'generateContent' was found.")
        sys.exit(1)
    print(f"Using model: {model_name}")
    
    crime_data_text, used_path = load_crime_data()
    if not crime_data_text:
        print("Error: No crime data file found.")
        sys.exit(1)
    print("Loaded crime data from:", used_path)
    
    article_summary_map = build_article_summary_map(crime_data_text)
    print(f"Loaded {len(article_summary_map)} article summaries from DB.")
    
    prompt_template = make_prompt_template(crime_data_text)
    model = genai.GenerativeModel(model_name)
    
    print("\n--- Legal Bot Server is running! ---")
    print("Open your web browser and go to http://127.0.0.1:5000")

from waitress import serve

if __name__ == "__main__":
    start_app()
    # Use Waitress to serve the app on the same port
    serve(app, host="0.0.0.0", port=5000)