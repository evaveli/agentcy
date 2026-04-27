#!/bin/bash

# File: setup_env.sh

# Function to generate a random string for Erlang cookie
generate_random_string() {
  openssl rand -base64 32
}

# Function to update or append a key-value pair in .env
update_env_var() {
  local key="$1"
  local value="$2"
  local env_file=".env"

  if grep -q "^${key}=" "$env_file"; then
    # If the key exists, update its value
    if [[ "$OSTYPE" == "darwin"* ]]; then
      # macOS sed syntax
      sed -i '' "s|^${key}=.*|${key}=${value}|" "$env_file"
    else
      # GNU sed syntax
      sed -i "s|^${key}=.*|${key}=${value}|" "$env_file"
    fi
  else
    # If the key does not exist, append it
    echo "${key}=${value}" >> "$env_file"
  fi
}

# Function to validate RabbitMQ hosts
validate_hosts() {
  local hosts="$1"
  local IFS=','
  local host
  local valid=true
  local regex='^[a-zA-Z0-9.-]+:[0-9]{1,5}$'

  for host in $hosts; do
    if [[ ! $host =~ $regex ]]; then
      valid=false
      break
    fi
  done

  if $valid; then
    return 0
  else
    return 1
  fi
}

# Prompt for RabbitMQ username
read -p "Enter RabbitMQ default username [default: admin]: " RABBITMQ_DEFAULT_USER
RABBITMQ_DEFAULT_USER=${RABBITMQ_DEFAULT_USER:-admin}

# Prompt for RabbitMQ password
while true; do
  read -s -p "Enter RabbitMQ default password: " RABBITMQ_DEFAULT_PASS
  echo
  read -s -p "Confirm RabbitMQ default password: " RABBITMQ_DEFAULT_PASS_CONFIRM
  echo
  if [ "$RABBITMQ_DEFAULT_PASS" = "$RABBITMQ_DEFAULT_PASS_CONFIRM" ]; then
    break
  else
    echo "Passwords do not match. Please try again."
  fi
done

# Prompt for Erlang cookie or generate one
read -p "Enter Erlang cookie (leave empty to generate one): " RABBITMQ_ERLANG_COOKIE
if [ -z "$RABBITMQ_ERLANG_COOKIE" ]; then
  RABBITMQ_ERLANG_COOKIE=$(generate_random_string)
  echo "Generated Erlang cookie: $RABBITMQ_ERLANG_COOKIE"
fi

# Prompt for RabbitMQ hosts
echo "Enter RabbitMQ hosts (in the format host:port). Separate multiple hosts with commas."
read -p "Example: localhost:5672,localhost:5673,localhost:5674: " RABBITMQ_HOSTS

# Validate RabbitMQ hosts input
if [ -z "$RABBITMQ_HOSTS" ]; then
  echo "No RabbitMQ hosts entered. Please enter at least one host."
  exit 1
fi

if ! validate_hosts "$RABBITMQ_HOSTS"; then
  echo "Invalid format for RabbitMQ hosts. Ensure each host is in the format host:port and separated by commas. Do not use any spaces"
  exit 1
fi

# Check if .env exists
ENV_FILE=".env"
if [ -f "$ENV_FILE" ]; then
  echo ".env file exists. Updating existing variables or appending new ones."
  update_env_var "RABBITMQ_DEFAULT_USER" "$RABBITMQ_DEFAULT_USER"
  update_env_var "RABBITMQ_DEFAULT_PASS" "$RABBITMQ_DEFAULT_PASS"
  update_env_var "RABBITMQ_ERLANG_COOKIE" "$RABBITMQ_ERLANG_COOKIE"
  update_env_var "RABBITMQ_HOSTS" "$RABBITMQ_HOSTS"
else
  # Write to .env file
  cat > "$ENV_FILE" <<EOF
RABBITMQ_DEFAULT_USER=${RABBITMQ_DEFAULT_USER}
RABBITMQ_DEFAULT_PASS=${RABBITMQ_DEFAULT_PASS}
RABBITMQ_ERLANG_COOKIE=${RABBITMQ_ERLANG_COOKIE}
RABBITMQ_HOSTS=${RABBITMQ_HOSTS}
EOF
fi

# Set file permissions to restrict access
chmod 600 "$ENV_FILE"

# Display confirmation to the user
echo ".env file has been successfully updated with the following variables:"
echo "RABBITMQ_DEFAULT_USER=${RABBITMQ_DEFAULT_USER}"
echo "RABBITMQ_ERLANG_COOKIE=${RABBITMQ_ERLANG_COOKIE}"
echo "RABBITMQ_HOSTS=${RABBITMQ_HOSTS}"
# Note: For security reasons, do not echo the password
