# The game server, and the sandbox it runs submitted code in.
#
# Two things here are not decoration. `bubblewrap` has to be installed *in the
# image*: the judge looks for `bwrap` inside this filesystem, and one on the
# host is not visible from in here. And the server runs as an unprivileged
# user, because that user is who a submitted solution runs as if the sandbox
# ever fails to start.
#
# Build and run:
#
#     podman build -t termination -f Containerfile .
#     podman run --rm -p 8888:8888 termination
#
# See the README section printed by `podman run --rm termination --help` for
# the flags, and the notes at the bottom of this file if the sandbox self-test
# fails on startup.

FROM registry.fedoraproject.org/fedora:42

# python3 for the server; bubblewrap is the sandbox the judge runs solutions
# in. Without it the server still starts, warns loudly, and lets a submission
# read the answers out of server/problems.py.
RUN dnf install -y --setopt=install_weak_deps=False \
        python3 \
        python3-pip \
        bubblewrap \
    && dnf clean all \
    && rm -rf /var/cache/dnf

RUN pip install --no-cache-dir uv

WORKDIR /app

# Dependencies before source, so editing the game does not re-resolve them.
# `--frozen` builds exactly what uv.lock pins rather than re-solving, which is
# what makes two builds of the same commit the same image.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Only what the server needs. The client is not copied: it would be dead
# weight, and every file in here is a file a submission could read if the
# sandbox ever failed open.
COPY server.py ./
COPY protocol/ ./protocol/
COPY server/ ./server/

# Untrusted code runs as whoever this process is. Root in a container is still
# root over everything in it, including the problem bank and the game state.
RUN useradd --create-home --uid 10001 termination \
    && chown -R termination:termination /app
USER termination

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8888

# 0.0.0.0 because the only thing that can reach this is the port the container
# publishes; binding loopback inside a container publishes nothing.
ENTRYPOINT ["python", "server.py", "--host", "0.0.0.0"]

# If startup logs "the bwrap sandbox is installed but did not run", the kernel
# is refusing nested user namespaces to a rootless container. In order of
# preference:
#
#   1. podman run --security-opt seccomp=unconfined ...
#      Lets the container call unshare(). Usually enough.
#   2. sudo sysctl -w user.max_user_namespaces=15000    (on the host)
#      When the host has namespaces disabled outright.
#   3. Run the container with --userns=host.
#
# Do not reach for --privileged. It removes the isolation this file exists to
# provide, and the sandbox would then be the only thing left between a
# submitted solution and the host.
