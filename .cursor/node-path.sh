# Shared helper: put a modern Node in front of the base image's default node.
#
# `npm test` runs `node --test` directly over the `.ts` test files and relies
# on TypeScript type stripping, which is on by default only in Node >= 22.18
# (CI pins `node-version: '22'`, i.e. the latest 22.x). The base image already
# ships such a Node via nvm, but an older `/exec-daemon/node` sits ahead of it
# on PATH (and that directory is not writable), so the bare `node` command
# resolves to the old one. Prepend the newest installed nvm Node 22 so every
# node/npm command in this environment uses a version that can strip types.
#
# Sourced by install and the frontend terminal; safe to source more than once.
_node_bin="$(ls -d "$HOME"/.nvm/versions/node/v22.*/bin 2>/dev/null | sort -V | tail -n1 || true)"
if [ -n "${_node_bin:-}" ]; then
  PATH="${_node_bin}:${PATH}"
  export PATH
fi
unset _node_bin
