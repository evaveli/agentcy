import consul
import os
from functools import lru_cache

@lru_cache()
def get_consul_client():
    consul_host = os.getenv("CONSUL_HOST", "localhost") 
    print(consul_host)
    consul_port = int(os.getenv("CONSUL_PORT", 8500))
    print(consul_port)
    consul_scheme = os.getenv("CONSUL_SCHEME", "http")
    print(consul_scheme)
    consul_token = os.getenv("CONSUL_TOKEN", None)

    return consul.Consul(
        host=consul_host,
        port=consul_port,
        scheme=consul_scheme,
        token=consul_token,
    )