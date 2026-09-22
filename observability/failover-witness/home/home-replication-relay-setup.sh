#!/usr/bin/env bash
# Run on Home (minecraftmachine) as root: PUBKEY=... bash home-replication-relay-setup.sh
# Canada->Home replication relay (#191), mirrors Oracle's pantry-replication-relay
# but restricted to one reverse-forward listener.
set -eu
PUBKEY="${PUBKEY:?set PUBKEY to Canada replication-ssh\id_ed25519.pub}"
id pantry-replication-relay >/dev/null 2>&1 || useradd --system --create-home --home-dir /var/lib/pantry-replication-relay --shell /usr/sbin/nologin pantry-replication-relay
passwd -l pantry-replication-relay >/dev/null
install -d -m 700 -o pantry-replication-relay -g pantry-replication-relay /var/lib/pantry-replication-relay/.ssh
printf '%s\n' "restrict,port-forwarding,permitlisten=\"127.0.0.1:25442\",command=\"/bin/false\" $PUBKEY" > /var/lib/pantry-replication-relay/.ssh/authorized_keys
chown pantry-replication-relay: /var/lib/pantry-replication-relay/.ssh/authorized_keys
chmod 600 /var/lib/pantry-replication-relay/.ssh/authorized_keys
cat > /etc/ssh/sshd_config.d/60-pantry-replication-relay.conf <<'CONF'
# PantryBot Canada->Home replication relay (#191): key-only, reverse forward
# to 127.0.0.1:25442 only, no shell/pty/agent/X11/local forwards.
Match User pantry-replication-relay
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    AllowTcpForwarding remote
    PermitListen 127.0.0.1:25442
    PermitOpen none
    X11Forwarding no
    AllowAgentForwarding no
    PermitTTY no
    ClientAliveInterval 15
    ClientAliveCountMax 3
CONF
sshd -t
echo "relay: $(sshd -T -C user=pantry-replication-relay,host=x,addr=100.104.83.28 | grep -E '^(passwordauthentication|allowtcpforwarding|permitlisten|permitopen|permittty) ' | tr '\n' ' ')"
echo "chase: $(sshd -T -C user=chase,host=x,addr=100.104.83.28 | grep -E '^(passwordauthentication|allowtcpforwarding|permitlisten|permittty) ' | tr '\n' ' ')"
