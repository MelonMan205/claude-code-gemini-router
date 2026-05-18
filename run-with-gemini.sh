#!/bin/bash

# Script to run Claude Code with Gemini router and model selection

set -e

# Define available models
MODELS=(
  "gemini-3-pro"
  "gemini-3-flash"
  "gemini-2.5-pro"
  "gemini-2.5-flash"
  "gemini-2.5-flash-lite"
  "gemini-2.0-flash"
  "gemini-2.0-flash-thinking-exp"
  "gemini-1.5-pro"
  "gemini-1.5-flash"
)

echo "🚀 Starting Claude Code with Gemini router..."
echo ""

# Determine model
if [ -n "$1" ]; then
  MODEL="$1"
  echo "Using model: $MODEL"
else
  # Show menu
  echo "Available Gemini models:"
  echo ""
  for i in "${!MODELS[@]}"; do
    printf "  %d) %s\n" "$((i + 1))" "${MODELS[$i]}"
  done
  echo ""

  read -p "Select model (1-${#MODELS[@]}) [default: 1]: " choice

  if [ -z "$choice" ]; then
    choice=1
  fi

  if ! [[ "$choice" =~ ^[0-9]+$ ]] || [ "$choice" -lt 1 ] || [ "$choice" -gt "${#MODELS[@]}" ]; then
    echo "Invalid selection. Using default: ${MODELS[0]}"
    MODEL="${MODELS[0]}"
  else
    MODEL="${MODELS[$((choice - 1))]}"
  fi
fi

# Use router.vseek.app by default
BASE_URL="${ANTHROPIC_BASE_URL:-https://router.vseek.app}"

# Get API key from environment or .env file or prompt
if [ -z "$ANTHROPIC_API_KEY" ]; then
  if [ -f .env ] && grep -q "PROXY_API_KEY=" .env; then
    ANTHROPIC_API_KEY=$(grep "PROXY_API_KEY=" .env | cut -d'=' -f2)
  else
    read -sp "Enter ANTHROPIC_API_KEY (or press Enter for 'dummy'): " api_key_input
    echo ""
    ANTHROPIC_API_KEY="${api_key_input:-dummy}"
  fi
fi

echo ""
echo "Configuration:"
echo "  Model: $MODEL"
echo "  Base URL: $BASE_URL"
echo "  API Key: ${ANTHROPIC_API_KEY:0:10}${ANTHROPIC_API_KEY:10:+...}"
echo ""

# Set environment and launch Claude Code
export ANTHROPIC_BASE_URL="$BASE_URL"
export ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY"

exec claude --model "$MODEL" "$@"
