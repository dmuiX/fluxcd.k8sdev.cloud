#!/bin/bash
# extract-and-validate-values.sh
# Extract values from a HelmRelease and validate them against the chart

HELMRELEASE_FILE=$1

if [ -z "$HELMRELEASE_FILE" ]; then
  echo "Usage: $0 <helmrelease.yaml>"
  exit 1
fi

# Extract chart name and version
CHART=$(yq eval '.spec.chart.spec.chart' "$HELMRELEASE_FILE")
VERSION=$(yq eval '.spec.chart.spec.version' "$HELMRELEASE_FILE")

echo "Chart: $CHART, Version: $VERSION"

# Extract values to a temporary file
yq eval '.spec.values' "$HELMRELEASE_FILE" > /tmp/hr-values.yaml

echo "Extracted values:"
cat /tmp/hr-values.yaml

# Validate by rendering the chart with extracted values
helm template test-release "$CHART" \
  --version "$VERSION" \
  --values /tmp/hr-values.yaml \
  --dry-run=server --debug 2>&1

if [ $? -eq 0 ]; then
  echo "Values validation passed."
else
  echo "Values validation FAILED."
  exit 1
fi