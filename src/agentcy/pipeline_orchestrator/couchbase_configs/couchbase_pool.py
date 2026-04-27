#src/agentcy/pipeline_orchestrator/couchbase_configs/couchbase_pool.py

import threading
import time
import asyncio
from contextlib import contextmanager
import logging
from agentcy.pipeline_orchestrator.couchbase_configs.couchbase_connection_manager import CouchbaseConnectionManager


logger = logging.getLogger(__name__)

class DynamicCouchbaseConnectionPool:

    def __init__(self, min_size: int = 1, max_size: int = 10, idle_timeout: float = 60.0, ephemeral: bool = False):

        """
        Create a dynamic connection pool for Couchbase connections.

        :param min_size: Minimum number of connections to keep in the pool.
        :param max_size: Maximum number of connections allowed.
        :param idle_timeout: Time (in seconds) after which an idle connection may be closed
                             if the pool size is above min_size.
        """

        self.min_size = min_size
        self.max_size = max_size
        self.idle_timeout = idle_timeout
        self.ephemeral = ephemeral
        # Synchronization primitives.
        self.lock = threading.Lock()
        self.condition = threading.Condition(self.lock)

        # List of tuples: (connection, last_used_timestamp)
        self.idle_connections = []
        self.total_connections = 0
        self._shutdown = False

        # Eagerly create the minimum number of connections.
        for i in range(self.min_size):
            try:
                conn = self._create_connection()
                self.idle_connections.append((conn, time.time()))
                self.total_connections += 1
                logger.info(f"Initialized connection {i+1}/{self.min_size}")
            except Exception as e:
                logger.error(f"Failed to initialize connection {i+1}: {e}")
                raise

        # Start a background thread to clean up idle connections.
        self._cleanup_thread = threading.Thread(target=self._cleanup_idle_connections, daemon=True)
        self._cleanup_thread.start()

    def _create_connection(self):
        """
        Creates a new Couchbase connection using your provided CouchbaseConnectionManager.
        """
        return CouchbaseConnectionManager(ephemeral=self.ephemeral)

    def get_connection(self, timeout: float = 10.0):
        """
        Acquire a connection from the pool.
        - If an idle connection exists, return it.
        - If not and the pool hasn't reached max_size, create a new connection.
        - Otherwise, wait for a connection to be returned until timeout.

        :param timeout: Maximum time to wait for a connection.
        :return: A Couchbase connection.
        :raises Exception: If no connection is available before timeout.
        """
        with self.condition:
            deadline = time.time() + timeout
            while True:
                if self.idle_connections:
                    conn, _ = self.idle_connections.pop(0)
                    logger.info("Acquired connection from pool.")
                    return conn
                if self.total_connections < self.max_size:
                    try:
                        conn = self._create_connection()
                        self.total_connections += 1
                        logger.info("Created new connection. Total connections: %d", self.total_connections)
                        return conn
                    except Exception as e:
                        logger.error("Error creating new connection: %s", e)
                        raise
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise Exception("Timeout waiting for an available connection.")
                self.condition.wait(remaining)

    def return_connection(self, connection):
        """
        Return a connection to the pool and notify waiting threads.
        """
        with self.condition:
            self.idle_connections.append((connection, time.time()))
            logger.info("Returned connection to pool.")
            self.condition.notify()

    @contextmanager
    def connection(self, timeout: float = 10.0):
        """
        Context manager for automatically acquiring and returning a connection.

        Usage:
            with pool.connection() as conn:
                # use conn
        """
        conn = self.get_connection(timeout=timeout)
        try:
            yield conn
        finally:
            self.return_connection(conn)

    def _cleanup_idle_connections(self):
        """
        Background task that periodically cleans up idle connections.
        Closes connections that have been idle longer than idle_timeout, but ensures that
        the total number of connections does not fall below min_size.
        """
        while not self._shutdown:
            with self.condition:
                now = time.time()
                new_idle = []
                for conn, last_used in self.idle_connections:
                    # If the connection has been idle for too long and we have extra connections...
                    if (now - last_used) > self.idle_timeout and self.total_connections > self.min_size:
                        try:
                            # Close the connection. Handle async close if applicable.
                            if asyncio.iscoroutinefunction(conn.close):
                                asyncio.run(conn.close())
                            else:
                                conn.close()
                            self.total_connections -= 1
                            logger.info("Closed an idle connection. Total connections: %d", self.total_connections)
                        except Exception as e:
                            logger.error("Error closing idle connection: %s", e)
                            new_idle.append((conn, last_used))
                    else:
                        new_idle.append((conn, last_used))
                self.idle_connections = new_idle
            time.sleep(1)  # Adjust the sleep time as needed.

    def close_all(self):
        """
        Shutdown the pool and gracefully close all idle connections.
        """
        self._shutdown = True
        with self.condition:
            self.condition.notify_all()
        self._cleanup_thread.join(timeout=5)
        with self.condition:
            for conn, _ in self.idle_connections:
                try:
                    if asyncio.iscoroutinefunction(conn.close):
                        asyncio.run(conn.close())
                    else:
                        conn.close()
                    logger.info("Closed connection from pool.")
                except Exception as e:
                    logger.error("Error closing connection: %s", e)
            self.idle_connections.clear()
        logger.info("Connection pool shutdown complete.")
