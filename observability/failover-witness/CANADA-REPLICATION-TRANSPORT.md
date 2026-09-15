# Canada replication transport

Canada PostgreSQL currently listens on `127.0.0.1:15432`. The prepared tunnel
forwards Oracle loopback `127.0.0.1:25442` to that Canada endpoint, using the
BotAdmin-managed SSH key. It is intentionally activation-window only; do not
start it until the isolated standby-prep configuration is applied and an
operator has verified the tunnel and replication credentials. The standby
consumes `PRIMARY_PORT=25442`.