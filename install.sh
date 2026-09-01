#!/usr/bin/env bash
set -e

# TraderAgentReinforcedGym - Simplified Installation Script
# This script provides a one-command installation for users

echo "======================================"
echo "TraderAgentReinforcedGym Installation"
echo "======================================"
echo ""

# Color codes for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Check Python version
echo -e "${BLUE}[1/6] Checking Python version...${NC}"
PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
REQUIRED_VERSION="3.10"
if python3 -c "import sys; exit(0 if sys.version_info >= (3, 10) else 1)"; then
    echo -e "${GREEN}✓ Python $PYTHON_VERSION found${NC}"
else
    echo -e "${RED}✗ Python $REQUIRED_VERSION+ required. Found: $PYTHON_VERSION${NC}"
    exit 1
fi

# Install Poetry if not present
echo -e "${BLUE}[2/6] Checking Poetry installation...${NC}"
if ! command -v poetry &> /dev/null; then
    echo -e "${YELLOW}Poetry not found. Installing Poetry...${NC}"
    curl -sSL https://install.python-poetry.org | python3 -
    export PATH="$HOME/.local/bin:$PATH"
    echo -e "${GREEN}✓ Poetry installed${NC}"
else
    echo -e "${GREEN}✓ Poetry already installed${NC}"
fi

# Configure Poetry to use local virtualenv
echo -e "${BLUE}[3/6] Configuring Poetry...${NC}"
poetry config virtualenvs.in-project true
echo -e "${GREEN}✓ Poetry configured${NC}"

# Install Python dependencies
echo -e "${BLUE}[4/6] Installing Python dependencies (this may take a few minutes)...${NC}"
poetry install
echo -e "${GREEN}✓ Dependencies installed${NC}"

# Create necessary directories
echo -e "${BLUE}[5/6] Creating data directories...${NC}"
mkdir -p data/{raw,lake,features}
mkdir -p logs
mkdir -p /tmp/{logs,checkpoints,tensorboard}
echo -e "${GREEN}✓ Directories created${NC}"

# Setup environment file
echo -e "${BLUE}[6/6] Setting up environment file...${NC}"
if [ ! -f .env ]; then
    cp .env.example .env 2>/dev/null || echo "# TraderAgentReinforcedGym Environment Variables" > .env
    echo -e "${YELLOW}⚠ Created .env file. Please configure your API keys before running.${NC}"
fi
echo -e "${GREEN}✓ Environment file ready${NC}"

echo ""
echo -e "${GREEN}======================================"
echo "Installation Complete! ✓"
echo "======================================"
echo -e "${NC}"
echo "Next steps:"
echo "  1. Configure your API keys in the .env file"
echo "  2. Run tests: poetry run pytest"
echo "  3. Start training: poetry run tg-train --config config/app.yaml"
echo "  4. View GUI: ./scripts/start-gui.sh"
echo ""
echo "For more information, see: docs/01_Project_Overview.md"
echo ""
