#!/bin/bash
echo "🚀 Iniciando Sensores Prensa..."

# Matar instancias previas
pkill -f "python3 app.py" 2>/dev/null
pkill -f "cloudflared" 2>/dev/null
sleep 1

# Directorio del script
DIR="$(cd "$(dirname "$0")" && pwd)"

# Iniciar Flask
cd "$DIR"
python3 app.py &
FLASK_PID=$!
echo "✅ Flask PID: $FLASK_PID"
sleep 2

# Iniciar tunnel
cloudflared tunnel --config "$DIR/cloudflared-config.yml" run &
TUNNEL_PID=$!
echo "✅ Tunnel PID: $TUNNEL_PID"

echo ""
echo "🌐 URL: https://sensores.celtavia.cl"
echo "🏠 Local: http://localhost:5050"
echo ""
echo "Para detener: ./stop.sh"

# Guardar PIDs
echo "$FLASK_PID" > /tmp/sensores_flask.pid
echo "$TUNNEL_PID" > /tmp/sensores_tunnel.pid

wait
