{{- define "pp.fullname" -}}
{{- if contains .Chart.Name .Release.Name -}}{{ .Release.Name | trunc 50 | trimSuffix "-" }}
{{- else -}}{{ printf "%s-%s" .Release.Name .Chart.Name | trunc 50 | trimSuffix "-" }}{{- end -}}
{{- end -}}

{{- define "pp.labels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}

{{/* selector labels for one component: include "pp.selector" (list . "api") */}}
{{- define "pp.selector" -}}
{{- $ctx := index . 0 -}}
app.kubernetes.io/name: {{ $ctx.Chart.Name }}
app.kubernetes.io/instance: {{ $ctx.Release.Name }}
app.kubernetes.io/component: {{ index . 1 }}
{{- end -}}

{{- define "pp.image" -}}
{{- $ctx := index . 0 -}}{{- $name := index . 1 -}}
{{- $tag := $ctx.Values.image.tag | default $ctx.Chart.AppVersion -}}
{{- if $ctx.Values.image.registry -}}{{ printf "%s/%s:%s" $ctx.Values.image.registry $name $tag }}
{{- else -}}{{ printf "%s:%s" $name $tag }}{{- end -}}
{{- end -}}

{{- define "pp.secretName" -}}
{{- .Values.existingSecret | default (printf "%s-app" (include "pp.fullname" .)) -}}
{{- end -}}

{{- define "pp.databaseUrl" -}}
{{- if .Values.postgres.enabled -}}
{{- printf "postgresql://portalpilot:%s@%s-postgres:5432/portalpilot" .Values.postgres.password (include "pp.fullname" .) -}}
{{- else -}}{{ required "database.url is required when postgres.enabled=false (or use existingSecret)" .Values.database.url }}{{- end -}}
{{- end -}}

{{- define "pp.temporalAddress" -}}
{{- if .Values.temporal.enabled -}}{{ printf "%s-temporal:7233" (include "pp.fullname" .) }}
{{- else -}}{{ required "temporal.address is required when temporal.enabled=false" .Values.temporal.address }}{{- end -}}
{{- end -}}

{{- define "pp.portalUrl" -}}
{{- if .Values.config.portalUrl -}}{{ .Values.config.portalUrl }}
{{- else if .Values.portal.enabled -}}{{ printf "http://%s-portal:8080" (include "pp.fullname" .) }}
{{- else -}}{{ fail "config.portalUrl is required when portal.enabled=false" }}{{- end -}}
{{- end -}}

{{/* environment shared by api, worker and relay */}}
{{- define "pp.appEnv" -}}
- { name: TEMPORAL_ADDRESS, value: {{ include "pp.temporalAddress" . | quote }} }
- { name: TEMPORAL_NAMESPACE, value: {{ .Values.config.temporalNamespace | quote }} }
- { name: TASK_QUEUE, value: {{ .Values.config.taskQueue | quote }} }
- { name: S3_BUCKET, value: {{ .Values.s3.bucket | quote }} }
- { name: AWS_REGION, value: {{ .Values.s3.region | quote }} }
{{- if .Values.s3.mock.enabled }}
- { name: S3_ENDPOINT, value: {{ printf "http://%s-s3:9090" (include "pp.fullname" .) | quote }} }
- { name: AWS_ACCESS_KEY_ID, value: local }
- { name: AWS_SECRET_ACCESS_KEY, value: local }
{{- else if .Values.s3.endpoint }}
- { name: S3_ENDPOINT, value: {{ .Values.s3.endpoint | quote }} }
{{- end }}
- { name: KEY_PROVIDER, value: {{ .Values.vault.keyProvider | quote }} }
{{- if .Values.vault.kmsKeyId }}
- { name: KMS_KEY_ID, value: {{ .Values.vault.kmsKeyId | quote }} }
{{- end }}
- { name: GROUNDER_CHAIN, value: {{ .Values.config.grounderChain | quote }} }
- { name: QWEN_URL, value: {{ .Values.config.qwenUrl | quote }} }
- { name: PORTAL_URL, value: {{ include "pp.portalUrl" . | quote }} }
- { name: METRICS_PORT, value: "9100" }
{{- range $k := list "DATABASE_URL" "OPERATOR_TOKEN" "LOCAL_MASTER_KEY" }}
- name: {{ $k }}
  valueFrom: { secretKeyRef: { name: {{ include "pp.secretName" $ }}, key: {{ $k }} } }
{{- end }}
- name: ANTHROPIC_API_KEY
  valueFrom: { secretKeyRef: { name: {{ include "pp.secretName" . }}, key: ANTHROPIC_API_KEY, optional: true } }
{{- end -}}

{{- define "pp.scrapeAnnotations" -}}
{{- $ctx := index . 0 -}}
{{- if $ctx.Values.metrics.podAnnotations }}
prometheus.io/scrape: "true"
prometheus.io/port: {{ index . 1 | quote }}
prometheus.io/path: /metrics
{{- end }}
{{- end -}}
