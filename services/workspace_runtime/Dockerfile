ARG BASE_IMAGE=ghcr.io/jmiahman1/sharedllm-base:latest
FROM ${BASE_IMAGE} AS base
ARG GIT_SHA=unknown
ARG BUILD_DATE=unknown
ARG SERVICE_NAME=workspace_runtime
ENV GIT_SHA=${GIT_SHA}
ENV BUILD_DATE=${BUILD_DATE}
ENV SERVICE_NAME=${SERVICE_NAME}
WORKDIR /app
ENV PYTHONPATH=/app
RUN mkdir -p /home/sharedllm && chown 1000:1000 /home/sharedllm
COPY . .
EXPOSE 8007
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Workspace-specific: ALL coding tools agentic AIs use
# fd alias (base image already provides fd-find at /usr/bin/fd-find)
RUN update-alternatives --install /usr/bin/fd fd /usr/bin/fd-find 100 2>/dev/null || true

# Install Node.js for frontend/tooling support (npm packages)
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y nodejs && \
    npm install -g npm@latest

# Install coding-related npm packages that agentic AIs use
RUN npm install -g \
    typescript \
    typescript-language-server \
    vscode-langservers-extracted \
    shfmt \
    shellcheck

# Install Docker CLI for container management
RUN curl -fsSL https://download.docker.com/linux/static/stable/x86_64/docker-27.5.1.tgz | \
    tar xz --strip-components=1 -C /usr/local/bin docker/docker

# Install deno
RUN wget -q https://github.com/denoland/deno/releases/latest/download/deno-x86_64-unknown-linux-gnu.zip -O /tmp/deno.zip && \
    unzip -o /tmp/deno.zip -d /usr/local/bin && \
    chmod +x /usr/local/bin/deno && \
    rm /tmp/deno.zip

# Install python language servers
RUN pip install --no-cache-dir \
    pyright \
    pylint \
    flake8 \
    black \
    isort \
    autopep8 \
    mypy \
    jedi \
    python-lsp-server \
    rope \
    yappi \
    memory-profiler \
    line-profiler

# ===========================================================================
# COMPREHENSIVE WORKSPACE SANDBOX TOOLCHAIN
# The per-workspace sandbox (wsbox) reuses this image, so every tool an
# autonomous agent needs must be present here and usable directly in the shell.
# `gh`/`git`/cloud CLIs authenticate via the GITHUB_TOKEN/GIT_TOKEN (and other
# Identity integration secrets) that the execution layer injects into the
# sandbox environment at command time — never from a .env file.
# ===========================================================================

# --- Google Cloud SDK apt repository ---
RUN curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg | gpg --dearmor -o /usr/share/keyrings/cloud-google.gpg \
 && echo "deb [signed-by=/usr/share/keyrings/cloud-google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" > /etc/apt/sources.list.d/google-cloud-sdk.list

# --- System packages: language runtimes, linters, media/CLI tooling ---
RUN apt-get update && apt-get install -y --no-install-recommends \
    imagemagick \
    ffmpeg \
    pandoc \
    netpbm \
    qrencode \
    jp2a \
    libimage-exiftool-perl \
    clang \
    clang-format \
    clang-tidy \
    cppcheck \
    yamllint \
    shellcheck \
    pdftk \
    default-jdk \
    maven \
    gradle \
    ruby-full \
    google-cloud-sdk \
    && rm -rf /var/lib/apt/lists/*

# --- Python linters / checkers ---
RUN pip install --no-cache-dir \
    ruff \
    bandit \
    img2pdf \
    vulture \
    mccabe

# --- Node-based linters / formatters / language servers ---
RUN npm install -g \
    eslint \
    prettier \
    @typescript-eslint/parser \
    stylelint \
    markdownlint-cli \
    yaml-language-server

# --- GitHub CLI (gh): runs NATIVELY in the sandbox with injected GITHUB_TOKEN ---
# Version pinned (no api.github.com lookup) to avoid GitHub API rate-limit 404s
# in CI image builds.
RUN set -eux; \
    ARCH="$(uname -m)"; \
    if [ "$ARCH" = "x86_64" ]; then GH_ARCH="amd64"; else GH_ARCH="$ARCH"; fi; \
    GH_VERSION="2.96.0"; \
    curl -fsSL "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_${GH_ARCH}.tar.gz" -o /tmp/gh.tar.gz; \
    tar -xzf /tmp/gh.tar.gz -C /tmp; \
    mv "/tmp/gh_${GH_VERSION}_linux_${GH_ARCH}/bin/gh" /usr/local/bin/gh; \
    rm -rf /tmp/gh.tar.gz "/tmp/gh_${GH_VERSION}_linux_${GH_ARCH}"; \
    gh --version

# --- Terraform + OpenTofu (IaC) ---
# Versions pinned (no api.github.com lookup) to avoid GitHub API rate-limit 404s
# in CI image builds.
RUN set -eux; \
    TERRAFORM_VERSION="1.9.8"; \
    curl -fsSL "https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_linux_amd64.zip" -o /tmp/tf.zip; \
    unzip -o /tmp/tf.zip -d /usr/local/bin; \
    rm -f /tmp/tf.zip; \
    TOFU_VERSION="1.9.0"; \
    curl -fsSL "https://github.com/opentofu/tofu/releases/download/v${TOFU_VERSION}/tofu_${TOFU_VERSION}_linux_amd64.zip" -o /tmp/tofu.zip; \
    unzip -o /tmp/tofu.zip -d /usr/local/bin; \
    rm -f /tmp/tofu.zip; \
    terraform version; tofu version

# --- AWS CLI v2 ---
RUN set -eux; \
    curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o /tmp/aws.zip; \
    unzip -o /tmp/aws.zip -d /tmp; \
    /tmp/aws/install --update; \
    rm -rf /tmp/aws /tmp/aws.zip; \
    aws --version

# --- Go (language + linters) ---
RUN set -eux; \
    GO_VERSION="$(curl -fsSL https://go.dev/VERSION?m=text | head -1 | sed 's/^go//')"; \
    curl -fsSL "https://go.dev/dl/go${GO_VERSION}.linux-amd64.tar.gz" -o /tmp/go.tgz; \
    tar -C /usr/local -xzf /tmp/go.tgz; \
    rm -f /tmp/go.tgz; \
    go version
ENV PATH="/usr/local/go/bin:${PATH}"
RUN set -eux; \
    GL_VERSION="2.12.2"; \
    curl -fsSL "https://github.com/golangci/golangci-lint/releases/download/v${GL_VERSION}/golangci-lint-${GL_VERSION}-linux-amd64.tar.gz" -o /tmp/gl.tgz; \
    tar -xzf /tmp/gl.tgz -C /tmp; \
    mv "/tmp/golangci-lint-${GL_VERSION}-linux-amd64/golangci-lint" /usr/local/bin/; \
    rm -rf /tmp/gl.tgz "/tmp/golangci-lint-${GL_VERSION}-linux-amd64"; \
    golangci-lint version

# --- Rust (cargo/rustc/rustfmt/clippy) ---
ENV CARGO_HOME=/usr/local/cargo RUSTUP_HOME=/usr/local/rustup
RUN set -eux; \
    curl -fsSL https://sh.rustup.rs -o /tmp/rustup.sh; \
    sh /tmp/rustup.sh -y --profile minimal --component rustfmt clippy --default-toolchain stable; \
    ln -sf /usr/local/cargo/bin/* /usr/local/bin/; \
    rm -f /tmp/rustup.sh; \
    rustc --version

# --- Terraform linter (tflint) ---
RUN set -eux; \
    TFL_VERSION="0.54.0"; \
    curl -fsSL "https://github.com/terraform-linters/tflint/releases/download/v${TFL_VERSION}/tflint_linux_amd64.zip" -o /tmp/tflint.zip; \
    unzip -o /tmp/tflint.zip -d /usr/local/bin; \
    rm -f /tmp/tflint.zip; \
    tflint --version

# --- Dockerfile linter (hadolint) ---
RUN set -eux; \
    HL_VERSION="2.12.0"; \
    curl -fsSL "https://github.com/hadolint/hadolint/releases/download/v${HL_VERSION}/hadolint-Linux-x86_64" -o /usr/local/bin/hadolint; \
    chmod +x /usr/local/bin/hadolint; \
    hadolint --version

# --- GitHub Actions linting/running (actionlint + act) ---
RUN set -eux; \
    AL_VERSION="1.7.4"; \
    curl -fsSL "https://github.com/rhysd/actionlint/releases/download/v${AL_VERSION}/actionlint_${AL_VERSION}_linux_amd64.tar.gz" -o /tmp/al.tgz; \
    tar -xzf /tmp/al.tgz -C /usr/local/bin; \
    rm -f /tmp/al.tgz; \
    ACT_VERSION="0.2.64"; \
    curl -fsSL "https://github.com/nektos/act/releases/download/v${ACT_VERSION}/act_Linux_x86_64.tar.gz" -o /tmp/act.tgz; \
    tar -xzf /tmp/act.tgz -C /usr/local/bin; \
    rm -f /tmp/act.tgz; \
    actionlint --version; act --version

# --- Git credential helper so NATIVE `git push` over HTTPS authenticates using
#     the GITHUB_TOKEN/GIT_TOKEN injected into the sandbox at exec time. The
#     helper reads the token from the environment, so no credential store is
#     needed. Configured for the sandbox user's HOME (/home/sharedllm). ---
RUN cat > /usr/local/bin/git-credential-github-token <<'EOF'
#!/bin/sh
if [ "$1" = "get" ]; then
  echo protocol=https
  echo host=github.com
  echo username=git
  echo password="${GITHUB_TOKEN:-$GH_TOKEN}"
fi
EOF
RUN chmod +x /usr/local/bin/git-credential-github-token
RUN git config --file /home/sharedllm/.gitconfig credential.helper /usr/local/bin/git-credential-github-token \
 && git config --file /home/sharedllm/.gitconfig --add safe.directory '*' \
 && git config --file /home/sharedllm/.gitconfig init.defaultBranch main \
 && chown 1000:1000 /home/sharedllm/.gitconfig

CMD ["uvicorn", "services.workspace_runtime.main:app", "--host", "0.0.0.0", "--port", "8007"]
