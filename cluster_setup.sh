#!/bin/bash

# File: cluster_setup.sh

set -euo pipefail  # Enhanced error handling

# Enable debug logging (optional)
# set -x
CONSUL_HTTP_ADDR="${CONSUL_SCHEME}://${CONSUL_HOST_DOCKER}:${CONSUL_PORT}"

log() {
  echo "[$(date +'%Y-%m-%dT%H:%M:%S%z')] $1"
}

register_with_consul() {
  log "Registering RabbitMQ with Consul..."
  
  SERVICE_ID="${RABBITMQ_NODENAME}-${HOSTNAME}"
  SERVICE_ADDRESS=$(hostname -i)
  RABBITMQ_PORT=5672
  RABBITMQ_MANAGEMENT_PORT=15672
  HEALTH_CHECK_HTTP="http://${SERVICE_ADDRE}:${RABBITMQ_MANAGEMENT_PORT}/api/healthchecks/node"

  SERVICE_DEFINITION=$(cat <<EOF
{
  "ID": "${SERVICE_ID}",
  "Name": "rabbitmq",
  "Address": "${SERVICE_ADDRESS}",
  "Port": ${RABBITMQ_PORT},
  "Tags": ["rabbitmq"],
  "Check": {
    "HTTP": "${HEALTH_CHECK_HTTP}",
    "Interval": "10s",
    "Timeout": "5s",
    "DeregisterCriticalServiceAfter": "1m"
  }
}
EOF
)

  # Attempt to register with Consul, retrying up to 5 times
  for attempt in {1..5}; do
    if curl --silent --fail -X PUT \
        -H "Content-Type: application/json" \
        --data "${SERVICE_DEFINITION}" \
        "${CONSUL_HTTP_ADDR}/v1/agent/service/register"; then
      log "Successfully registered RabbitMQ with Consul."
      return
    else
      log "Consul registration attempt ${attempt} failed. Retrying in 5 seconds..."
      sleep 5
    fi
  done

  log "Consul registration failed after 5 attempts."
  # Decide whether to exit or continue
  # exit 1
}

deregister_with_consul() {
  log "Deregistering RabbitMQ from Consul..."
  
  CONSUL_HTTP_ADDR="${CONSUL_SCHEME}://${CONSUL_HOST_DOCKER}:${CONSUL_PORT}"
  SERVICE_ID="${RABBITMQ_NODENAME}-${HOSTNAME}"

  if curl --silent --fail -X PUT \
      "${CONSUL_HTTP_ADDR}/v1/agent/service/deregister/${SERVICE_ID}"; then
    log "Successfully deregistered RabbitMQ from Consul."
  else
    log "Consul deregistration failed."
  fi
}

# Trap SIGTERM and SIGINT to deregister service on shutdown
trap 'deregister_with_consul; exit 0' SIGTERM SIGINT

# Check required environment variables
: "${CONSUL_SCHEME:?Environment variable CONSUL_SCHEME not set}"
: "${CONSUL_HOST_DOCKER:?Environment variable CONSUL_HOST_DOCKER not set}"
: "${CONSUL_PORT:?Environment variable CONSUL_PORT not set}"
: "${RABBITMQ_NODENAME:?Environment variable RABBITMQ_NODENAME not set}"

# Wait until Consul is up
wait_for_consul() {
  log "Waiting for Consul to be available at ${CONSUL_HTTP_ADDR}/v1/status/leader..."
  until curl --silent --fail "${CONSUL_HTTP_ADDR}/v1/status/leader" > /dev/null; do
    log "Consul is not available. Retrying in 5 seconds..."
    sleep 5
  done
  log "Consul is available."
}

wait_for_consul

# Register RabbitMQ with Consul
register_with_consul


# Cluster nodes
NODES=(rabbitmq1 rabbitmq2 rabbitmq3)

for NODE in "${NODES[@]}"; do
  CURRENT_NODE="$(hostname)"
  if [ "$NODE" != "$CURRENT_NODE" ]; then
    TARGET_NODE="rabbit@${NODE}"
    log "Clustering with ${TARGET_NODE}..."

    # Attempt to stop the RabbitMQ application
    if rabbitmqctl stop_app; then
      log "Successfully stopped RabbitMQ application."
    else
      log "Failed to stop RabbitMQ application. It might not be running."
    fi

    # Attempt to join the cluster
    if rabbitmqctl join_cluster "$TARGET_NODE"; then
      log "Successfully joined the cluster with ${TARGET_NODE}."
    else
      log "Failed to join the cluster with ${TARGET_NODE}."
      # Decide whether to exit or continue
      # exit 1
    fi

    # Start the RabbitMQ application
    if rabbitmqctl start_app; then
      log "Successfully started RabbitMQ application."
    else
      log "Failed to start RabbitMQ application."
      # Decide whether to exit or continue
      # exit 1
    fi
  fi
done

# Start RabbitMQ server in the foreground
log "Starting RabbitMQ server..."
exec rabbitmq-server
