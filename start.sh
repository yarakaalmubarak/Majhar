#!/bin/bash
cd "$(dirname "$0")" || exit 1

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is not installed. Install it from https://www.python.org/downloads/ and try again."
  exit 1
fi

if [ -z "$GROQ_API_KEY" ] && [ -z "$GEMINI_API_KEY" ] && [ -z "$OPENAI_API_KEY" ] && [ -z "$ANTHROPIC_API_KEY" ]; then
  echo ""
  echo "Paste your Groq API key (only the key, it starts with gsk_) and press Enter."
  echo "(Nothing appears on screen while you paste. That is normal: the key is hidden.)"
  read -rs GROQ_API_KEY
  GROQ_API_KEY="${GROQ_API_KEY//\"/}"
  GROQ_API_KEY="$(echo "$GROQ_API_KEY" | tr -d '[:space:]')"
  export GROQ_API_KEY
  echo ""
fi

echo "Checking the AI connection (this can take up to a minute)..."
OUT="$(python3 check_ai.py 2>&1)"
echo "$OUT" | tail -3
if echo "$OUT" | grep -q "CERTIFICATE_VERIFY_FAILED"; then
  echo ""
  echo "Your Mac's Python is missing its security certificates. Run this once, then start again:"
  echo '    open /Applications/Python\ 3*/Install\ Certificates.command'
  exit 1
fi
if ! echo "$OUT" | grep -q "^WORKS"; then
  echo ""
  echo "The AI did not answer, so Majhar was not started (a test now would only measure the backup)."
  echo "Most often the key was mistyped: copy it again from console.groq.com and run  bash start.sh  again."
  echo "In a NEW Terminal window it will ask for the key again. Paste only the key (it starts with gsk_)."
  echo "To start without the AI anyway:  python3 app.py --rules"
  exit 1
fi

echo "Starting Majhar. The website opens in your browser in a moment."
echo "Keep this window open while you use it. To stop: press Ctrl + C."
echo ""
python3 app.py
