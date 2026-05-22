#!/bin/bash
pkill -f "python3 app.py" 2>/dev/null
pkill -f "cloudflared" 2>/dev/null
echo "⛔ Sensores Prensa detenido."
