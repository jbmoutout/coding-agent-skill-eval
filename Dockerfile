# Dev container for running coding agents on a target codebase (SKILL-ON arm).
#
# Build with the codebase under evaluation mounted into /app at the pinned
# commit. Pair with Dockerfile.noskills (the skill-off arm) so the only
# difference between the two images is whether the skill is globally installed
# - this makes the ablation an env-level property, not a prompt property.
#
# Usage (paired ablation):
#     docker build -t <task>-eval-app          -f Dockerfile          .
#     docker build -t <task>-eval-app-noskills -f Dockerfile.noskills .

FROM node:24-bookworm

# System deps generally useful for coding agents
RUN apt-get update && apt-get install -y \
    git \
    curl \
    jq \
    && rm -rf /var/lib/apt/lists/*

# Install opencode (the agent harness)
RUN curl -fsSL https://opencode.ai/install | bash
ENV PATH="/root/.opencode/bin:$PATH"

# Install the skill globally so opencode (and any other agent harness)
# discovers it through its `skills` tool. THIS is the line that's removed
# in Dockerfile.noskills - the only intentional diff between the two images.
RUN npx -y skills@latest add mattpocock/skills --all --global

WORKDIR /app

# --- CUSTOMIZE for your target codebase below this line ---
#
# At minimum you need to COPY the codebase in (pinned to the eval commit),
# install its dependencies, and configure any per-task system packages.
#
# Example (Next.js / Prisma):
#   RUN apt-get update && apt-get install -y postgresql-client \
#       && rm -rf /var/lib/apt/lists/*
#   COPY package.json package-lock.json ./
#   COPY prisma ./prisma/
#   RUN npm ci
#   COPY . .
#   RUN npx prisma generate
#
# Keep both Dockerfile and Dockerfile.noskills in sync on this section.

CMD ["bash"]
