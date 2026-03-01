#!/usr/bin/env bash
# =============================================================================
# yt-transcribe installer
# =============================================================================
# Installs the YouTube transcription tool as 'yt-transcribe' command.
#
# Usage:
#   curl -sSL https://raw.githubusercontent.com/<you>/yt-transcribe/main/setup.sh | bash
#   # or
#   git clone https://github.com/<you>/yt-transcribe && cd yt-transcribe && ./setup.sh
#
# What this does:
#   1. Detects your OS and package manager
#   2. Installs system dependencies (ffmpeg) — asks for sudo if needed
#   3. Installs Python dependencies (faster-whisper, yt-dlp) — no sudo
#   4. Installs 'yt-transcribe' command to ~/.local/bin
#   5. Ensures ~/.local/bin is on your PATH
# =============================================================================

set -euo pipefail

# -- Colors & formatting ------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

info()    { echo -e "${BLUE}ℹ${NC}  $*"; }
success() { echo -e "${GREEN}✅${NC} $*"; }
warn()    { echo -e "${YELLOW}⚠️${NC}  $*"; }
error()   { echo -e "${RED}❌${NC} $*"; }
header()  { echo -e "\n${BOLD}${CYAN}$*${NC}"; }

# -- Sanity checks ------------------------------------------------------------
header "═══════════════════════════════════════════"
header "  yt-transcribe installer"
header "═══════════════════════════════════════════"
echo ""

# Python 3.8+ required
if ! command -v python3 &>/dev/null; then
    error "Python 3 not found. Please install Python 3.8+ first."
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PYTHON_MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
PYTHON_MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)

if [ "$PYTHON_MAJOR" -lt 3 ] || { [ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 8 ]; }; then
    error "Python 3.8+ required, found $PYTHON_VERSION"
    exit 1
fi
success "Python $PYTHON_VERSION"

# pip
if ! python3 -m pip --version &>/dev/null; then
    error "pip not found. Install it with: python3 -m ensurepip --upgrade"
    exit 1
fi
success "pip available"

# -- OS Detection -------------------------------------------------------------
header "Detecting system..."

OS="$(uname -s)"
ARCH="$(uname -m)"
DISTRO="unknown"
PKG_MANAGER="unknown"
WSL=false
SUDO_CMD="sudo"

case "$OS" in
    Linux)
        # WSL check
        if grep -qi microsoft /proc/version 2>/dev/null; then
            WSL=true
        fi

        if [ -f /etc/os-release ]; then
            . /etc/os-release
            case "$ID" in
                ubuntu|debian|pop|mint|elementary|zorin)
                    DISTRO="debian"
                    PKG_MANAGER="apt"
                    ;;
                fedora|rhel|centos|rocky|alma)
                    DISTRO="redhat"
                    if command -v dnf &>/dev/null; then
                        PKG_MANAGER="dnf"
                    else
                        PKG_MANAGER="yum"
                    fi
                    ;;
                arch|manjaro|endeavouros)
                    DISTRO="arch"
                    PKG_MANAGER="pacman"
                    ;;
                opensuse*|suse*)
                    DISTRO="suse"
                    PKG_MANAGER="zypper"
                    ;;
                alpine)
                    DISTRO="alpine"
                    PKG_MANAGER="apk"
                    ;;
                void)
                    DISTRO="void"
                    PKG_MANAGER="xbps-install"
                    ;;
                nixos)
                    DISTRO="nixos"
                    PKG_MANAGER="nix-env"
                    ;;
                *)
                    DISTRO="$ID"
                    ;;
            esac
        fi
        ;;
    Darwin)
        DISTRO="macos"
        if command -v brew &>/dev/null; then
            PKG_MANAGER="brew"
            SUDO_CMD=""  # brew doesn't use sudo
        else
            PKG_MANAGER="none"
        fi
        ;;
    MINGW*|MSYS*|CYGWIN*)
        DISTRO="windows"
        PKG_MANAGER="manual"
        ;;
    *)
        warn "Unknown OS: $OS"
        ;;
esac

WSL_TAG=""
if $WSL; then WSL_TAG=" (WSL)"; fi

info "OS: $OS / $DISTRO$WSL_TAG ($ARCH)"
info "Package manager: $PKG_MANAGER"

# -- Check if running as root (don't do that) ---------------------------------
if [ "$(id -u)" -eq 0 ]; then
    warn "Running as root is not recommended. The script will use sudo when needed."
    SUDO_CMD=""
fi

# -- Install ffmpeg -----------------------------------------------------------
header "Checking ffmpeg..."

if command -v ffmpeg &>/dev/null; then
    FFMPEG_VERSION=$(ffmpeg -version 2>/dev/null | head -1 | awk '{print $3}')
    success "ffmpeg $FFMPEG_VERSION already installed"
else
    info "ffmpeg not found — installing..."

    install_ffmpeg() {
        case "$PKG_MANAGER" in
            apt)
                $SUDO_CMD apt-get update -qq
                $SUDO_CMD apt-get install -y -qq ffmpeg
                ;;
            dnf)
                # Fedora may need RPM Fusion for ffmpeg
                if ! $SUDO_CMD dnf install -y ffmpeg 2>/dev/null; then
                    warn "ffmpeg not in default repos. Trying RPM Fusion..."
                    $SUDO_CMD dnf install -y \
                        "https://mirrors.rpmfusion.org/free/fedora/rpmfusion-free-release-$(rpm -E %fedora).noarch.rpm" \
                        2>/dev/null || true
                    $SUDO_CMD dnf install -y ffmpeg
                fi
                ;;
            yum)
                $SUDO_CMD yum install -y ffmpeg || {
                    error "ffmpeg not available via yum. You may need EPEL + RPM Fusion."
                    error "See: https://rpmfusion.org/Configuration"
                    exit 1
                }
                ;;
            pacman)
                $SUDO_CMD pacman -Sy --noconfirm ffmpeg
                ;;
            zypper)
                $SUDO_CMD zypper install -y ffmpeg
                ;;
            apk)
                $SUDO_CMD apk add ffmpeg
                ;;
            xbps-install)
                $SUDO_CMD xbps-install -y ffmpeg
                ;;
            nix-env)
                nix-env -iA nixpkgs.ffmpeg
                ;;
            brew)
                brew install ffmpeg
                ;;
            *)
                error "Don't know how to install ffmpeg on $DISTRO."
                error "Please install ffmpeg manually and re-run this script."
                exit 1
                ;;
        esac
    }

    if [ -n "$SUDO_CMD" ] && ! sudo -n true 2>/dev/null; then
        info "sudo access needed for ffmpeg installation."
    fi

    install_ffmpeg
    success "ffmpeg installed"
fi

# -- Install Python dependencies ----------------------------------------------
header "Installing Python dependencies..."

# Determine pip install flags
PIP_FLAGS=("--user")

# Check if --break-system-packages is supported (pip 23.0.1+)
if python3 -m pip install --help 2>/dev/null | grep -q "break-system-packages"; then
    PIP_FLAGS+=("--break-system-packages")
fi

# If we're in a virtualenv, don't use --user
if [ -n "${VIRTUAL_ENV:-}" ]; then
    PIP_FLAGS=()
    info "Virtual environment detected: $VIRTUAL_ENV"
fi

PYTHON_DEPS=("faster-whisper" "yt-dlp")

for dep in "${PYTHON_DEPS[@]}"; do
    # Normalize package name for import check
    import_name="${dep//-/_}"

    if python3 -c "import $import_name" 2>/dev/null; then
        success "$dep already installed"
    else
        info "Installing $dep..."
        if python3 -m pip install "${PIP_FLAGS[@]}" "$dep" 2>/dev/null; then
            success "$dep installed"
        else
            # Retry without flags
            warn "Retrying $dep install without flags..."
            python3 -m pip install "$dep" || {
                error "Failed to install $dep"
                exit 1
            }
            success "$dep installed"
        fi
    fi
done

# -- Install yt-transcribe command --------------------------------------------
header "Installing yt-transcribe command..."

INSTALL_DIR="$HOME/.local/bin"
mkdir -p "$INSTALL_DIR"

# Find the transcribe.py script
SCRIPT_SOURCE=""
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -f "$SCRIPT_DIR/transcribe.py" ]; then
    SCRIPT_SOURCE="$SCRIPT_DIR/transcribe.py"
elif [ -f "./transcribe.py" ]; then
    SCRIPT_SOURCE="$(pwd)/transcribe.py"
else
    error "Cannot find transcribe.py!"
    error "Make sure setup.sh and transcribe.py are in the same directory."
    exit 1
fi

# Copy script and create command wrapper
INSTALLED_SCRIPT="$INSTALL_DIR/yt-transcribe-core.py"
cp "$SCRIPT_SOURCE" "$INSTALLED_SCRIPT"
chmod +x "$INSTALLED_SCRIPT"

# Create the wrapper that invokes python3
cat > "$INSTALL_DIR/yt-transcribe" << EOF
#!/usr/bin/env bash
# yt-transcribe — YouTube video transcription tool
# https://github.com/<you>/yt-transcribe
exec python3 "$INSTALLED_SCRIPT" "\$@"
EOF

chmod +x "$INSTALL_DIR/yt-transcribe"

# Common misspelling — be kind to fast typers
ln -sf "$INSTALL_DIR/yt-transcribe" "$INSTALL_DIR/yt-transcibe"

success "Installed to $INSTALL_DIR/yt-transcribe"

# -- Ensure ~/.local/bin is on PATH -------------------------------------------
header "Checking PATH..."

add_to_path_instructions() {
    local shell_name="$1"
    local rc_file="$2"

    if [ -f "$rc_file" ]; then
        if ! grep -q '\.local/bin' "$rc_file" 2>/dev/null; then
            echo '' >> "$rc_file"
            echo '# Added by yt-transcribe installer' >> "$rc_file"
            echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$rc_file"
            success "Added ~/.local/bin to PATH in $rc_file"
            return 0
        fi
    fi
    return 1
}

if echo "$PATH" | tr ':' '\n' | grep -q "$HOME/.local/bin"; then
    success "~/.local/bin is already on PATH"
else
    warn "~/.local/bin is not on your PATH"

    SHELL_NAME="$(basename "${SHELL:-/bin/bash}")"
    UPDATED=false

    case "$SHELL_NAME" in
        bash)
            add_to_path_instructions "bash" "$HOME/.bashrc" && UPDATED=true
            # Also try .bash_profile for login shells
            [ -f "$HOME/.bash_profile" ] && add_to_path_instructions "bash" "$HOME/.bash_profile"
            ;;
        zsh)
            add_to_path_instructions "zsh" "$HOME/.zshrc" && UPDATED=true
            ;;
        fish)
            FISH_CONFIG="$HOME/.config/fish/config.fish"
            if [ -f "$FISH_CONFIG" ] && ! grep -q '.local/bin' "$FISH_CONFIG" 2>/dev/null; then
                echo '' >> "$FISH_CONFIG"
                echo '# Added by yt-transcribe installer' >> "$FISH_CONFIG"
                echo 'fish_add_path $HOME/.local/bin' >> "$FISH_CONFIG"
                success "Added ~/.local/bin to PATH in $FISH_CONFIG"
                UPDATED=true
            fi
            ;;
        *)
            warn "Unknown shell: $SHELL_NAME"
            ;;
    esac

    if ! $UPDATED; then
        warn "Add this to your shell config manually:"
        echo -e "    ${BOLD}export PATH=\"\$HOME/.local/bin:\$PATH\"${NC}"
    fi

    # Apply to current session immediately
    export PATH="$HOME/.local/bin:$PATH"
    success "PATH updated for current session"
fi

# -- GPU info (helpful for users) ---------------------------------------------
header "GPU detection..."

HAS_CUDA=false
if command -v nvidia-smi &>/dev/null; then
    GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)
    GPU_MEM=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader 2>/dev/null | head -1)
    if [ -n "$GPU_NAME" ]; then
        success "NVIDIA GPU: $GPU_NAME ($GPU_MEM)"
        HAS_CUDA=true

        # Check if PyTorch has CUDA
        if python3 -c "import torch; assert torch.cuda.is_available()" 2>/dev/null; then
            success "PyTorch CUDA support: yes"
        else
            warn "PyTorch CUDA support: no"
            info "For GPU acceleration, install PyTorch with CUDA:"
            echo -e "    ${BOLD}pip install torch --index-url https://download.pytorch.org/whl/cu121${NC}"
        fi
    fi
fi

if ! $HAS_CUDA; then
    # Check for Apple Silicon MPS
    if [ "$DISTRO" = "macos" ] && [ "$ARCH" = "arm64" ]; then
        success "Apple Silicon detected — MPS acceleration may be available"
    else
        info "No NVIDIA GPU detected — will use CPU (still works, just slower)"
        info "Recommended models for CPU: tiny, base, small"
    fi
fi

# -- Done! --------------------------------------------------------------------
echo ""
header "═══════════════════════════════════════════"
header "  Installation complete! 🎉"
header "═══════════════════════════════════════════"
echo ""
echo -e "  ${BOLD}Usage:${NC}"
echo -e "    yt-transcribe ${CYAN}https://www.youtube.com/watch?v=VIDEO_ID${NC}"
echo -e "    yt-transcribe ${CYAN}https://youtu.be/VIDEO_ID${NC} --model medium"
echo -e "    yt-transcribe ${CYAN}URL${NC} --model large-v3 --language en"
echo ""
echo -e "  ${BOLD}Models (speed vs quality):${NC}"
echo -e "    tiny   → fastest, decent quality"
echo -e "    base   → fast, good quality"
echo -e "    small  → ${GREEN}balanced (default)${NC}"
echo -e "    medium → slow, excellent quality"
echo -e "    large-v3 → slowest, best quality"
echo ""
echo -e "  ${BOLD}Output:${NC} JSON file in current directory"
echo ""

if ! echo "$PATH" | tr ':' '\n' | grep -q "$HOME/.local/bin"; then
    echo -e "  ${YELLOW}⚠️  PATH update didn't persist. Run:${NC}"
    echo -e "    export PATH=\"\$HOME/.local/bin:\$PATH\""
    echo ""
fi
