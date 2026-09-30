#!/bin/bash
# ═══════════════════════════════════════════════════════════
#  Crypto Signal Bot — Quick Start Script
# ═══════════════════════════════════════════════════════════

set -e

echo "═══════════════════════════════════════════════════"
echo "  CRYPTO SIGNAL BOT — Setup & Run"
echo "═══════════════════════════════════════════════════"

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Python3 nie jest zainstalowany!"
    echo "   sudo apt install python3 python3-pip"
    exit 1
fi

# Install dependencies
echo "📦 Instaluję zależności..."
pip3 install --break-system-packages -r requirements.txt 2>/dev/null || pip3 install -r requirements.txt

# Check for .env or --webhook argument
if [ -f .env ]; then
    source .env
    echo "✅ Załadowano .env"
fi

# Run
WEBHOOK="${DISCORD_WEBHOOK_URL:-$1}"

if [ -z "$WEBHOOK" ]; then
    echo ""
    echo "⚠️  Nie podano webhook URL!"
    echo ""
    echo "Użycie:"
    echo "  ./start.sh https://discord.com/api/webhooks/YOUR/WEBHOOK"
    echo ""
    echo "  ALBO ustaw DISCORD_WEBHOOK_URL w pliku .env:"
    echo "  cp .env.example .env && nano .env"
    echo ""
    echo "Test bez Discord:"
    echo "  python3 bot.py --test --scan"
    exit 1
fi

echo "🚀 Uruchamiam bota..."
echo "   Webhook: ${WEBHOOK:0:50}..."
echo ""

python3 bot.py --webhook "$WEBHOOK" "$@"
