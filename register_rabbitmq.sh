#!/bin/bash

set -e

CONSUL_HOST=${CONSUL_HOST:-"http://consul:8500"}
SERVICE_NAME=${CONSUL_SERVICE_NAME:-"rabbitmq"}
SERVICE_TAGS=${CONSUL_SERVICE_TAGS:-"rabbitmq,amqp"}
SERVICE_PORT=${RABBITMQ_PORT:-5672}
MANAGEMENT_PORT=${RABBITMQ_MANAGEMENT_PORT:-15672}

SERVICE_ID="${SERVICE_NAME}-$(hostname)"

SERVICE_ADDRESS=$(hostname -i)


read -r -d '' HEALTH_CHECK <<EOF
{
  "HTTP": "http://${SERVICE_ADDRESS}:${MANAGEMENT_PORT}/api/healthchecks",
  "Interval": "10s",
  "Timeout": "5s",
  "DeregisterCriticalServiceAfter": "1m"
}
EOF