{{/* Name helpers */}}
{{- define "traceiq.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "traceiq.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "traceiq.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
app.kubernetes.io/name: {{ include "traceiq.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ include "traceiq.imageTag" . | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{/* Selector labels for one component: include "traceiq.selectorLabels" (dict "root" . "component" "backend") */}}
{{- define "traceiq.selectorLabels" -}}
app.kubernetes.io/name: {{ include "traceiq.name" .root }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{- define "traceiq.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "traceiq.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{- define "traceiq.imageTag" -}}
{{- default .Chart.AppVersion .Values.image.tag -}}
{{- end -}}

{{- define "traceiq.image" -}}
{{- printf "%s/traceiq-%s:%s" .root.Values.image.registry .name (include "traceiq.imageTag" .root) -}}
{{- end -}}

{{- define "traceiq.secretName" -}}
{{- default (printf "%s-secrets" (include "traceiq.fullname" .)) .Values.secrets.existingSecret -}}
{{- end -}}

{{/* Hostnames of the data services, in-cluster or external. */}}
{{- define "traceiq.postgresHost" -}}
{{- if .Values.postgresql.enabled -}}{{ include "traceiq.fullname" . }}-postgresql{{- else -}}{{ required "postgresql.external.host is required when postgresql.enabled=false" .Values.postgresql.external.host }}{{- end -}}
{{- end -}}
{{- define "traceiq.postgresPort" -}}
{{- if .Values.postgresql.enabled -}}5432{{- else -}}{{ .Values.postgresql.external.port }}{{- end -}}
{{- end -}}
{{- define "traceiq.redisHost" -}}
{{- if .Values.redis.enabled -}}{{ include "traceiq.fullname" . }}-redis{{- else -}}{{ required "redis.external.host is required when redis.enabled=false" .Values.redis.external.host }}{{- end -}}
{{- end -}}
{{- define "traceiq.redisPort" -}}
{{- if .Values.redis.enabled -}}6379{{- else -}}{{ .Values.redis.external.port }}{{- end -}}
{{- end -}}
{{- define "traceiq.redisScheme" -}}
{{- if and (not .Values.redis.enabled) .Values.redis.external.tls -}}rediss{{- else -}}redis{{- end -}}
{{- end -}}
{{- define "traceiq.minioEndpoint" -}}
{{- if .Values.minio.enabled -}}{{ include "traceiq.fullname" . }}-minio:9000{{- else -}}{{ required "minio.external.endpoint is required when minio.enabled=false" .Values.minio.external.endpoint }}{{- end -}}
{{- end -}}
{{- define "traceiq.minioUseSSL" -}}
{{- if .Values.minio.enabled -}}false{{- else -}}{{ .Values.minio.external.useSSL }}{{- end -}}
{{- end -}}

{{/* One secret-backed env var. */}}
{{- define "traceiq.secretEnv" -}}
- name: {{ .name }}
  valueFrom:
    secretKeyRef:
      name: {{ .secret }}
      key: {{ .key }}
      {{- if .optional }}
      optional: true
      {{- end }}
{{- end -}}

{{/*
Environment shared by the API and every Celery process. Connection URLs are
assembled with $(VAR) so the passwords stay in the Secret and never appear in
the ConfigMap or the pod spec.
*/}}
{{- define "traceiq.backendEnv" -}}
{{- $secret := include "traceiq.secretName" . -}}
{{ include "traceiq.secretEnv" (dict "name" "POSTGRES_PASSWORD" "secret" $secret "key" "POSTGRES_PASSWORD") }}
{{ include "traceiq.secretEnv" (dict "name" "REDIS_PASSWORD" "secret" $secret "key" "REDIS_PASSWORD") }}
{{ include "traceiq.secretEnv" (dict "name" "SECRET_KEY" "secret" $secret "key" "SECRET_KEY") }}
{{ include "traceiq.secretEnv" (dict "name" "WEBHOOK_SECRET" "secret" $secret "key" "WEBHOOK_SECRET") }}
{{ include "traceiq.secretEnv" (dict "name" "MINIO_ACCESS_KEY" "secret" $secret "key" "MINIO_ROOT_USER") }}
{{ include "traceiq.secretEnv" (dict "name" "MINIO_SECRET_KEY" "secret" $secret "key" "MINIO_ROOT_PASSWORD") }}
{{ include "traceiq.secretEnv" (dict "name" "SECRETS_KEY" "secret" $secret "key" "SECRETS_KEY" "optional" true) }}
{{ include "traceiq.secretEnv" (dict "name" "SECRETS_KEY_PREVIOUS" "secret" $secret "key" "SECRETS_KEY_PREVIOUS" "optional" true) }}
{{ include "traceiq.secretEnv" (dict "name" "METRICS_TOKEN" "secret" $secret "key" "METRICS_TOKEN" "optional" true) }}
{{ include "traceiq.secretEnv" (dict "name" "SENTRY_DSN" "secret" $secret "key" "SENTRY_DSN" "optional" true) }}
{{ include "traceiq.secretEnv" (dict "name" "OTEL_EXPORTER_OTLP_HEADERS" "secret" $secret "key" "OTEL_EXPORTER_OTLP_HEADERS" "optional" true) }}
{{ include "traceiq.secretEnv" (dict "name" "ADMIN_PASSWORD" "secret" $secret "key" "ADMIN_PASSWORD" "optional" true) }}
- name: DATABASE_URL
  value: {{ printf "postgresql+asyncpg://%s:$(POSTGRES_PASSWORD)@%s:%s/%s%s" .Values.postgresql.external.user (include "traceiq.postgresHost" .) (include "traceiq.postgresPort" .) .Values.postgresql.external.database (ternary "" (printf "?sslmode=%s" .Values.postgresql.external.sslmode) .Values.postgresql.enabled) | quote }}
- name: CELERY_BROKER_URL
  value: {{ printf "%s://:$(REDIS_PASSWORD)@%s:%s/0" (include "traceiq.redisScheme" .) (include "traceiq.redisHost" .) (include "traceiq.redisPort" .) | quote }}
- name: CELERY_RESULT_BACKEND
  value: {{ printf "%s://:$(REDIS_PASSWORD)@%s:%s/0" (include "traceiq.redisScheme" .) (include "traceiq.redisHost" .) (include "traceiq.redisPort" .) | quote }}
{{- if .Values.celeryBeat.redbeat }}
- name: CELERY_BEAT_SCHEDULER
  value: redbeat.RedBeatScheduler
- name: REDBEAT_LOCK_TIMEOUT
  value: {{ .Values.celeryBeat.redbeatLockTimeout | quote }}
{{- end }}
{{- end -}}
