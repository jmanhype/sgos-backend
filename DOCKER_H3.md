# Running the H3 Pipeline (SSH → 3090) inside Docker

`lib/h3_pipeline.py` + `routers/h3.py` submit Wan2GP (H3) jobs to a remote 3090
GPU server over SSH/SCP. In the container those binaries come from
`openssh-client`, and access to your key is via a read-only bind mount of
`~/.ssh`.

## Service wiring

- `docker-compose.yml` defines the `sgos-backend` service (part of the VBL stack):
  - port `127.0.0.1:8420:8420`
  - `~/.ssh:/root/.ssh:ro`
  - env: `H3_GPU_HOST`, `H3_GPU_USER`, `H3_REMOTE_HOME` (and optional `SGOS_API_KEY`)
  - healthcheck: `curl -sf http://localhost:8420/health`

## SSH from inside Docker — what you must get right

1. **Key file permissions.** `openssh` refuses keys that are world-readable
   (`Permissions 0644 are too open`). On the HOST make sure the private key is
   `600` and `~/.ssh` is `700`:
   ```bash
   chmod 700 ~/.ssh && chmod 600 ~/.ssh/id_ed25519
   ```
   Inside the container it is mounted read-only, so the permissions stay as the
   host set them; OpenSSH checks them from the mount.

2. **known_hosts.** `lib/h3_pipeline.py` SSHes with `BatchMode=yes` and does NOT
   disable host-key checking, so the 3090's host key must already be in
   `~/.ssh/known_hosts` on the HOST (it is mounted into the container):
   ```bash
   ssh-keyscan -H 192.168.1.143 >> ~/.ssh/known_hosts
   ```
   Since the mount is read-only, add the key on the host, not inside the image.

3. **Connecting to the 3090.** These jobs do NOT start a container attach to the
   GPU box; they SSH/SCP *out* from the container to the LAN host at
   `192.168.1.143:22`. Docker's default bridge has outbound connectivity to the
   LAN, so this works without `network_mode: host` **as long as** the 3090's sshd
   is reachable from the docker bridge (no host-firewall blocking, and the 3090
   is on the same LAN/subnet the bridge can reach).

4. **Key type + authorized_keys.** The mounted key must be the one in
   `straughter@192.168.1.143:~/.ssh/authorized_keys`. Test on the host first:
   ```bash
   ssh -o BatchMode=yes straughter@192.168.1.143 'echo ok'
   ```
   If that works on the host but not in the container, the usual culprits are
   key permissions (see #1) or known_hosts (see #2).

5. **Read-only mount → no key writes.** Because `~/.ssh` is mounted `:ro`, the
   container cannot write keys or update `known_hosts` at runtime. Everything the
   ssh client needs must exist on the host mount before the container starts.

6. **Env wiring.** `H3_GPU_HOST` / `H3_GPU_USER` / `H3_REMOTE_HOME` are read from
   the environment by `lib/h3_pipeline.py` at import time. Set them in the compose
   `environment:` block; the defaults match the standard box
   (192.168.1.143 / straughter / /home/straughter/Wan2GP).

## Known limits / notes

- The container runs the API server; the H3 work runs remotely on the 3090, so
  the container itself needs no GPU.
- If the 3090 is unreachable from the Docker bridge but is reachable from the
  host, run the service with `network_mode: host` instead (Linux only) — not the
  default and not shown in the compose file.
- `openssh-client` is added to the image specifically for `ssh`/`scp`.
