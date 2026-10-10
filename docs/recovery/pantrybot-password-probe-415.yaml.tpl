# Replace @STAGE@ with temp or final before creating this one-shot Job.
# Its psql command reads PG* environment variables from a staged Secret;
# credentials do not appear in the command line or Job logs.
apiVersion: batch/v1
kind: Job
metadata:
  name: pantry-db-probe-@STAGE@-415
  namespace: pantry-bot
spec:
  backoffLimit: 0
  activeDeadlineSeconds: 120
  ttlSecondsAfterFinished: 3600
  template:
    spec:
      automountServiceAccountToken: false
      restartPolicy: Never
      securityContext:
        runAsUser: 70
        runAsGroup: 70
        runAsNonRoot: true
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: probe
          image: postgres:16-alpine
          command: ["/bin/sh", "-ec"]
          args:
            - |
              psql -AtX -v ON_ERROR_STOP=1 -c \
                "SELECT current_user, current_database(), inet_client_addr(), pg_is_in_recovery()"
          envFrom:
            - secretRef:
                name: pantry-bot-db-url-@STAGE@-415
          resources:
            requests:
              cpu: 10m
              memory: 32Mi
            limits:
              memory: 128Mi
          securityContext:
            allowPrivilegeEscalation: false
            capabilities:
              drop: ["ALL"]
