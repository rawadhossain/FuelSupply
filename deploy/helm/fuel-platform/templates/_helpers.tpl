{{- define "fuel-platform.fullname" -}}
{{- .Release.Name -}}
{{- end -}}

{{- define "fuel-platform.labels" -}}
app.kubernetes.io/part-of: fuel-platform
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "fuel-platform.svcLabels" -}}
app: {{ .name }}
{{ include "fuel-platform.labels" .ctx }}
{{- end -}}

{{- define "fuel-platform.secretName" -}}
{{- if .Values.secrets.existingSecret -}}
{{ .Values.secrets.existingSecret }}
{{- else -}}
{{ .Release.Name }}-secrets
{{- end -}}
{{- end -}}
