#!/usr/bin/env bash
# Run as root on the GCP witness VM: PUBKEY="ssh-ed25519 ... pantry-canada-witness-20260922" bash witness-canada-user.sh
# Dedicated local user for Canada's direct witness tunnel (#191): key-only, local forward to 127.0.0.1:8765 only.
set -eu
U=pantry-witness-canada
id $U >/dev/null 2>&1 || useradd --system --create-home --home-dir /var/lib/$U --shell /usr/sbin/nologin $U
passwd -l $U >/dev/null
install -d -m 700 -o $U -g $U /var/lib/$U/.ssh
printf '%s\n' "restrict,port-forwarding,permitopen=\"127.0.0.1:8765\",command=\"/bin/false\" ${PUBKEY:?Canada witness-ssh id_ed25519.pub}" > /var/lib/$U/.ssh/authorized_keys
chown $U: /var/lib/$U/.ssh/authorized_keys; chmod 600 /var/lib/$U/.ssh/authorized_keys
cat > /etc/ssh/sshd_config.d/60-pantry-witness-canada.conf <<'CONF'
# Canada -> witness direct path (#191): key-only, local forward to the witness port only.
Match User pantry-witness-canada
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    AllowTcpForwarding local
    PermitOpen 127.0.0.1:8765
    PermitListen none
    X11Forwarding no
    AllowAgentForwarding no
    PermitTTY no
    ClientAliveInterval 15
    ClientAliveCountMax 3
CONF
sshd -t
echo "canada: $(sshd -T -C user=pantry-witness-canada,host=x,addr=100.104.83.28 | grep -E '^(allowtcpforwarding|permitopen|permittty|passwordauthentication) ' | tr '\n' ' ')"
echo "owner:  $(sshd -T -C user=chasepdrsn_gmail_com,host=x,addr=100.84.89.87 | grep -E '^(allowtcpforwarding|permitopen|permittty) ' | tr '\n' ' ')"
