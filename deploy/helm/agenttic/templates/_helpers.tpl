{{- define "agenttic.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "agenttic.fullname" -}}
{{- printf "%s-%s" .Release.Name (include "agenttic.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "agenttic.labels" -}}
app.kubernetes.io/name: {{ include "agenttic.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}

{{- define "agenttic.selectorLabels" -}}
app.kubernetes.io/name: {{ include "agenttic.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "agenttic.secretName" -}}
{{- if .Values.secrets.existingSecret -}}
{{ .Values.secrets.existingSecret }}
{{- else -}}
{{ include "agenttic.fullname" . }}
{{- end -}}
{{- end -}}

{{- define "agenttic.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{ default (include "agenttic.fullname" .) .Values.serviceAccount.name }}
{{- else -}}
{{ default "default" .Values.serviceAccount.name }}
{{- end -}}
{{- end -}}
