#!/bin/sh
set -e
OS=http://opensearch:9200

for name in sre-logs sre-traces; do
  curl -fsS -X PUT "$OS/_index_template/${name}-template" \
    -H 'Content-Type: application/json' -d "{
      \"index_patterns\": [\"${name}*\"],
      \"template\": {
        \"settings\": {
          \"number_of_shards\": 1,
          \"number_of_replicas\": 0,
          \"refresh_interval\": \"5s\"
        }
      }
    }" > /dev/null
  echo "index template ready: ${name}"
done
